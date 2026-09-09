"""曲库索引 —— 给无标签的音乐文件自动打情绪标签。

MoneyPrinterTurbo 自带 29 首曲子，文件名是 output000.mp3 这种，
没有任何风格信息。人工听一遍不现实也不可复现，
改为提取音频特征（tempo / energy / brightness）后按 MOOD_PROFILES 匹配。

索引结果缓存到 JSON，只在曲库变化时重算（每首约 2-3 秒）。
"""

import json
import logging
import random
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from xhs_manager.video_pipeline.audio.moods import MOOD_PROFILES, BgmMood

logger = logging.getLogger(__name__)

AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac")


@dataclass
class Track:
    path: str
    duration: float
    tempo: float
    energy: float
    brightness: float
    moods: list[str]          # 匹配到的情绪（可多个）
    scores: dict[str, float]  # 每个情绪的匹配分，便于排序和调试


def _fit(value: float, lo: float, hi: float) -> float:
    """特征值落在区间内得 1 分，区间外按距离衰减，永不为负。"""
    if lo <= value <= hi:
        return 1.0
    span = max(hi - lo, 1e-6)
    dist = (lo - value) if value < lo else (value - hi)
    return max(0.0, 1.0 - dist / span)


def analyze_track(path: Path) -> Optional[Track]:
    """提取单首曲子的特征并打情绪标签。"""
    import librosa
    import numpy as np

    try:
        # 只读前 60 秒：整首 180 秒会让索引变慢，且开头足以代表风格
        y, sr = librosa.load(str(path), sr=22050, mono=True, duration=60)
        if y.size == 0:
            return None

        tempo = float(np.atleast_1d(librosa.beat.beat_track(y=y, sr=sr)[0])[0])
        rms = float(np.mean(librosa.feature.rms(y=y)))
        # RMS 原始值很小，用对数映射到 0-1 更贴近听感
        energy = min(1.0, max(0.0, (math.log10(rms + 1e-6) + 3) / 2.5))
        brightness = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
        duration = float(librosa.get_duration(path=str(path)))
    except Exception as e:
        logger.warning("分析失败 %s: %s", path.name, e)
        return None

    return Track(
        path=str(path), duration=round(duration, 2),
        tempo=round(tempo, 1), energy=round(energy, 3),
        brightness=round(brightness, 1), moods=[], scores={},
    )


def _percentile_ranks(values: list[float]) -> list[float]:
    """把一组数值映射成 0-1 的组内百分位。全部相同则都给 0.5。"""
    n = len(values)
    if n == 0:
        return []
    if n == 1:
        return [0.5]
    order = sorted(range(n), key=lambda i: values[i])
    rank = [0.0] * n
    for pos, idx in enumerate(order):
        rank[idx] = pos / (n - 1)
    return rank


def classify_library(tracks: list[Track]) -> None:
    """按库内百分位给所有曲目打情绪标签（原地修改）。

    必须整库一起算 —— 单首曲子无法知道自己在库里算快还是算慢。
    """
    if not tracks:
        return
    pct = {
        "tempo": _percentile_ranks([t.tempo for t in tracks]),
        "energy": _percentile_ranks([t.energy for t in tracks]),
        "brightness": _percentile_ranks([t.brightness for t in tracks]),
    }
    for i, t in enumerate(tracks):
        scores: dict[str, float] = {}
        for mood, prof in MOOD_PROFILES.items():
            scores[mood.value] = round(
                sum(_fit(pct[k][i], *prof[k]) for k in prof) / len(prof), 3
            )
        best = max(scores.values())
        t.scores = scores
        t.moods = [m for m, sc in scores.items() if sc >= best - 0.05]


class MusicLibrary:
    """按情绪检索的曲库。"""

    def __init__(self, dirs: list[Path], cache_path: Optional[Path] = None) -> None:
        self.dirs = [Path(d) for d in dirs]
        self.cache_path = Path(cache_path) if cache_path else None
        self.tracks: list[Track] = []

    def _audio_files(self) -> list[Path]:
        files: list[Path] = []
        for d in self.dirs:
            if d.exists():
                files.extend(
                    f for f in sorted(d.rglob("*"))
                    if f.suffix.lower() in AUDIO_EXTS
                )
        return files

    def load(self, rebuild: bool = False) -> int:
        """加载曲库索引，必要时重建。返回曲目数。"""
        files = self._audio_files()
        if not files:
            logger.warning("曲库为空: %s", [str(d) for d in self.dirs])
            return 0

        if not rebuild and self.cache_path and self.cache_path.exists():
            try:
                data = json.loads(self.cache_path.read_text(encoding="utf-8"))
                cached = [Track(**t) for t in data.get("tracks", [])]
                # 文件集合没变才用缓存
                if {t.path for t in cached} == {str(f) for f in files}:
                    self.tracks = cached
                    logger.info("曲库索引命中缓存: %d 首", len(cached))
                    return len(cached)
            except Exception as e:
                logger.warning("缓存读取失败，重建: %s", e)

        logger.info("分析曲库 %d 首（每首约 2-3 秒）…", len(files))
        self.tracks = [t for t in (analyze_track(f) for f in files) if t]
        classify_library(self.tracks)

        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({"tracks": [asdict(t) for t in self.tracks]},
                           ensure_ascii=False, indent=1),
                encoding="utf-8",
            )
        logger.info("曲库索引完成: %d 首", len(self.tracks))
        return len(self.tracks)

    def pick(self, mood: BgmMood, min_duration: float = 0.0,
             exclude: Optional[set[str]] = None,
             rng: "random.Random | None" = None,
             band: float = 0.88, top_k: int = 6) -> Optional[Track]:
        """按情绪选一首。同一条视频内尽量不重复（exclude 传已用过的路径）。

        为什么不是取最高分：argmax 是确定性的，同一个情绪永远选中同一首。
        曲库从 20 首扩到 60 首也救不了——每条片子听到的还是那几首。
        改成在「最高分的 band 倍以内、最多 top_k 首」里按分数加权随机：
        质量下限还是分数说了算，但不再钉死在第一名上。

        band 不宜再放宽：0.88 大致意味着"和最佳差距在一成出头以内"，
        再松就会把明显不搭的曲子放进候选。
        """
        rng = rng or random
        exclude = exclude or set()
        cands = [
            t for t in self.tracks
            if t.duration >= min_duration and t.path not in exclude
        ]
        if not cands:
            # 宁可重复也不要没有音乐
            cands = [t for t in self.tracks if t.duration >= min_duration]
        if not cands:
            return None

        cands.sort(key=lambda t: t.scores.get(mood.value, 0.0), reverse=True)
        best = cands[0].scores.get(mood.value, 0.0)
        if best <= 0:
            return cands[0]

        pool = [t for t in cands[:top_k] if t.scores.get(mood.value, 0.0) >= best * band]
        weights = [t.scores.get(mood.value, 0.0) for t in pool]
        return rng.choices(pool, weights=weights, k=1)[0]

    def stats(self) -> dict[str, int]:
        """每个情绪下的曲目数，用于检查曲库覆盖是否均衡。"""
        out = {m.value: 0 for m in BgmMood}
        for t in self.tracks:
            for m in t.moods:
                out[m] = out.get(m, 0) + 1
        return out
