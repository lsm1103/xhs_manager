"""视频 Pipeline 领域模型：状态机、枚举、错误类型。"""

from enum import Enum

from xhs_manager.domain import DomainError


class VideoPipelineError(DomainError):
    code = "VIDEO_PIPELINE_ERROR"


class StageError(VideoPipelineError):
    """某个阶段执行失败。"""

    code = "STAGE_ERROR"

    def __init__(self, stage: str, message: str) -> None:
        super().__init__(f"[{stage}] {message}")
        self.stage = stage


class MaterialNotFoundError(VideoPipelineError):
    """素材搜索未找到合适结果。"""

    code = "MATERIAL_NOT_FOUND"


class RenderError(VideoPipelineError):
    """视频渲染失败。"""

    code = "RENDER_ERROR"


class PublishError(VideoPipelineError):
    """平台发布失败。"""

    code = "PUBLISH_ERROR"


# ── Pipeline 运行状态 ──────────────────────────────────────────────


class PipelineStatus(str, Enum):
    COLLECTING = "collecting"
    SELECTING = "selecting"
    MATERIALIZING = "materializing"
    COMPOSING = "composing"
    RENDERING = "rendering"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"


PIPELINE_STAGE_ORDER: list[PipelineStatus] = [
    PipelineStatus.COLLECTING,
    PipelineStatus.SELECTING,
    PipelineStatus.MATERIALIZING,
    PipelineStatus.COMPOSING,
    PipelineStatus.RENDERING,
    PipelineStatus.PUBLISHING,
    PipelineStatus.COMPLETED,
]


def next_stage(current: PipelineStatus) -> PipelineStatus:
    """返回流水线的下一个阶段。COMPLETED 和 FAILED 没有下一阶段。"""
    if current in (PipelineStatus.COMPLETED, PipelineStatus.FAILED):
        raise VideoPipelineError(f"状态 {current.value} 没有后续阶段")
    idx = PIPELINE_STAGE_ORDER.index(current)
    return PIPELINE_STAGE_ORDER[idx + 1]


# ── 平台 ──────────────────────────────────────────────────────────


class Platform(str, Enum):
    XIAOHONGSHU = "xiaohongshu"
    DOUYIN = "douyin"
    BILIBILI = "bilibili"
    TWITTER = "twitter"


# ── 视频类型 ─────────────────────────────────────────────────────


class VideoType(str, Enum):
    EXPLAINER = "explainer"      # 讲解型（概念科普）
    MASHUP = "mashup"            # 混剪型（多素材拼接）
    DATA_VIZ = "data_viz"        # 数据可视化
    COMMENTARY = "commentary"    # 评论型（热点评论）


# ── 素材类型 ─────────────────────────────────────────────────────


class MaterialType(str, Enum):
    VIDEO_CLIP = "video_clip"
    GENERATED_IMAGE = "generated_image"
    STOCK_PHOTO = "stock_photo"
    CHART = "chart"
    TEXT_CARD = "text_card"
    SCREEN_RECORDING = "screen_recording"


# ── 素材来源 ─────────────────────────────────────────────────────


class MaterialSource(str, Enum):
    MONEYPRINTER = "moneyprinter"  # MoneyPrinterTurbo（Pexels/Pixabay/AI生成）
    PIXELLE = "pixelle"            # Pixelle-Video API
    PEXELS = "pexels"              # 直连 Pexels API
    IMAGE_GEN = "image_gen"        # 生图模型兜底
    MANUAL = "manual"


# ── 转场效果 ─────────────────────────────────────────────────────


class Transition(str, Enum):
    FADE = "fade"
    SLIDE_LEFT = "slide_left"
    SLIDE_RIGHT = "slide_right"
    ZOOM_IN = "zoom_in"
    ZOOM_OUT = "zoom_out"
    GLITCH = "glitch"
    BLUR = "blur"
    WIPE = "wipe"
    FLIP = "flip"
    NONE = "none"


# ── 文字动画 ─────────────────────────────────────────────────────


class TextAnimation(str, Enum):
    TYPEWRITER = "typewriter"
    POP_IN = "pop_in"
    SLIDE_UP = "slide_up"
    COUNTER = "counter"
    HIGHLIGHT = "highlight"
    NONE = "none"


# ── 发布方式 ─────────────────────────────────────────────────────


class PublishMethod(str, Enum):
    OPENCLI = "opencli"
    EGO_BROWSER = "ego_browser"
    API = "api"


# ── 各平台默认发布方式 ───────────────────────────────────────────

PLATFORM_PUBLISH_METHOD: dict[Platform, PublishMethod] = {
    Platform.XIAOHONGSHU: PublishMethod.OPENCLI,
    Platform.DOUYIN: PublishMethod.EGO_BROWSER,
    Platform.BILIBILI: PublishMethod.EGO_BROWSER,
    Platform.TWITTER: PublishMethod.EGO_BROWSER,
}

# ── 各平台内容限制 ───────────────────────────────────────────────

PLATFORM_LIMITS: dict[Platform, dict] = {
    Platform.XIAOHONGSHU: {
        "title_max": 20,
        "desc_max": 1000,
        "tags_max": 10,
        "cover_ratio": "3:4",
        "cover_size": (1080, 1440),
        "video_max_mb": 100,
    },
    Platform.DOUYIN: {
        "title_max": 55,
        "desc_max": 300,
        "tags_max": 5,
        "cover_ratio": "9:16",
        "cover_size": (1080, 1920),
        "video_max_mb": 128,
    },
    Platform.BILIBILI: {
        "title_max": 80,
        "desc_max": 2000,
        "tags_max": 12,
        "cover_ratio": "16:9",
        "cover_size": (1920, 1080),
        "video_max_mb": 4096,
    },
    Platform.TWITTER: {
        "title_max": 280,
        "desc_max": 0,
        "tags_max": 0,
        "cover_ratio": "16:9",
        "cover_size": (1920, 1080),
        "video_max_mb": 512,
    },
}
