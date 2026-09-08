"""肖像权结果侧防御 —— 对**已经拿到的素材**做人脸检测，可辨识就拒。

## 为什么必须有这一层

原来的三层防御（记忆约束 / Stage2 prompt / Stage3 搜索词黑名单）全在**请求侧**，
共同的前提是「危险画面来自危险搜索词」。这个前提是错的。

2026-09-04 下架的那条片子，9 个场景的搜索词逐个查过，没有一个触发黑名单，
而且它们本来也不该被拦：

    search:office worker desk computer      ← 完全正常的词
    search:person ordering menu counter     ← 完全正常的词

Pexels 对这两个词返回的就是咖啡店店员的清晰正脸中景（实测脸宽占画面 11-15%，
检测置信度 0.92-0.94）。搜「office worker」必然返回真人办公场景，
人脸清晰是常态而不是例外。

结论：搜索词和返回内容之间没有可靠映射，请求侧再怎么加词表都兜不住。
唯一能兜住的是看**实际画面**。

## 职责划分

- **搜索词层**（stage3 的 `sanitize_search_term`）：降低搜出人脸素材的**概率**，
  避免整批素材被这一层拒光、最后只能降级成文字卡片。它不负责保证合规。
- **本模块**：保证合规。命中即拒，没有例外。

## fail-closed

检测器不可用时**拒绝全部素材**，而不是放行。
红线的定义就是「宁可不出片，也不能违规」——放行等于这层不存在，
而这层不存在正是上次下架的原因。
"""

import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# YuNet 人脸检测模型。OpenCV 5.0 起移除了 Haar cascade，
# 而 YuNet 本来就更合适：带置信度、侧脸也能检出、模型只有 230KB。
MODEL_URL = (
    "https://github.com/opencv/opencv_zoo/raw/main/models/"
    "face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
MODEL_PATH = Path("data/models/face_detection_yunet.onnx")

# 判定「可辨识」的脸宽阈值（占画面宽度）。
#
# 怎么定的：下架那条片子里的脸实测 11-15%，明显可辨识；
# 1080 宽的竖屏里 5% = 54px，这个尺寸在手机上勉强能看出五官轮廓。
# 远景人群里的脸通常在 3% 以下，不会误伤「无法辨识个人的远景/人群」这类合规素材。
# 阈值往低了取——红线宁可误杀。
MAX_FACE_WIDTH_RATIO = 0.05

# 检测置信度下限。YuNet 对真实人脸给 0.9+，0.6 以下多是误检（纹理、图案）。
MIN_CONFIDENCE = 0.6

# 每个视频抽多少帧。太少会漏掉中途才入镜的人；
# 太多没必要——素材普遍 10-20 秒，8 帧已经能覆盖到镜头切换。
SAMPLE_FRAMES = 8


@dataclass
class FaceScan:
    """一次扫描的结果。"""

    rejected: bool                  # 是否判定为不可用
    reason: str = ""
    max_face_ratio: float = 0.0     # 最大人脸宽度占画面宽度
    frames_hit: int = 0
    frames_sampled: int = 0
    confidence: float = 0.0

    @property
    def detail(self) -> str:
        if not self.frames_sampled:
            return self.reason
        return (
            f"{self.reason}（{self.frames_hit}/{self.frames_sampled} 帧命中，"
            f"最大脸宽 {self.max_face_ratio * 100:.1f}%，conf {self.confidence:.2f}）"
        )


def ensure_model(path: Path = MODEL_PATH) -> bool:
    """确保模型就位，缺失时自动下载一次。"""
    if path.exists() and path.stat().st_size > 100_000:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        logger.info("下载人脸检测模型…")
        urllib.request.urlretrieve(MODEL_URL, str(path))
    except Exception as e:
        logger.error("人脸检测模型下载失败: %s", e)
        return False
    return path.exists() and path.stat().st_size > 100_000


def available() -> tuple[bool, str]:
    """检测器是否可用。不可用时调用方必须 fail-closed。"""
    try:
        import cv2  # noqa: F401
    except ImportError:
        return False, "未安装 opencv（pip install opencv-python-headless）"
    if not ensure_model():
        return False, f"人脸检测模型不可用: {MODEL_PATH}"
    return True, ""


def _scan_frames(frames, width: int, height: int) -> FaceScan:
    """对一组 BGR 帧跑检测。frames 为可迭代的 numpy 数组。"""
    import cv2

    det = cv2.FaceDetectorYN_create(
        str(MODEL_PATH), "", (width, height), MIN_CONFIDENCE, 0.3, 5000,
    )
    max_ratio = 0.0
    conf_at_max = 0.0
    hit = 0
    sampled = 0

    for img in frames:
        if img is None:
            continue
        sampled += 1
        _, faces = det.detect(img)
        if faces is None or not len(faces):
            continue
        hit += 1
        for f in faces:
            ratio = float(f[2]) / max(width, 1)
            if ratio > max_ratio:
                max_ratio = ratio
                conf_at_max = float(f[-1])

    rejected = max_ratio >= MAX_FACE_WIDTH_RATIO
    return FaceScan(
        rejected=rejected,
        reason="检出可辨识人脸" if rejected else "无可辨识人脸",
        max_face_ratio=max_ratio,
        frames_hit=hit,
        frames_sampled=sampled,
        confidence=conf_at_max,
    )


def scan_video(path: Path, sample_frames: int = SAMPLE_FRAMES) -> FaceScan:
    """均匀抽帧扫描一个视频素材。"""
    ok, why = available()
    if not ok:
        return FaceScan(rejected=True, reason=f"检测器不可用，按红线拒收（{why}）")

    import cv2

    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return FaceScan(rejected=True, reason="素材无法打开，按红线拒收")
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if total <= 0 or w <= 0 or h <= 0:
            return FaceScan(rejected=True, reason="素材元信息异常，按红线拒收")

        def gen():
            for i in range(sample_frames):
                cap.set(cv2.CAP_PROP_POS_FRAMES, int(total * i / sample_frames))
                ok_, img = cap.read()
                yield img if ok_ else None

        return _scan_frames(gen(), w, h)
    finally:
        cap.release()


def scan_image(path: Path) -> FaceScan:
    """扫描一张图片素材。"""
    ok, why = available()
    if not ok:
        return FaceScan(rejected=True, reason=f"检测器不可用，按红线拒收（{why}）")

    import cv2

    img = cv2.imread(str(path))
    if img is None:
        return FaceScan(rejected=True, reason="素材无法读取，按红线拒收")
    h, w = img.shape[:2]
    return _scan_frames([img], w, h)


def scan(path: Path) -> FaceScan:
    """按扩展名分派到视频/图片扫描。"""
    if Path(path).suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".avi"):
        return scan_video(Path(path))
    return scan_image(Path(path))


def filter_safe(paths: list[Path]) -> tuple[list[Path], list[tuple[Path, FaceScan]]]:
    """批量过滤，返回 (通过的, [(被拒的, 扫描结果)])。"""
    kept: list[Path] = []
    rejected: list[tuple[Path, FaceScan]] = []
    for p in paths:
        r = scan(Path(p))
        if r.rejected:
            rejected.append((Path(p), r))
            logger.warning("素材因肖像权被拒: %s — %s", Path(p).name, r.detail)
        else:
            kept.append(Path(p))
    return kept, rejected
