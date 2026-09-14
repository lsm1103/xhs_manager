"""HTML 视频组合：主题 / 时间轴 / 版面 / 外壳。"""

from xhs_manager.video_pipeline.composition.builder import (
    SceneMedia,
    build_composition_html,
    classify_media,
    transition_duration,
)
from xhs_manager.video_pipeline.composition.cover import (
    COVER_SPECS,
    CoverSpec,
    build_all_covers,
    build_cover_html,
    fit_title_size,
)
from xhs_manager.video_pipeline.composition.theme import THEMES, Theme, resolve_theme
from xhs_manager.video_pipeline.composition.timeline import (
    CaptionCue,
    PlannedScene,
    Timeline,
    build_captions,
    infer_layout,
    plan_timeline,
    split_caption_text,
)

__all__ = [
    "COVER_SPECS",
    "THEMES",
    "CaptionCue",
    "CoverSpec",
    "PlannedScene",
    "SceneMedia",
    "Theme",
    "Timeline",
    "build_all_covers",
    "build_captions",
    "build_cover_html",
    "build_composition_html",
    "classify_media",
    "fit_title_size",
    "infer_layout",
    "plan_timeline",
    "resolve_theme",
    "split_caption_text",
    "transition_duration",
]
