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

# 黑金：汽车、金融、高端消费
CARBON_GOLD = Theme(
    name="carbon_gold",
    bg="#0a0908",
    bg_alt="#17130c",
    ink="#fffdf6",
    ink_muted="rgba(255,253,246,0.74)",
    accent="#e9c46a",
    accent_2="#f4a261",
    accent_ink="#0a0908",
)

# 新闻红：热点、事实核查、时事
NEWSROOM = Theme(
    name="newsroom",
    bg="#060b18",
    bg_alt="#0e1a33",
    ink="#ffffff",
    ink_muted="rgba(255,255,255,0.74)",
    accent="#ff4d5e",
    accent_2="#ffb020",
    accent_ink="#ffffff",
)

# 深海青：科普、健康、环保
OCEAN_GLASS = Theme(
    name="ocean_glass",
    bg="#03141a",
    bg_alt="#072a35",
    ink="#ffffff",
    ink_muted="rgba(255,255,255,0.72)",
    accent="#2dd4bf",
    accent_2="#38bdf8",
    accent_ink="#03141a",
)

# 日落橙粉：生活方式、情绪、种草
SUNSET_POP = Theme(
    name="sunset_pop",
    bg="#12060d",
    bg_alt="#2a0f1e",
    ink="#ffffff",
    ink_muted="rgba(255,255,255,0.74)",
    accent="#ff7a59",
    accent_2="#ff3d81",
    accent_ink="#12060d",
)

# 黑白极简：纯观点、文字为主
MONO_INK = Theme(
    name="mono_ink",
    bg="#050505",
    bg_alt="#121212",
    ink="#ffffff",
    ink_muted="rgba(255,255,255,0.70)",
    accent="#ffffff",
    accent_2="#9ca3af",
    accent_ink="#050505",
)

THEMES: dict[str, Theme] = {
    t.name: t
    for t in (
        TECH_NIGHT, WARM_PAPER, ELECTRIC,
        CARBON_GOLD, NEWSROOM, OCEAN_GLASS, SUNSET_POP, MONO_INK,
    )
}

DEFAULT_THEME = TECH_NIGHT


def resolve_theme(name: str | None) -> Theme:
    """按名取主题，未知名字回落到默认，不抛错——渲染不该因为主题名写错就断。"""
    if not name:
        return DEFAULT_THEME
    return THEMES.get(name.strip().lower(), DEFAULT_THEME)


def theme_for_scenes(scenes: list[dict], default: str | None = None) -> str | None:
    """脚本自己指定的主题优先，没有就用全局默认。

    主题是整片一个，不是逐场景换色——所以约定写在**第一个带 theme 的场景**上，
    全片生效。场景是脚本里唯一不受 schema 约束的自由 JSON，借它的一个可选键，
    比为这件事加一列再写迁移轻得多。
    """
    for sc in scenes:
        name = sc.get("theme")
        if name:
            return str(name)
    return default
