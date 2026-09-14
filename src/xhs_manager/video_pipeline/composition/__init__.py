"""HTML 视频组合：主题 / 时间轴 / 版面 / 外壳。"""

from xhs_manager.video_pipeline.composition.builder import (
    SceneMedia,
    build_composition_html,
    classify_media,
    transition_duration,
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
    "THEMES",
    "CaptionCue",
    "PlannedScene",
    "SceneMedia",
    "Theme",
    "Timeline",
    "build_captions",
    "build_composition_html",
    "classify_media",
    "infer_layout",
    "plan_timeline",
    "resolve_theme",
    "split_caption_text",
    "transition_duration",
]
