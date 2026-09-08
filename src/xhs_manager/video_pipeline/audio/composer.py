"""BGM 分段配乐 —— 把场景情绪合并成乐段，选曲、裁剪、交叉淡化、与旁白混音。"""

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from xhs_manager.video_pipeline.audio.library import MusicLibrary, Track
from xhs_manager.video_pipeline.audio.moods import BgmMood, default_mood_for_position

logger = logging.getLogger(__name__)

# 乐段最短时长。低于这个值听感是"音乐还没进来就换了"。
# 注意不能定太高：场景普遍 5-7 秒，阈值 8 秒会把所有段并成一段。
MIN_SEGMENT_S = 6.0

# 每多少秒允许一个乐段。60 秒视频约 3-4 段，
# 再多就是每十几秒换一次歌，听感比不换还差。
SECONDS_PER_SEGMENT = 16.0
MAX_SEGMENTS = 5

# 段间交叉淡化时长
CROSSFADE_S = 1.5

# BGM 相对旁白的基础音量。
# 0.22 实听偏轻（"能听见但不成立"），0.30 是音乐能撑起氛围、
# 又不会在 ducking 松开的间隙抢过人声的位置。
BGM_GAIN = 0.30

# 有旁白时把 BGM 再压低多少（ducking）。
# 不做 ducking 的话音乐会糊住人声，这是"干"和"不干"之外的另一个问题。
DUCK_RATIO = 8.0


@dataclass
class BgmSegment:
    """一个乐段：连续同情绪的场景合并而成。"""
    mood: BgmMood
    start: float
    duration: float
    scene_ids: list[str]
    track: Optional[Track] = None

    @property
    def end(self) -> float:
        return self.start + self.duration


def plan_segments(scenes: list[dict[str, Any]]) -> list[BgmSegment]:
    """把场景序列合并成乐段。

    相邻同情绪场景合并；过短的乐段并入前一段，避免音乐频繁切换。
    """
    if not scenes:
        return []

    segs: list[BgmSegment] = []
    cursor = 0.0
    for i, sc in enumerate(scenes):
        dur = float(sc.get("duration", 5) or 5)
        sid = sc.get("scene_id", f"s{i + 1:02d}")
        raw = (sc.get("bgm_mood") or "").strip().lower()
        try:
            mood = BgmMood(raw)
        except ValueError:
            mood = default_mood_for_position(i, len(scenes))

        if segs and segs[-1].mood == mood:
            segs[-1].duration += dur
            segs[-1].scene_ids.append(sid)
        else:
            segs.append(BgmSegment(mood=mood, start=cursor, duration=dur, scene_ids=[sid]))
        cursor += dur

    return _consolidate(segs)


def _consolidate(segs: list[BgmSegment]) -> list[BgmSegment]:
    """迭代合并：每次挑最短的一段并进相邻段，直到都达标且段数不超上限。

    并进哪一边：选相邻段里更长的那个，让长段吸收短段，
    避免把两个短段并成一个仍然偏短的段。
    """
    total = sum(s.duration for s in segs)
    max_segs = max(1, min(MAX_SEGMENTS, int(total // SECONDS_PER_SEGMENT) or 1))

    while len(segs) > 1:
        shortest = min(range(len(segs)), key=lambda i: segs[i].duration)
        too_short = segs[shortest].duration < MIN_SEGMENT_S
        too_many = len(segs) > max_segs
        if not (too_short or too_many):
            break

        if shortest == 0:
            target = 1
        elif shortest == len(segs) - 1:
            target = shortest - 1
        else:
            target = (shortest - 1 if segs[shortest - 1].duration >= segs[shortest + 1].duration
                      else shortest + 1)

        src = segs.pop(shortest)
        if target > shortest:
            target -= 1
        dst = segs[target]
        dst.duration += src.duration
        if src.start < dst.start:
            dst.start = src.start
            dst.scene_ids = src.scene_ids + dst.scene_ids
        else:
            dst.scene_ids = dst.scene_ids + src.scene_ids

    # 合并后重排起点，保证连续无缝
    cursor = 0.0
    for seg in segs:
        seg.start = cursor
        cursor += seg.duration
    return segs


def assign_tracks(segments: list[BgmSegment], library: MusicLibrary) -> list[BgmSegment]:
    """给每个乐段选曲，同一视频内尽量不重复。"""
    used: set[str] = set()
    for seg in segments:
        # 留出 crossfade 的余量
        need = seg.duration + CROSSFADE_S * 2
        t = library.pick(seg.mood, min_duration=min(need, 30.0), exclude=used)
        if t:
            seg.track = t
            used.add(t.path)
        else:
            logger.warning("乐段 %s 未选到曲子", seg.mood.value)
    return segments


def build_bgm_track(
    segments: list[BgmSegment],
    output_path: Path,
    total_duration: float,
) -> Optional[Path]:
    """把各乐段的曲子裁剪拼接成一条完整 BGM 音轨（段间交叉淡化）。"""
    usable = [s for s in segments if s.track]
    if not usable:
        return None

    inputs: list[str] = []
    filters: list[str] = []

    for i, seg in enumerate(usable):
        # 每段从曲子的 8 秒处开始取，避开可能的静音前奏
        take = seg.duration + (CROSSFADE_S if i < len(usable) - 1 else 0)
        inputs += ["-ss", "8", "-t", f"{take:.3f}", "-i", seg.track.path]
        # 单段自身首尾淡入淡出，避免拼接处爆音
        filters.append(
            f"[{i}:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,"
            f"afade=t=in:st=0:d=0.4,"
            f"afade=t=out:st={max(take - 0.4, 0):.3f}:d=0.4[a{i}]"
        )

    if len(usable) == 1:
        chain = "[a0]anull[bgm]"
    else:
        parts = []
        prev = "a0"
        for i in range(1, len(usable)):
            out = "bgm" if i == len(usable) - 1 else f"x{i}"
            parts.append(
                f"[{prev}][a{i}]acrossfade=d={CROSSFADE_S}:c1=tri:c2=tri[{out}]"
            )
            prev = out
        chain = ";".join(parts)

    filtergraph = ";".join(filters) + ";" + chain
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-filter_complex", filtergraph,
        "-map", "[bgm]", "-t", f"{total_duration:.3f}",
        "-c:a", "aac", "-b:a", "192k", str(output_path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not output_path.exists():
        logger.error("BGM 拼接失败: %s", r.stderr[-500:])
        return None

    logger.info(
        "BGM 音轨已生成: %d 段 (%s)",
        len(usable), " → ".join(s.mood.value for s in usable),
    )
    return output_path


def mix_with_narration(
    bgm_path: Path,
    narration_path: Optional[Path],
    output_path: Path,
    duration: float,
) -> Optional[Path]:
    """混合 BGM 与旁白。有旁白时对 BGM 做 sidechain ducking。"""
    if narration_path is None or not narration_path.exists():
        cmd = [
            "ffmpeg", "-y", "-i", str(bgm_path),
            "-af", f"volume={BGM_GAIN}",
            "-t", f"{duration:.3f}", "-c:a", "aac", "-b:a", "192k", str(output_path),
        ]
    else:
        # sidechaincompress: 用旁白当触发源压低音乐，人声一出现музыка自动让路
        cmd = [
            "ffmpeg", "-y",
            "-i", str(bgm_path), "-i", str(narration_path),
            "-filter_complex",
            f"[0:a]volume={BGM_GAIN}[bg];"
            f"[1:a]asplit=2[nar][key];"
            f"[bg][key]sidechaincompress="
            f"threshold=0.05:ratio={DUCK_RATIO}:attack=15:release=350[ducked];"
            f"[ducked][nar]amix=inputs=2:duration=first:dropout_transition=0,"
            f"loudnorm=I=-16:TP=-1.5:LRA=11[out]",
            "-map", "[out]", "-t", f"{duration:.3f}",
            "-c:a", "aac", "-b:a", "192k", str(output_path),
        ]

    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0 or not output_path.exists():
        logger.error("混音失败: %s", r.stderr[-500:])
        return None
    logger.info("混音完成（%s）", "含旁白 ducking" if narration_path else "仅 BGM")
    return output_path


def compose_soundtrack(
    scenes: list[dict[str, Any]],
    library: MusicLibrary,
    work_dir: Path,
    total_duration: float,
    narration_path: Optional[Path] = None,
) -> tuple[Optional[Path], list[BgmSegment]]:
    """一站式：规划乐段 → 选曲 → 拼 BGM → 与旁白混音。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    segments = assign_tracks(plan_segments(scenes), library)
    if not segments:
        return None, []

    bgm = build_bgm_track(segments, work_dir / "bgm_track.m4a", total_duration)
    if not bgm:
        return None, segments

    final = mix_with_narration(
        bgm, narration_path, work_dir / "soundtrack.m4a", total_duration,
    )
    return final, segments
