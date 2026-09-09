"""逐场景旁白合成 —— 让「场景时长」等于「这段话真正念完要多久」。

为什么需要这一层：
  脚本里的 duration 是 LLM 拍脑袋写的（9 秒、9 秒、8 秒……），
  而 TTS 念完同一段话可能要 11 秒。整段合成时这个误差会累积：
  实测一条标称 75 秒的片子，旁白真实时长 88.9 秒——
  成片按 75 秒截断，最后两句话直接没了。

  对 BGM 来说后果更隐蔽：乐段是按场景 duration 切的，
  标称时长一偏，音乐的情绪转折点就和画面/语音对不上，
  "揭示"的音乐可能在还在铺垫的时候就进来了。

  解决办法是反过来：先逐场景合成，量出真实时长，再拿它当场景时长。
  逐段合成还有个副产品——场景边界精确已知，拼接时不用去猜静音在哪。
"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 每个场景念完后的留白。没有它，上一句的尾音会直接顶着下一句开头，
# 听感是"赶"，而且场景切换处没有呼吸点。
TAIL_PAUSE_S = 0.45


@dataclass
class SceneNarration:
    scene_id: str
    path: Path
    speech: float          # 纯语音时长
    total: float           # 含尾部留白，即该场景应有的时长


def _probe(path: Path) -> float:
    from xhs_manager.video_pipeline.integrations.renderer import probe_duration
    return float(probe_duration(path) or 0.0)


def synthesize_scenes(
    scenes: list[dict[str, Any]],
    out_dir: Path,
    settings: Any,
    tail_pause: float = TAIL_PAUSE_S,
) -> list[SceneNarration]:
    """逐场景合成旁白，返回每段的真实时长。任一场景失败即返回空列表。"""
    from xhs_manager.video_pipeline.tts import registry

    out_dir.mkdir(parents=True, exist_ok=True)
    items: list[SceneNarration] = []

    for i, sc in enumerate(scenes):
        text = (sc.get("narration") or "").strip()
        sid = sc.get("scene_id") or f"s{i + 1:02d}"
        if not text:
            continue

        dst = out_dir / f"{sid}_narration.mp3"
        res = registry.synthesize(
            text, dst, settings, mood=sc.get("bgm_mood"),
        )
        if not res.success:
            logger.error("场景 %s 旁白合成失败: %s", sid, res.error)
            return []

        speech = res.duration or _probe(dst)
        items.append(SceneNarration(sid, dst, speech, speech + tail_pause))

    return items


def concat_narration(
    items: list[SceneNarration],
    output_path: Path,
    tail_pause: float = TAIL_PAUSE_S,
) -> Optional[Path]:
    """把逐场景旁白按顺序拼成整轨，每段后补 tail_pause 的静音。

    补静音用 apad 而不是插入独立静音文件：少一半输入，
    也避免各段采样率不一致时 concat 直接失败。
    """
    if not items:
        return None

    inputs: list[str] = []
    filters: list[str] = []
    for i, it in enumerate(items):
        inputs += ["-i", str(it.path)]
        filters.append(
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"apad=pad_dur={tail_pause:.3f},atrim=0:{it.total:.3f},asetpts=N/SR/TB[n{i}]"
        )
    concat = "".join(f"[n{i}]" for i in range(len(items)))
    filtergraph = ";".join(filters) + f";{concat}concat=n={len(items)}:v=0:a=1[out]"

    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filtergraph,
        "-map", "[out]", "-c:a", "libmp3lame", "-b:a", "192k", str(output_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not output_path.exists():
        logger.error("旁白拼接失败: %s", r.stderr[-500:])
        return None
    return output_path


def calibrate_scene_durations(
    scenes: list[dict[str, Any]],
    items: list[SceneNarration],
) -> float:
    """用实测旁白时长覆盖脚本里的标称时长（原地修改），返回新总时长。"""
    by_id = {it.scene_id: it for it in items}
    total = 0.0
    for sc in scenes:
        it = by_id.get(sc.get("scene_id"))
        if it:
            sc["duration"] = round(it.total, 2)
        total += float(sc.get("duration") or 0)
    return round(total, 2)


def load_scenes(
    scenes: list[dict[str, Any]],
    out_dir: Path,
    tail_pause: float = TAIL_PAUSE_S,
) -> list[SceneNarration]:
    """复用磁盘上已有的逐场景旁白，不重新合成。

    IndexTTS-2 在这台机器上 RTF 十几倍，一条 8 场景的片子要合成七八分钟。
    只是想换个 BGM 情绪重渲一遍时，没有理由再付一次这个钱。
    任一场景缺文件就返回空——宁可全量重合成，也不要新旧音频混在一条轨里。
    """
    items: list[SceneNarration] = []
    for i, sc in enumerate(scenes):
        if not (sc.get("narration") or "").strip():
            continue
        sid = sc.get("scene_id") or f"s{i + 1:02d}"
        p = out_dir / f"{sid}_narration.mp3"
        if not p.exists():
            logger.warning("场景 %s 没有已合成的旁白: %s", sid, p)
            return []
        speech = _probe(p)
        items.append(SceneNarration(sid, p, speech, speech + tail_pause))
    return items


def build_aligned_narration(
    scenes: list[dict[str, Any]],
    out_dir: Path,
    settings: Any,
    tail_pause: float = TAIL_PAUSE_S,
    reuse: bool = False,
) -> tuple[Optional[Path], float]:
    """一站式：逐场景合成 → 校准场景时长 → 拼成整轨。

    reuse=True 时跳过合成，直接用 out_dir 里已有的分段旁白。

    返回 (整轨路径, 校准后的总时长)。失败时返回 (None, 原总时长)。
    """
    items = (
        load_scenes(scenes, out_dir, tail_pause) if reuse
        else synthesize_scenes(scenes, out_dir, settings, tail_pause)
    )
    if not items:
        return None, sum(float(s.get("duration") or 0) for s in scenes)

    total = calibrate_scene_durations(scenes, items)
    track = concat_narration(items, out_dir / "full_narration.mp3", tail_pause)
    if not track:
        return None, total

    logger.info(
        "旁白对齐完成: %d 段, 总时长 %.2fs (%s)",
        len(items), total,
        ", ".join(f"{it.scene_id}={it.total:.1f}s" for it in items),
    )
    return track, total
