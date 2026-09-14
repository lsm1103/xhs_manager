"""视觉主题：颜色、字阶、安全区。

把设计决策从 HTML 生成逻辑里抽出来，换风格只改这里，不动 builder。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SafeArea:
    """各平台 UI 会盖住的区域（px，基于 1080x1920）。

    小红书右侧有点赞/收藏/评论竖排按钮，底部有作者信息和文案区；
    正文和字幕必须避开，否则发出去被遮一半。
    """

    top: int = 160
    bottom: int = 420
    left: int = 72
    right: int = 190


@dataclass(frozen=True)
class Theme:
    name: str

    # ── 底色 ──
    bg: str
    bg_alt: str

    # ── 文字 ──
    ink: str
    ink_muted: str

    # ── 强调色 ──
    accent: str
    accent_2: str
    accent_ink: str

    # ── 字号（px，基于 1080 宽）──
    size_kicker: int = 34
    size_display: int = 104
    size_title: int = 76
    size_body: int = 44
    size_caption: int = 40

    font_stack: str = (
        "'Noto Sans SC', 'Source Han Sans SC', 'PingFang SC', "
        "'Microsoft YaHei', 'Helvetica Neue', sans-serif"
    )
    mono_stack: str = "'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace"

    safe: SafeArea = field(default_factory=SafeArea)


TECH_NIGHT = Theme(
    name="tech_night",
    bg="#05060a",
    bg_alt="#0d1220",
    ink="#ffffff",
    ink_muted="rgba(255,255,255,0.72)",
    accent="#3ddc97",
    accent_2="#4d8cff",
    accent_ink="#05060a",
)

WARM_PAPER = Theme(
    name="warm_paper",
    bg="#12100e",
    bg_alt="#1e1a16",
    ink="#fdfbf7",
    ink_muted="rgba(253,251,247,0.74)",
    accent="#ffb703",
    accent_2="#fb8500",
    accent_ink="#12100e",
)

ELECTRIC = Theme(
    name="electric",
    bg="#07030f",
    bg_alt="#170a2b",
    ink="#ffffff",
    ink_muted="rgba(255,255,255,0.70)",
    accent="#c77dff",
    accent_2="#00e5ff",
    accent_ink="#07030f",
)

THEMES: dict[str, Theme] = {
    t.name: t for t in (TECH_NIGHT, WARM_PAPER, ELECTRIC)
}

DEFAULT_THEME = TECH_NIGHT


def resolve_theme(name: str | None) -> Theme:
    """按名取主题，未知名字回落到默认，不抛错——渲染不该因为主题名写错就断。"""
    if not name:
        return DEFAULT_THEME
    return THEMES.get(name.strip().lower(), DEFAULT_THEME)
