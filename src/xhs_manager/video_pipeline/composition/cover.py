"""封面：按各平台比例**重新排版**，而不是从 9:16 成片里裁一块。

裁剪的问题是它必然丢东西：1080x1920 裁成 3:4 要砍掉上下各 240px，
顶部品牌条和底部字幕正好在那里，于是小红书封面上字幕被切了一半。

这里每个比例单独排一版：背景仍用素材（或渐变兜底），
但文字是按目标画布重新摆的，缩到信息流里那么小也读得清。
"""

from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from html import escape
from pathlib import Path
from string import Template

from xhs_manager.video_pipeline.composition.builder import SceneMedia
from xhs_manager.video_pipeline.composition.theme import Theme, resolve_theme

_CSS_PATH = Path(__file__).parent / "assets" / "cover.css"


@dataclass(frozen=True)
class CoverSpec:
    """一个平台的封面画布。"""

    platform: str
    width: int
    height: int

    @property
    def is_wide(self) -> bool:
        return self.width > self.height


# 和 domain.PLATFORM_LIMITS 的 cover_size 对齐
COVER_SPECS: dict[str, CoverSpec] = {
    "default": CoverSpec("default", 1080, 1440),      # 3:4，小红书主用
    "xiaohongshu": CoverSpec("xiaohongshu", 1080, 1440),
    "douyin": CoverSpec("douyin", 1080, 1920),        # 9:16
    "bilibili": CoverSpec("bilibili", 1920, 1080),    # 16:9
    "twitter": CoverSpec("twitter", 1920, 1080),
}


# ── 字号自适应 ────────────────────────────────────────────────────
#
# 封面标题必须在信息流缩略图里还能读，所以宁可字大、字少。
# 用 CSS 自动缩放要跑 JS 测量，逐帧渲染里不可复现；这里在 Python 侧
# 按「估算宽度 / 盒子宽度」算行数，确定性、可测试。

TITLE_MAX = 132     # 字号上限
TITLE_MIN = 48      # 字号下限，再小就不像封面了
PACK_SLACK = 0.94   # 断行粒度导致的排布损耗


def _text_units(text: str) -> float:
    """估算文本宽度，单位是「一个全角字」。

    CJK 和全角标点约等于 1em，ASCII 约 0.55em，空格更窄。
    只要估得够准能选对字号就行，不需要真去测字形。
    """
    total = 0.0
    for ch in text:
        if ch == " ":
            total += 0.3
        elif ord(ch) < 128:
            total += 0.55
        else:
            total += 1.0
    return total


def fit_title_size(title: str, box_width: int, max_lines: int = 2) -> int:
    """选一个能在 max_lines 行内放下的最大字号。

    只按字数分档是不够的：同样 12 个字，在 3:4 的 928px 盒子里
    和在 16:9 的 1115px 盒子里能放的字号不一样。按字数分档会把
    「AI 写代码，人做什么？」断成 "……人做什" / "么？"，
    末行只剩两个字，很难看。

    两行放不下就放宽到三行，再放不下就取下限（让它自己换行）。
    """
    units = _text_units(title.strip())
    if units <= 0:
        return TITLE_MAX

    for limit in (max_lines, max_lines + 1):
        for size in range(TITLE_MAX, TITLE_MIN - 1, -2):
            # 乘 PACK_SLACK：浏览器只能在允许换行的位置断，
            # 实际每行塞不满理论宽度。不留余量就会算成 2 行、
            # 实际排成 3 行，末行只剩一个字。
            per_line = (box_width / size) * PACK_SLACK
            if per_line <= 0:
                continue
            if math.ceil(units / per_line) <= limit:
                return size
    return TITLE_MIN


# 标题里想高亮的片段：「」『』引号内，或数字+单位
_HL = re.compile(r"([「『][^」』]+[」』]|[+\-]?\d[\d,.]*\s*[%倍x×]?)")


def _render_title(title: str) -> str:
    """把引号片段和数字标成强调色。先转义再插标签，避免 XSS。"""
    out: list[str] = []
    pos = 0
    for m in _HL.finditer(title):
        out.append(escape(title[pos:m.start()]))
        out.append(f'<span class="hl">{escape(m.group(0))}</span>')
        pos = m.end()
    out.append(escape(title[pos:]))
    return "".join(out)


@lru_cache(maxsize=1)
def _template() -> Template:
    return Template(_CSS_PATH.read_text(encoding="utf-8"))


def title_box_width(spec: CoverSpec, pad: int) -> int:
    """标题实际可用的横向空间。16:9 上正文只占 66%，不横跨整幅。"""
    inner = spec.width - 2 * pad
    return int(inner * 0.66) if spec.is_wide else inner


def _build_css(theme: Theme, spec: CoverSpec, title: str) -> str:
    # 以短边为基准缩放，三种比例的短边都是 1080，所以默认都是 1.0
    scale = min(spec.width, spec.height) / 1080
    return _template().substitute(
        width=spec.width,
        height=spec.height,
        bg=theme.bg,
        bg_alt=theme.bg_alt,
        ink=theme.ink,
        ink_muted=theme.ink_muted,
        accent=theme.accent,
        accent_2=theme.accent_2,
        accent_ink=theme.accent_ink,
        font_stack=theme.font_stack,
        pad=int(76 * scale),
        gap=int(14 * scale),
        brand_size=int(32 * scale),
        dot=int(36 * scale),
        dot_radius=int(11 * scale),
        kicker_size=int(32 * scale),
        kicker_gap=int(20 * scale),
        rule=int(56 * scale),
        rule_h=max(3, int(4 * scale)),
        title_size=fit_title_size(title, title_box_width(spec, int(76 * scale))),
        sub_size=int(40 * scale),
        sub_gap=int(24 * scale),
        badge_size=int(28 * scale),
        badge_pad_v=int(12 * scale),
        badge_pad_h=int(28 * scale),
    )


def _background_html(media: SceneMedia | None, assets_prefix: str = "assets") -> str:
    """背景层。assets_prefix 是从封面 HTML 所在目录到素材目录的相对路径。"""
    if media is None:
        return '<div class="cover-bg is-gradient"></div>'

    src = escape(f"{assets_prefix}/{media.filename}")
    if media.kind == "video":
        # 视频取一帧当底：由渲染侧 seek 到 seek_second 再截图
        return (
            f'<div class="cover-bg">'
            f'<video src="{src}" muted playsinline preload="auto"></video></div>'
        )
    return (
        f'<div class="cover-bg">'
        f"<div class=\"still\" style=\"background-image:url('{src}')\"></div></div>"
    )


def build_cover_html(
    *,
    title: str,
    subtitle: str = "",
    kicker: str = "",
    badge: str = "",
    brand: str = "AI 工作流实验员",
    spec: CoverSpec | None = None,
    theme: Theme | str | None = None,
    media: SceneMedia | None = None,
    assets_prefix: str = "assets",
) -> str:
    """生成一张封面的 HTML。"""
    spec = spec or COVER_SPECS["default"]
    th = theme if isinstance(theme, Theme) else resolve_theme(theme)
    title = (title or "").strip()

    parts = []
    if kicker:
        parts.append(f'<div class="cover-kicker">{escape(kicker)}</div>')
    parts.append(f'<div class="cover-title">{_render_title(title)}</div>')
    if subtitle:
        parts.append(f'<div class="cover-sub">{escape(subtitle)}</div>')
    if badge:
        parts.append(f'<div class="cover-badge">{escape(badge)}</div>')

    wide_cls = " is-wide" if spec.is_wide else ""

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Cover {escape(spec.platform)}</title>
<style>
{_build_css(th, spec, title)}
</style>
</head>
<body>
<div class="cover{wide_cls}">
{_background_html(media, assets_prefix)}
  <div class="cover-scrim"></div>
  <div class="cover-tint"></div>
  <div class="cover-body">
    <div class="cover-brand"><span class="cover-brand-dot"></span>{escape(brand)}</div>
    <div class="cover-foot">
{chr(10).join("      " + p for p in parts)}
    </div>
  </div>
  <div class="vignette"></div>
</div>
</body>
</html>
"""


# ── 批量生成 ──────────────────────────────────────────────────────


def build_all_covers(
    out_dir: Path,
    *,
    title: str,
    subtitle: str = "",
    kicker: str = "",
    badge: str = "",
    brand: str = "AI 工作流实验员",
    theme: Theme | str | None = None,
    media: SceneMedia | None = None,
    assets_dir: Path | None = None,
    platform_titles: dict[str, str] | None = None,
    platforms: list[str] | None = None,
) -> dict[str, str]:
    """为各平台各排一版封面并渲染成 JPEG。

    platform_titles 可以给某个平台单独指定标题（比如小红书限 20 字，
    B站可以更长）；没给就都用 title。

    返回 {platform: jpg 路径}，渲染失败的平台不会出现在结果里。
    """
    from xhs_manager.video_pipeline.integrations.renderer import render_html_images

    names = platforms or list(COVER_SPECS)
    titles = platform_titles or {}

    html_dir = out_dir / "_cover_html"
    html_dir.mkdir(parents=True, exist_ok=True)

    # 封面 HTML 和素材通常不在同一层（HTML 在 _cover_html/，素材在组合目录的
    # assets/），所以要算一次相对路径，否则背景图 404、封面变成纯渐变。
    if assets_dir is not None:
        assets_prefix = os.path.relpath(assets_dir, html_dir).replace(os.sep, "/")
    else:
        assets_prefix = "assets"

    jobs = []
    by_out: dict[Path, str] = {}
    for name in names:
        spec = COVER_SPECS.get(name)
        if spec is None:
            continue
        html = build_cover_html(
            title=titles.get(name, title),
            subtitle=subtitle,
            kicker=kicker,
            badge=badge,
            brand=brand,
            spec=spec,
            theme=theme,
            media=media,
            assets_prefix=assets_prefix,
        )
        html_path = html_dir / f"cover_{name}.html"
        html_path.write_text(html, encoding="utf-8")

        out_path = out_dir / f"cover_{name}.jpg"
        jobs.append((html_path, out_path, spec.width, spec.height))
        by_out[out_path] = name

    rendered = render_html_images(jobs)
    return {by_out[p]: str(p) for p in rendered}
