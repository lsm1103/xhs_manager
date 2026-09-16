"""工具体检：把散落各处的探测函数并联成一页。

这一页几乎不写新逻辑——每个检查项背后都是已有的 available() / doctor / detect_*，
它们原本各自散在 CLI、采集器、渲染器、TTS 注册表里，出问题时要挨个手查。

两条约束决定了实现方式：
  探测慢    opencli doctor 要 1-3 秒，平台后端链要发网络请求。
            所以全部并发跑，并且带 TTL 缓存——连点刷新不该把外部服务打一遍。
  探测会炸  任何一项抛异常都不能让整页 500。每个探测器都自己兜住异常，
            兜不住的由 run_all 兜。
"""

from __future__ import annotations

import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

# 严重度即排序：要你动手的排最上面。这是工具页存在的唯一意义。
SEVERITY = {"down": 0, "degraded": 1, "unknown": 2, "ok": 3}

_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
CACHE_TTL_S = 20.0


@dataclass
class Probe:
    key: str
    name: str
    status: str = "unknown"      # ok | degraded | down | unknown
    detail: str = ""
    fix: str = ""                # 怎么修，尽量给可直接粘贴的命令
    facts: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0


def _timed(job: tuple[str, str, Callable[[], Probe]]) -> Probe:
    """跑一个检查项并计时。探测器自己炸掉时也要报出是哪一项。"""
    key, name, fn = job
    t0 = time.monotonic()
    try:
        probe = fn()
    except Exception as e:                       # 单项炸掉不该影响整页
        probe = Probe(key=key, name=name, status="unknown",
                      detail=f"探测本身失败：{type(e).__name__}: {e}"[:200],
                      fix="这是控制台自己的 bug，不是工具坏了")
    probe.elapsed_ms = int((time.monotonic() - t0) * 1000)
    return probe


# ── 各检查项 ────────────────────────────────────────────────


def probe_opencli() -> Probe:
    """opencli 浏览器扩展。小红书/抖音/X 的站内通道全靠它。"""
    try:
        r = subprocess.run(["opencli", "doctor"], capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        return Probe("opencli", "OpenCLI 扩展", "down", "未安装 opencli",
                     fix="npm install -g @jackwener/opencli")
    except subprocess.TimeoutExpired:
        return Probe("opencli", "OpenCLI 扩展", "unknown", "opencli doctor 超时（30s）")

    out = r.stdout or ""
    connected = "Extension: connected" in out or "[OK] Extension" in out
    version = ""
    for line in out.splitlines():
        if "Extension:" in line and "connected" in line:
            version = line.split("connected", 1)[1].strip(" ()")
            break
    if connected:
        return Probe("opencli", "OpenCLI 扩展", "ok",
                     f"已连接 {version}".strip(), facts={"version": version})
    return Probe("opencli", "OpenCLI 扩展", "down", "扩展未连接，站内通道会降级到站外索引",
                 fix="打开 chrome://extensions 启用 OpenCLI，再 opencli daemon restart")


def probe_collect_backends(settings) -> Probe:
    """采集后端链。每个平台自己是一条链，这里统计有多少条跑在首选后端上。"""
    from xhs_manager.video_pipeline.integrations.collectors import probe_platform

    platforms = list(settings.trend_platforms)

    def one(p: str) -> tuple[str, str]:
        try:
            chain = probe_platform(p)
        except Exception:
            return p, "unknown"
        if not chain:
            return p, "unknown"
        first_ok = next((i for i, (_, ok) in enumerate(chain) if ok), None)
        if first_ok is None:
            return p, "down"
        return p, "ok" if first_ok == 0 else "degraded"

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = dict(pool.map(one, platforms))

    n_ok = sum(1 for v in results.values() if v == "ok")
    n_deg = sum(1 for v in results.values() if v == "degraded")
    n_down = sum(1 for v in results.values() if v == "down")

    status = "down" if n_down and not (n_ok or n_deg) else "degraded" if (n_deg or n_down) else "ok"
    fix = ""
    if n_deg or n_down:
        fix = ("降级的平台走的是站外索引（只有标题和链接）。"
               "site-login <平台> 扫码，或配 ZHIHU_COOKIE / WEIBO_COOKIE 可拉回站内。")
    return Probe("collect", "采集后端链", status,
                 f"{len(platforms)} 平台 · 站内 {n_ok} / 降级 {n_deg} / 不可用 {n_down}",
                 fix=fix, facts={"platforms": results})


def probe_tts_providers(settings) -> Probe:
    from xhs_manager.video_pipeline.tts import registry

    rows = []
    for provider in registry.build_providers(settings):
        try:
            ok, why = provider.available()
        except Exception as e:
            ok, why = False, f"{type(e).__name__}: {e}"
        rows.append({"name": provider.name, "ok": ok, "why": why})

    usable = [r for r in rows if r["ok"]]
    preferred = rows[0] if rows else None
    speed = registry.speed_from_settings(settings)

    if not usable:
        return Probe("tts", "TTS 提供方", "down", "没有可用的配音引擎",
                     fix="pip install edge-tts", facts={"chain": rows})
    status = "ok" if preferred and preferred["ok"] else "degraded"
    detail = " → ".join(f"{'✓' if r['ok'] else '✗'} {r['name']}" for r in rows)
    return Probe("tts", "TTS 提供方", status,
                 f"{detail} · 语速 {int((speed - 1) * 100):+d}%",
                 fix=("" if status == "ok"
                      else f"首选 {preferred['name']} 不可用，已回落到 {usable[0]['name']}"),
                 facts={"chain": rows, "speed": speed})


def probe_tts_studio(settings) -> Probe:
    """TTS Studio 是独立进程（模型常驻），这里只聚合它的状态，不代管它的生命周期。"""
    import httpx

    url = getattr(settings, "tts_studio_url", "http://127.0.0.1:8420").rstrip("/")
    try:
        r = httpx.get(f"{url}/api/models", timeout=2.0)
        models = r.json()
    except Exception as e:
        return Probe("tts_studio", "TTS Studio", "down",
                     f"{url} 连接不上（{type(e).__name__}）· 已回落 edge-tts",
                     fix="python -m xhs_manager.tts_studio.app",
                     facts={"url": url})

    if isinstance(models, dict):
        models = models.get("models", [])
    loaded = [m.get("id") or m.get("name") for m in models if m.get("loaded")] if models else []
    return Probe("tts_studio", "TTS Studio", "ok",
                 f"{len(models)} 个模型 · 已加载 {len(loaded) or 0}",
                 facts={"url": url, "models": models, "loaded": loaded})


def probe_moneyprinter(settings) -> Probe:
    from xhs_manager.video_pipeline.integrations.moneyprinter import MoneyPrinterTurbo

    mpt = MoneyPrinterTurbo(settings.moneyprinter_path)
    if not mpt.available:
        return Probe("mpt", "MoneyPrinterTurbo", "down",
                     f"找不到 cli.py：{settings.moneyprinter_path}",
                     fix="检查 XHS_VIDEO_MONEYPRINTER_PATH")

    config = Path(settings.moneyprinter_path) / "config.toml"
    has_key = False
    if config.exists():
        try:
            text = config.read_text(encoding="utf-8", errors="ignore")
            has_key = 'pexels_api_keys = [""]' not in text and "pexels_api_keys" in text
        except OSError:
            pass
    if not has_key:
        return Probe("mpt", "MoneyPrinterTurbo", "degraded", "已安装，但没读到 Pexels key",
                     fix="在 MoneyPrinterTurbo/config.toml 里填 pexels_api_keys")
    return Probe("mpt", "MoneyPrinterTurbo", "ok", "已安装 · Pexels key 已配置")


def probe_ffmpeg() -> Probe:
    from xhs_manager.video_pipeline.integrations.renderer import detect_ffmpeg

    path = detect_ffmpeg()
    if not path:
        return Probe("ffmpeg", "ffmpeg", "down", "找不到支持 libx264 的 ffmpeg",
                     fix="brew install ffmpeg")
    return Probe("ffmpeg", "ffmpeg", "ok", f"{path} · libx264 已验证", facts={"path": path})


def probe_renderer(settings) -> Probe:
    from xhs_manager.video_pipeline.integrations.renderer import HtmlVideoRenderer

    width, _, height = settings.render_resolution.partition("x")
    r = HtmlVideoRenderer(width=int(width), height=int(height or 1920), fps=settings.render_fps)
    if r.available():
        return Probe("renderer", "渲染器", "ok",
                     f"Playwright + Chrome 就绪 · {settings.render_resolution}"
                     f" @{settings.render_fps}fps")
    return Probe("renderer", "渲染器", "down", "Playwright 或 Chrome 不可用",
                 fix="pip install playwright && 确认 Chrome 已安装")


def probe_xhs_login(settings) -> Probe:
    """只检查 profile 在不在——logged_in() 要真开一次浏览器，太慢，不适合放体检页。"""
    from xhs_manager.video_pipeline.integrations.xhs_publisher import (
        DEFAULT_PROFILE_DIR,
        XhsPublisher,
    )

    profile = Path(settings.xhs_profile_dir) if settings.xhs_profile_dir else DEFAULT_PROFILE_DIR
    pub = XhsPublisher(profile_dir=profile, cdp_url=settings.xhs_cdp_url)
    ok, why = pub.available()
    if not ok:
        return Probe("xhs", "小红书登录态", "down", why[:160],
                     fix="python -m xhs_manager.video_pipeline.cli xhs-login")
    return Probe("xhs", "小红书登录态", "ok",
                 f"profile 就绪 · 发布模式 {settings.xhs_publish_mode}",
                 facts={"profile": str(profile)})


def probe_portrait_guard() -> Probe:
    from xhs_manager.video_pipeline import portrait_guard

    ok, why = portrait_guard.available()
    if ok:
        return Probe("portrait", "人脸检测模型", "ok", "肖像权过滤已启用")
    return Probe("portrait", "人脸检测模型", "down",
                 f"{why[:160]} · 素材不会做人脸过滤",
                 fix="首次使用时会自动下载模型，也可手动放到 data/models/")


def probe_bgm(settings) -> Probe:
    dirs = [Path(d) for d in settings.bgm_dirs]
    files: list[Path] = []
    for d in dirs:
        if d.is_dir():
            files += [p for p in d.rglob("*")
                      if p.suffix.lower() in {".mp3", ".m4a", ".wav", ".flac"}]
    if not settings.bgm_enabled:
        return Probe("bgm", "BGM 曲库", "ok", f"已关闭配乐 · 曲库 {len(files)} 首")
    if not files:
        return Probe("bgm", "BGM 曲库", "degraded", "曲库为空，成片将没有背景音乐",
                     fix=f"把音频放进 {', '.join(str(d) for d in dirs)}")

    index = Path(settings.bgm_index_path)
    newest = max((p.stat().st_mtime for p in files), default=0)
    stale = index.exists() and newest > index.stat().st_mtime
    return Probe("bgm", "BGM 曲库", "ok",
                 f"{len(files)} 首" + ("（索引比曲库旧，下次运行会重建）" if stale else ""),
                 facts={"tracks": len(files)})


def probe_disk(settings) -> Probe:
    root = Path(settings.output_base_dir)
    root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(root)
    free_gb = usage.free / 1024 ** 3
    # 一支 2 分半的片子，中间帧能占到 8 GB——低于 5 GB 就该提醒了
    if free_gb < 2:
        status, fix = "down", "清理 data/video_pipeline/ 下已完成运行的中间帧"
    elif free_gb < 5:
        status, fix = "degraded", "渲染中间帧可能占用数 GB，建议清理旧运行"
    else:
        status, fix = "ok", ""
    return Probe("disk", "磁盘空间", status, f"剩余 {free_gb:.1f} GB", fix=fix,
                 facts={"free_gb": round(free_gb, 1)})


def probe_queue(session) -> Probe:
    """工作队列。视频线接进 work_items 要到 P3，现在只反映图文线。"""
    from xhs_manager.models import WorkItem

    rows = session.query(WorkItem.status, WorkItem.attempt).all()
    pending = sum(1 for st, _ in rows if st in ("pending", "available"))
    running = sum(1 for st, _ in rows if st == "running")
    failed = sum(1 for st, _ in rows if st == "failed")
    status = "degraded" if failed else "ok"
    return Probe("queue", "工作队列", status,
                 f"待处理 {pending} · 执行中 {running} · 失败 {failed}"
                 + ("（视频线尚未接入队列，P3 之后才会出现在这里）" if not rows else ""),
                 fix="队列里有失败项，去任务台看具体错误" if failed else "",
                 facts={"pending": pending, "running": running, "failed": failed})


# ── 汇总 ────────────────────────────────────────────────────


def run_all(session, settings, force: bool = False) -> dict[str, Any]:
    """并发跑完所有检查项，按严重度排序返回。带 TTL 缓存。"""
    now = time.monotonic()
    if not force and _CACHE["payload"] and now - _CACHE["at"] < CACHE_TTL_S:
        cached = dict(_CACHE["payload"])
        cached["cached"] = True
        return cached

    t0 = time.monotonic()
    jobs: list[tuple[str, str, Callable[[], Probe]]] = [
        ("opencli", "OpenCLI 扩展", probe_opencli),
        ("collect", "采集后端链", lambda: probe_collect_backends(settings)),
        ("tts", "TTS 提供方", lambda: probe_tts_providers(settings)),
        ("tts_studio", "TTS Studio", lambda: probe_tts_studio(settings)),
        ("mpt", "MoneyPrinterTurbo", lambda: probe_moneyprinter(settings)),
        ("ffmpeg", "ffmpeg", probe_ffmpeg),
        ("renderer", "渲染器", lambda: probe_renderer(settings)),
        ("xhs", "小红书登录态", lambda: probe_xhs_login(settings)),
        ("portrait", "人脸检测模型", probe_portrait_guard),
        ("bgm", "BGM 曲库", lambda: probe_bgm(settings)),
        ("disk", "磁盘空间", lambda: probe_disk(settings)),
    ]

    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        probes = list(pool.map(_timed, jobs))

    # 队列走数据库会话，不能跨线程共用，单独串行跑
    probes.append(_timed(("queue", "工作队列", lambda: probe_queue(session))))

    probes.sort(key=lambda p: (SEVERITY.get(p.status, 9), p.name))
    counts = {st: sum(1 for p in probes if p.status == st)
              for st in ("ok", "degraded", "down", "unknown")}

    payload = {
        "probes": [asdict(p) for p in probes],
        "counts": counts,
        "elapsed_ms": int((time.monotonic() - t0) * 1000),
        "cached": False,
    }
    _CACHE["at"] = now
    _CACHE["payload"] = payload
    return payload


def reset_cache() -> None:
    _CACHE["at"] = 0.0
    _CACHE["payload"] = None
