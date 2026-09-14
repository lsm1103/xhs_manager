"""把 base.css 模板 + 主题 编译成可内联的样式表。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from string import Template

from xhs_manager.video_pipeline.composition.theme import Theme

_CSS_PATH = Path(__file__).parent / "assets" / "base.css"


@lru_cache(maxsize=1)
def _template() -> Template:
    return Template(_CSS_PATH.read_text(encoding="utf-8"))


def build_css(theme: Theme, width: int, height: int) -> str:
    """用主题值替换 base.css 里的 ${} 占位符。

    用 substitute 而非 safe_substitute：占位符拼错就该立刻炸，
    而不是把 '${size_titl}px' 原样写进产物、等渲染出来才发现字号不对。
    """
    return _template().substitute(
        width=width,
        height=height,
        bg=theme.bg,
        bg_alt=theme.bg_alt,
        ink=theme.ink,
        ink_muted=theme.ink_muted,
        accent=theme.accent,
        accent_2=theme.accent_2,
        accent_ink=theme.accent_ink,
        size_kicker=theme.size_kicker,
        size_display=theme.size_display,
        size_title=theme.size_title,
        size_body=theme.size_body,
        size_caption=theme.size_caption,
        font_stack=theme.font_stack,
        safe_top=theme.safe.top,
        safe_bottom=theme.safe.bottom,
        safe_left=theme.safe.left,
        safe_right=theme.safe.right,
    )
