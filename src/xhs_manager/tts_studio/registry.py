"""TTS 模型注册表 —— 统一管理加载状态与生成调用。

三类模型的加载语义不同，这里如实区分而不是假装一致：
  - edge:     无模型，HTTP 调用，always ready
  - voxcpm2:  C++ CLI，每次调用自行加载（~8s），无常驻收益
  - indextts2: Python 类，加载 54s，常驻 worker 进程后每次省掉这段
"""

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ModelSpec:
    id: str
    name: str
    kind: str                      # stateless | cli | worker
    supports_voice: bool = False   # 预置音色
    supports_ref: bool = False     # 参考音色克隆
    supports_emotion: bool = False
    voices: list[str] = field(default_factory=list)
    emotions: list[str] = field(default_factory=list)
    note: str = ""


EDGE_VOICES = [
    "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural",
    "zh-CN-YunjianNeural", "zh-CN-YunxiNeural",
    "zh-CN-YunxiaNeural", "zh-CN-YunyangNeural",
    "zh-CN-liaoning-XiaobeiNeural", "zh-CN-shaanxi-XiaoniNeural",
    "zh-HK-HiuGaaiNeural", "zh-HK-HiuMaanNeural", "zh-HK-WanLungNeural",
    "zh-TW-HsiaoChenNeural", "zh-TW-HsiaoYuNeural", "zh-TW-YunJheNeural",
]

INDEXTTS_EMOTIONS = [
    "happy", "angry", "sad", "afraid",
    "disgusted", "melancholic", "surprised", "calm",
]

SPECS: dict[str, ModelSpec] = {
    "edge": ModelSpec(
        id="edge", name="Edge TTS", kind="stateless",
        supports_voice=True, voices=EDGE_VOICES,
        note="微软 Edge 朗读服务，免费无需 key。14 个固定音色，不支持情感控制。",
    ),
    "voxcpm2": ModelSpec(
        id="voxcpm2", name="VoxCPM2", kind="cli",
        supports_ref=True, supports_emotion=True,
        note="括号内自然语言指令控制音色情绪，如「(语气轻快的女声)」。48kHz 输出，Apache-2.0。",
    ),
    "indextts2": ModelSpec(
        id="indextts2", name="IndexTTS-2 (MLX)", kind="worker",
        supports_ref=True, supports_emotion=True, emotions=INDEXTTS_EMOTIONS,
        note="必须提供参考音频。8 维情感可加权混合。加载约 54s，常驻后免重复加载。",
    ),
}


class ModelState:
    def __init__(self, spec: ModelSpec):
        self.spec = spec
        self.status = "unloaded"      # unloaded | loading | ready | error
        self.error = ""
        self.load_elapsed: Optional[float] = None
        self.proc: Optional[subprocess.Popen] = None
        self.stderr_path = None
        self.lock = threading.Lock()

    def to_dict(self) -> dict[str, Any]:
        s = self.spec
        return {
            "id": s.id, "name": s.name, "kind": s.kind,
            "status": self.status, "error": self.error,
            "load_elapsed": self.load_elapsed,
            "supports_voice": s.supports_voice,
            "supports_ref": s.supports_ref,
            "supports_emotion": s.supports_emotion,
            "voices": s.voices, "emotions": s.emotions, "note": s.note,
            # stateless/cli 没有常驻进程，UI 上要说清楚"加载"对它们是可用性检查
            "resident": s.kind == "worker",
        }


class Registry:
    def __init__(self, settings):
        self.settings = settings
        self.states: dict[str, ModelState] = {
            k: ModelState(v) for k, v in SPECS.items()
        }

    def list(self) -> list[dict[str, Any]]:
        return [s.to_dict() for s in self.states.values()]

    def get(self, model_id: str) -> ModelState:
        st = self.states.get(model_id)
        if not st:
            raise KeyError(f"未知模型 {model_id}")
        return st

    # ── 加载 / 释放 ────────────────────────────────────────────

    def load(self, model_id: str) -> dict[str, Any]:
        st = self.get(model_id)
        with st.lock:
            if st.status == "ready":
                return st.to_dict()
            st.status = "loading"
            st.error = ""
            t0 = time.time()
            try:
                if st.spec.kind == "stateless":
                    self._check_edge()
                elif st.spec.kind == "cli":
                    self._check_voxcpm()
                elif st.spec.kind == "worker":
                    self._start_indextts(st)
                st.status = "ready"
                st.load_elapsed = getattr(st, "worker_load_elapsed", None) or round(time.time() - t0, 2)
            except Exception as e:
                st.status = "error"
                st.error = str(e)[:400]
                logger.exception("加载 %s 失败", model_id)
            return st.to_dict()

    def unload(self, model_id: str) -> dict[str, Any]:
        st = self.get(model_id)
        with st.lock:
            if st.proc:
                try:
                    self._rpc(st, {"cmd": "unload"}, timeout=30)
                    st.proc.stdin.close()
                    st.proc.terminate()
                    st.proc.wait(timeout=10)
                except Exception:
                    try:
                        st.proc.kill()
                    except Exception:
                        pass
                st.proc = None
            st.status = "unloaded"
            st.load_elapsed = None
            return st.to_dict()

    def _check_edge(self):
        import edge_tts  # noqa: F401

    def _check_voxcpm(self):
        s = self.settings
        for p in (s.voxcpm_cli, s.voxcpm_base, s.voxcpm_acoustic):
            if not Path(p).exists():
                raise FileNotFoundError(f"缺少 {p}")

    def _start_indextts(self, st: ModelState):
        import os
        s = self.settings
        py = s.indextts_python
        if not Path(py).exists():
            raise FileNotFoundError(f"找不到 {py}")
        worker = Path(__file__).with_name("worker_indextts.py")
        env = dict(os.environ)
        env["INDEXTTS_REPO"] = s.indextts_repo
        env["INDEXTTS_MODEL"] = s.indextts_model
        env["HF_HUB_OFFLINE"] = "1"
        # -u 强制无缓冲：子进程 stdout 默认块缓冲，行缓冲在管道下不生效，
        # 会导致 readline 一直读不到内容而误判为"空响应"。
        # stderr 单独收集，出错时才有线索可查。
        st.stderr_path = Path("/tmp") / f"indextts_worker_{id(st)}.err"
        errf = open(st.stderr_path, "w")
        st.proc = subprocess.Popen(
            [py, "-u", str(worker)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errf,
            text=True, bufsize=0 if False else 1, cwd=s.indextts_repo, env=env,
        )
        ping = self._rpc(st, {"cmd": "ping"}, timeout=120)
        if not ping.get("ok"):
            raise RuntimeError(self._worker_err(st, ping))
        r = self._rpc(st, {"cmd": "load"}, timeout=900)
        if not r.get("ok"):
            raise RuntimeError(self._worker_err(st, r))
        # 用 worker 上报的真实加载耗时，父进程计时会漏掉 import 开销
        st.worker_load_elapsed = r.get("elapsed")

    def _worker_err(self, st: ModelState, resp: dict) -> str:
        """worker 返回异常时，带上 stderr 尾部，否则只有一句 JSON 解析错误没法排查。"""
        msg = resp.get("error", "worker 无响应")
        tail = ""
        p = getattr(st, "stderr_path", None)
        if p and Path(p).exists():
            tail = Path(p).read_text(errors="ignore")[-400:].strip()
        return f"{msg}" + (f" | stderr: {tail}" if tail else "")

    def _rpc(self, st: ModelState, req: dict, timeout: int = 600) -> dict:
        """与 worker 进程通信。stdout 一行一个 JSON。"""
        if not st.proc or st.proc.poll() is not None:
            raise RuntimeError("worker 进程未运行")
        st.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        st.proc.stdin.flush()

        result: dict = {}
        done = threading.Event()

        def reader():
            nonlocal result
            try:
                # 跳过非 JSON 行：第三方库可能仍往 stdout 打东西，
                # 协议只认能解析成 JSON 对象的那一行。
                for _ in range(2000):
                    line = st.proc.stdout.readline()
                    if not line:
                        result = {"ok": False, "error": "worker 已退出"}
                        break
                    line = line.strip()
                    if not line or not line.startswith("{"):
                        continue
                    try:
                        result = json.loads(line)
                        break
                    except json.JSONDecodeError:
                        continue
                else:
                    result = {"ok": False, "error": "未收到协议响应"}
            except Exception as e:
                result = {"ok": False, "error": str(e)}
            finally:
                done.set()

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        if not done.wait(timeout):
            raise TimeoutError(f"worker 超时 ({timeout}s)")
        return result
