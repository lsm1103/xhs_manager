"""生成引擎 —— 把注册表里的模型统一成同一个调用入口。"""

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def extract_waveform(path: Path, buckets: int = 360) -> list[float]:
    """把音频下采样成 N 个峰值，前端直接画波形。

    存下来而不是让前端每次解码：列表里几十条音频全解码会很卡。
    """
    try:
        import librosa
        import numpy as np

        y, _ = librosa.load(str(path), sr=8000, mono=True)
        if y.size == 0:
            return []
        step = max(1, len(y) // buckets)
        peaks = [float(np.abs(y[i:i + step]).max()) for i in range(0, len(y), step)]
        m = max(peaks) or 1.0
        return [round(p / m, 3) for p in peaks[:buckets]]
    except Exception as e:
        logger.warning("波形提取失败 %s: %s", path.name, e)
        return []


def probe(path: Path) -> tuple[Optional[float], Optional[int]]:
    """返回 (时长秒, 采样率)。"""
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=sample_rate", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=30,
        )
        d = json.loads(r.stdout)
        dur = float(d.get("format", {}).get("duration", 0)) or None
        sr = None
        for s in d.get("streams", []):
            if s.get("sample_rate"):
                sr = int(s["sample_rate"])
                break
        return dur, sr
    except Exception:
        return None, None


# ── 各模型的生成实现 ──────────────────────────────────────────


def gen_edge(text: str, out: Path, voice: str, speed: float = 1.0) -> float:
    import asyncio
    import edge_tts

    rate = f"{int((speed - 1) * 100):+d}%"

    async def run():
        c = edge_tts.Communicate(text, voice or "zh-CN-XiaoxiaoNeural", rate=rate)
        await c.save(str(out))

    t0 = time.time()
    asyncio.run(run())
    return round(time.time() - t0, 2)


def gen_voxcpm(text: str, out: Path, settings, ref: Optional[str] = None,
               instruct: str = "") -> float:
    # VoxCPM 的情绪控制是把指令写在文本开头的括号里
    full = f"({instruct}){text}" if instruct else text
    cmd = [settings.voxcpm_cli, "-t", full, "-o", str(out)]
    if ref:
        cmd += ["-r", ref]
    cmd += [settings.voxcpm_base, settings.voxcpm_acoustic]

    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError((r.stderr or r.stdout)[-400:])
    return round(time.time() - t0, 2)


def gen_indextts(registry, text: str, out: Path, ref: str,
                 emotion: str = "", emo_alpha: Optional[float] = None,
                 speed: Optional[float] = None) -> float:
    st = registry.get("indextts2")
    if st.status != "ready":
        raise RuntimeError("IndexTTS-2 未加载，请先在模型面板加载")
    req: dict[str, Any] = {"cmd": "generate", "text": text,
                           "ref_audio": ref, "output": str(out)}
    if emotion:
        req["emotion"] = emotion
    if emo_alpha is not None:
        req["emo_alpha"] = emo_alpha
    if speed is not None:
        req["speed"] = speed

    with st.lock:
        r = registry._rpc(st, req, timeout=1800)
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "生成失败"))
    return float(r.get("elapsed", 0))
