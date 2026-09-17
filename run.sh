#!/usr/bin/env bash
#
# 一条命令把整套服务拉起来。
#
#   ./run.sh              主服务 + worker
#   ./run.sh api          只起主服务（API + 控制台 + 配音）
#   ./run.sh worker       只起 worker
#   ./run.sh --no-migrate 跳过数据库迁移
#
# 两个进程都在前台跑，输出带 [api] / [worker] 前缀。
# Ctrl-C 一次全停。
#
# 为什么必须优雅退出：配音的重模型（IndexTTS-2、OmniVoice）是主服务的
# 子进程，主服务的 shutdown 钩子负责卸载它们。被 kill -9 打断的话，
# 会留下几个 GB 的孤儿进程，而且下次启动还会再起一份。
# 所以这里一律发 TERM，并且等它们真的走完。

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PY=".venv/bin/python"
HOST="${XHS_HOST:-127.0.0.1}"
PORT="${XHS_PORT:-8000}"

WHAT="both"
MIGRATE=1
for arg in "$@"; do
  case "$arg" in
    api|worker|both) WHAT="$arg" ;;
    --no-migrate)    MIGRATE=0 ;;
    -h|--help)       sed -n '3,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "不认识的参数: ${arg} —— 可用: api | worker | both | --no-migrate" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then
  C_API=$'\033[38;5;75m'; C_WRK=$'\033[38;5;114m'
  C_DIM=$'\033[2m'; C_ERR=$'\033[38;5;203m'; C_OFF=$'\033[0m'
else
  C_API=""; C_WRK=""; C_DIM=""; C_ERR=""; C_OFF=""
fi

note() { printf '%s· %s%s\n' "$C_DIM" "$1" "$C_OFF"; }
die()  { printf '%s✗ %s%s\n' "$C_ERR" "$1" "$C_OFF" >&2; exit 1; }

[ -x "$PY" ] || die "找不到 $PY —— 先建虚拟环境并安装依赖（uv sync）"

# 端口被占的话早点说清楚。这个坑踩过：一个没退干净的 uvicorn 孤儿
# 会让新起的服务静默失败，而你对着旧代码调半天。
if [ "$WHAT" != "worker" ]; then
  holder="$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null | head -1)"
  if [ -n "$holder" ]; then
    die "端口 $PORT 被 PID $holder 占着（$(ps -p "$holder" -o comm= 2>/dev/null)）
  先停掉它：kill $holder
  或者换个端口：XHS_PORT=8001 ./run.sh"
  fi
fi

if [ "$MIGRATE" = 1 ]; then
  note "数据库迁移到最新"
  "$PY" -m alembic upgrade head >/dev/null 2>&1 || die "alembic upgrade 失败，单独跑一次看报什么：$PY -m alembic upgrade head"
fi

PIDS=()
NAMES=()
stopping=0

# 每条输出都带上来源。两个进程共用一个终端，不标出处就没法看。
# fflush 是必须的：不刷新的话 awk 会攒着输出，你在终端里什么也看不到。
start() {
  local name="$1" color="$2"; shift 2
  "$@" > >(awk -v p="$color[$name]$C_OFF " '{print p $0; fflush()}') 2>&1 &
  PIDS+=($!)
  NAMES+=("$name")
}

shutdown() {
  [ "$stopping" = 1 ] && return
  stopping=1
  printf '\n'
  note "正在停止（配音模型要卸载，稍等）"
  for pid in "${PIDS[@]}"; do
    kill -TERM "$pid" 2>/dev/null
  done
  # 给足时间走完 shutdown 钩子；实在不走再硬来
  for _ in $(seq 1 100); do
    local alive=0
    for pid in "${PIDS[@]}"; do
      kill -0 "$pid" 2>/dev/null && alive=1
    done
    [ "$alive" = 0 ] && break
    sleep 0.2
  done
  for pid in "${PIDS[@]}"; do
    kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
  done
  note "已停止"
}
trap shutdown INT TERM

case "$WHAT" in
  api)    note "主服务  http://$HOST:$PORT/console" ;;
  worker) note "worker  从队列领视频阶段来跑" ;;
  both)   note "主服务  http://$HOST:$PORT/console"
          note "worker  从队列领视频阶段来跑" ;;
esac
note "Ctrl-C 停止"
printf '\n'

if [ "$WHAT" = "api" ] || [ "$WHAT" = "both" ]; then
  start api "$C_API" "$PY" -m uvicorn xhs_manager.api:app --host "$HOST" --port "$PORT"
fi
if [ "$WHAT" = "worker" ] || [ "$WHAT" = "both" ]; then
  start worker "$C_WRK" "$PY" -m xhs_manager.video_pipeline.cli worker
fi

# 任何一个挂了就把另一个也收掉。一个进程悄悄死掉、另一个继续跑，
# 比两个都停更难发现。
# 用轮询而不是 wait -n：macOS 自带的 bash 是 3.2，没有 wait -n。
while [ "$stopping" = 0 ]; do
  for i in "${!PIDS[@]}"; do
    if ! kill -0 "${PIDS[$i]}" 2>/dev/null; then
      wait "${PIDS[$i]}" 2>/dev/null
      code=$?
      printf '%s✗ %s 退出了（code %s）%s\n' "$C_ERR" "${NAMES[$i]}" "$code" "$C_OFF" >&2
      shutdown
      exit "$code"
    fi
  done
  sleep 0.5
done
