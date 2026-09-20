"""版面模板：把一个 PlannedScene 渲染成 .scene-content 内部的 HTML。

每个模板只关心「这一屏长什么样」，时间由 render_motion() 统一注入。
"""

from __future__ import annotations

from html import escape

from xhs_manager.video_pipeline.composition.media import SceneMedia
from xhs_manager.video_pipeline.composition.timeline import (
    FocusSpec,
    PlannedScene,
    ScrollSpec,
)

# 文字动画名 → CSS 类
_ANIM_CLASS = {
    "pop_in": "m-pop-in",
    "slide_up": "m-slide-up",
    "typewriter": "m-typewriter",
    "counter": "m-counter",
    "highlight": "m-fade-in",   # 高亮由 .mark::after 负责，主体只需淡入
    "none": "m-fade-in",
}


def motion(start: float, duration: float = 0.6, extra: str = "") -> str:
    """生成动画所需的 class + inline CSS 变量。

    --s / --d 是绝对起始秒和时长；真正的进度由 :root 上的 --t 决定。
    """
    cls = f"m {extra}".strip()
    return f'class="{cls}" style="--s:{start:.3f};--d:{duration:.3f}"'


def anim_class(animation: str) -> str:
    return _ANIM_CLASS.get(animation, "m-pop-in")


def _typewriter_steps(text: str) -> int:
    return max(4, len(text))


def _text_node(text: str, animation: str, base_cls: str, start: float,
               duration: float = 0.6) -> str:
    """渲染一段会动的文字。打字机需要 steps 数，且必须是 inline-block。"""
    safe = escape(text)
    acls = anim_class(animation)
    if animation == "typewriter":
        steps = _typewriter_steps(text)
        return (
            f'<div class="{base_cls}">'
            f'<span class="m {acls}" '
            f'style="--s:{start:.3f};--d:{duration:.3f};--steps:{steps}">{safe}</span>'
            f"</div>"
        )
    return (
        f'<div class="{base_cls} m {acls}" '
        f'style="--s:{start:.3f};--d:{duration:.3f}">{safe}</div>'
    )


# ── 各版面 ────────────────────────────────────────────────────────


def _hook(s: PlannedScene) -> str:
    t0 = s.start
    parts = [
        f'<div class="kicker m m-slide-up" style="--s:{t0:.3f};--d:0.5">'
        f"{escape(s.bgm_mood.upper())}</div>",
        _text_node(s.text_main, s.animation, "display", t0 + 0.18, 0.72),
    ]
    if s.text_sub:
        parts.append(
            f'<div class="body m m-slide-up" style="--s:{t0 + 0.5:.3f};--d:0.6">'
            f"{escape(s.text_sub)}</div>"
        )
    return "\n".join(parts)


def _statement(s: PlannedScene) -> str:
    t0 = s.start
    parts = [_text_node(s.text_main, s.animation, "title", t0 + 0.1, 0.66)]
    if s.text_sub:
        parts.append(
            f'<div class="body m m-slide-up" style="--s:{t0 + 0.42:.3f};--d:0.6">'
            f"{escape(s.text_sub)}</div>"
        )
    return "\n".join(parts)


def _stat(s: PlannedScene) -> str:
    t0 = s.start
    parts = [
        f'<div class="stat-value m m-counter" style="--s:{t0 + 0.1:.3f};--d:0.75">'
        f"{escape(s.stat_value)}</div>"
    ]
    if s.stat_unit:
        parts.append(
            f'<div class="stat-unit m m-slide-up" style="--s:{t0 + 0.55:.3f};--d:0.5">'
            f"{escape(s.stat_unit)}</div>"
        )
    if s.text_sub:
        parts.append(
            f'<div class="body m m-slide-up" style="--s:{t0 + 0.75:.3f};--d:0.55">'
            f"{escape(s.text_sub)}</div>"
        )
    return "\n".join(parts)


def _quote(s: PlannedScene) -> str:
    t0 = s.start
    text = s.text_main.strip("「」『』\"“”")
    parts = [
        f'<div class="quote-mark m m-pop-in" style="--s:{t0:.3f};--d:0.5">&ldquo;</div>',
        f'<div class="quote-text m m-slide-up" style="--s:{t0 + 0.2:.3f};--d:0.66">'
        f"{escape(text)}</div>",
    ]
    if s.text_sub:
        parts.append(
            f'<div class="quote-source m m-fade-in" style="--s:{t0 + 0.6:.3f};--d:0.5">'
            f"{escape(s.text_sub)}</div>"
        )
    return "\n".join(parts)


def _bullets(s: PlannedScene) -> str:
    t0 = s.start
    head = _text_node(s.text_main, s.animation, "title", t0 + 0.1, 0.6)

    # 逐条错开入场；间隔按剩余时长自适应，条目多时不会挤在最后才出完
    n = max(1, len(s.bullets))
    step = min(0.42, max(0.16, (s.duration * 0.55) / n))

    items = []
    for i, b in enumerate(s.bullets):
        at = t0 + 0.5 + i * step
        items.append(
            f'<div class="bullet m m-slide-up" style="--s:{at:.3f};--d:0.5">'
            f'<span class="bullet-index">{i + 1}</span>'
            f"<span>{escape(b)}</span>"
            f"</div>"
        )
    return head + f'\n<div class="bullet-list">{"".join(items)}</div>'


def _compare(s: PlannedScene) -> str:
    t0 = s.start
    left, right = s.compare or ("", "")
    head = (
        _text_node(s.text_main, s.animation, "title", t0 + 0.1, 0.6)
        if s.text_main else ""
    )
    grid = (
        f'<div class="compare-grid">'
        f'<div class="compare-card m m-slide-up" style="--s:{t0 + 0.5:.3f};--d:0.5">'
        f"{escape(left)}</div>"
        f'<div class="compare-vs m m-pop-in" style="--s:{t0 + 0.75:.3f};--d:0.4">VS</div>'
        f'<div class="compare-card is-b m m-slide-up" style="--s:{t0 + 0.95:.3f};--d:0.5">'
        f"{escape(right)}</div>"
        f"</div>"
    )
    return (head + "\n" + grid) if head else grid


# ── 截图特写 ──────────────────────────────────────────────────────

# 放大卡的倍率上下限。不设上限的话，一个 20px 宽的小图标会被放到 70 倍，
# 糊得只剩色块；不设下限的话，框住整个窗口的特写跟原图一样大，白放一次。
ZOOM_MIN, ZOOM_MAX = 1.5, 8.0


def _focus_fractions(
    f: FocusSpec, media: SceneMedia,
) -> tuple[float, float, float, float] | None:
    """把特写框换算成 0-1 的比例。换算不了返回 None。

    比例而不是像素，是为了让同一份脚本在任何画布尺寸下都成立：
    框贴在图片上，图片怎么缩放，框就怎么跟着缩放。
    """
    x, y, w, h = f.rect
    if f.is_fraction:
        return x, y, w, h
    if not media.width or not media.height:
        return None      # 探不出源图尺寸，像素框没有参照系
    return (
        x / media.width, y / media.height,
        w / media.width, h / media.height,
    )


def _window_chrome(label: str) -> str:
    """假的浏览器/终端标题栏。给的那行字直接当地址栏内容。

    值得画：它一眼就说明了「这东西跑在哪」——是浏览器里的 localhost，
    还是 github.com 上的仓库页。光一张截图说不清这件事。
    """
    if not label:
        return ""
    dots = '<span class="win-dot"></span>' * 3
    return (
        f'<div class="win-bar">{dots}'
        f'<span class="win-url">{escape(label)}</span></div>'
    )


def _focus_insets(s: PlannedScene, media: SceneMedia, src: str) -> str:
    """特写放大卡（含说明文字）。screenshot 和 scroll 共用。"""
    insets = []
    for f in s.focus:
        frac = _focus_fractions(f, media)
        if frac is None:
            continue
        fx, fy, fw, fh = frac
        zoom = f.zoom if f.zoom else 1.0 / max(fw, 1e-6)
        zoom = min(max(zoom, ZOOM_MIN), ZOOM_MAX)
        cx, cy = fx + fw / 2, fy + fh / 2

        label = ""
        if f.label or f.note:
            label = (
                f'<div class="shot-label">'
                f"<b>{escape(f.label)}</b>"
                + (f"<small>{escape(f.note)}</small>" if f.note else "")
                + "</div>"
            )
        # translate 的百分比参照的是 img 自己的尺寸，所以把框心平移到
        # 卡片正中只需要这一句，不必知道卡片到底多少像素宽。
        insets.append(
            f'<div class="shot-inset m m-focus" '
            f'style="--s:{f.at:.3f};--d:{f.duration:.3f}">'
            f'<div class="shot-zoom"><img src="{src}" alt="" '
            f'style="width:{zoom * 100:.3f}%;'
            f'transform:translate({-cx * 100:.3f}%,{-cy * 100:.3f}%)"></div>'
            f"{label}</div>"
        )
    return f'<div class="shot-insets">{"".join(insets)}</div>' if insets else ""


def _screenshot(s: PlannedScene, media: SceneMedia | None = None) -> str:
    """整张截图 + 依次亮起的高亮框 + 对应的局部放大卡。

    给「介绍自己做的工具」用：观众要能看清界面上的那个控件，
    而竖屏里横向的界面截图缩到画面宽度后，控件只有十几像素高——
    所以必须配一张把那块放大出来的卡片。
    """
    if media is None:
        return _statement(s)

    t0 = s.start
    head = (
        _text_node(s.text_main, s.animation, "title", t0 + 0.1, 0.6)
        if s.text_main else ""
    )

    src = f"assets/{escape(media.filename)}"
    rings = []
    for f in s.focus:
        frac = _focus_fractions(f, media)
        if frac is None:
            continue
        fx, fy, fw, fh = frac
        rings.append(
            f'<span class="shot-ring m m-focus" '
            f'style="--s:{f.at:.3f};--d:{f.duration:.3f};'
            f"left:{fx * 100:.3f}%;top:{fy * 100:.3f}%;"
            f'width:{fw * 100:.3f}%;height:{fh * 100:.3f}%"></span>'
        )

    frame = (
        f'<div class="shot-frame m m-slide-up" '
        f'style="--s:{t0 + 0.35:.3f};--d:0.6">'
        f'{_window_chrome(s.window)}'
        f'<div class="shot-pane"><img src="{src}" alt="">{"".join(rings)}</div>'
        f"</div>"
    )
    body = frame + _focus_insets(s, media, src)
    return (head + "\n" if head else "") + f'<div class="shot">{body}</div>'


# ── 长截图滚动 ────────────────────────────────────────────────────

# 尺寸探不出来时的兜底行程。宁可少滚一点也不要滚过头：
# 滚过头的画面是一整屏空白，少滚只是没看到底部。
SCROLL_FALLBACK_END = 0.5


def scroll_travel(media: SceneMedia, spec: ScrollSpec) -> float:
    """算出图片要上移自身高度的百分之几（0-1）。

    视窗按 aspect 定高，图片按视窗宽度等比缩放，于是
      可见高度 / 图片渲染高度 = imgW / (aspect * imgH)
    剩下的就是能滚的行程。作者给的 end 只会被往小里夹，不会滚出底边。
    """
    if not media.width or not media.height:
        return spec.end if spec.end else SCROLL_FALLBACK_END
    visible = media.width / (spec.aspect * media.height)
    max_travel = max(0.0, 1.0 - visible)
    return min(spec.end, max_travel) if spec.end else max_travel


def _scroll(s: PlannedScene, media: SceneMedia | None = None) -> str:
    """长截图在视窗里缓缓上移。网页整页、长文档、README 都走这个。

    整段滚动就是一条 translateY 动画，起止由 --travel 决定，进度照旧由 --t 驱动，
    所以逐帧渲染出来和实时播放完全一致。
    """
    if media is None:
        return _statement(s)

    t0 = s.start
    spec = s.scroll or ScrollSpec()
    travel = scroll_travel(media, spec)
    src = f"assets/{escape(media.filename)}"

    head = (
        _text_node(s.text_main, s.animation, "title", t0 + 0.1, 0.6)
        if s.text_main else ""
    )
    frame = (
        f'<div class="shot-frame m m-slide-up" '
        f'style="--s:{t0 + 0.3:.3f};--d:0.6">'
        f'{_window_chrome(s.window)}'
        f'<div class="scroll-pane" style="aspect-ratio:{spec.aspect:.4f}">'
        f'<img class="m m-scroll" src="{src}" alt="" '
        f'style="--s:{t0 + 0.45:.3f};--d:{max(s.duration - 0.9, 0.6):.3f};'
        f'--travel:{-travel * 100:.3f}%"></div>'
        f"</div>"
    )
    body = frame + _focus_insets(s, media, src)
    return (head + "\n" if head else "") + f'<div class="shot">{body}</div>'


# ── 终端 ──────────────────────────────────────────────────────────

# 打字速度：每字符多少秒，整体夹在这个区间。太快看不清是在敲字，
# 太慢会把一句短命令拖满整个场景。
TYPE_PER_CHAR = 0.075
TYPE_MIN, TYPE_MAX = 0.5, 2.0


def _terminal(s: PlannedScene) -> str:
    """终端窗口：命令逐字敲出来，输出随后一行行出现。

    「一条命令就能跑」这件事，用打字机演一遍比写在标题里有说服力——
    观众看到的是命令的长度，而不是一句"很简单"。
    """
    spec = s.terminal
    if spec is None or not spec.command:
        return _statement(s)

    t0 = s.start
    cmd_at = t0 + 0.55
    type_dur = min(max(len(spec.command) * TYPE_PER_CHAR, TYPE_MIN), TYPE_MAX)
    done_at = cmd_at + type_dur

    lines = [
        f'<div class="term-line">'
        f'<span class="term-prompt">{escape(spec.prompt)}</span>'
        f'<span class="m m-typewriter" '
        f'style="--s:{cmd_at:.3f};--d:{type_dur:.3f};'
        f'--steps:{max(4, len(spec.command))}">{escape(spec.command)}</span>'
        f'<i class="term-caret"></i></div>'
    ]
    for i, out in enumerate(spec.output):
        lines.append(
            f'<div class="term-out m m-fade-in" '
            f'style="--s:{done_at + 0.25 + i * 0.3:.3f};--d:0.35">'
            f"{escape(out)}</div>"
        )

    win = (
        f'<div class="term-win m m-slide-up" style="--s:{t0 + 0.15:.3f};--d:0.55">'
        f'{_window_chrome(s.window or "TERMINAL")}'
        f'<div class="term-body">{"".join(lines)}</div></div>'
    )
    sub = (
        f'<div class="body m m-slide-up" '
        f'style="--s:{done_at + 0.3:.3f};--d:0.55">{escape(s.text_sub)}</div>'
        if s.text_sub else ""
    )
    return win + ("\n" + sub if sub else "")


def _outro(s: PlannedScene) -> str:
    t0 = s.start
    parts = [_text_node(s.text_main, s.animation, "title", t0 + 0.1, 0.6)]
    if s.text_sub:
        parts.append(
            f'<div class="outro-cta m m-pop-in" style="--s:{t0 + 0.5:.3f};--d:0.5">'
            f"{escape(s.text_sub)}</div>"
        )
    return "\n".join(parts)


_RENDERERS = {
    "hook": _hook,
    "statement": _statement,
    "stat": _stat,
    "quote": _quote,
    "bullets": _bullets,
    "compare": _compare,
    "terminal": _terminal,
    "outro": _outro,
}

# 需要素材本身参与排版的版面。其余版面只认文字，签名保持单参数。
_MEDIA_RENDERERS = {
    "screenshot": _screenshot,
    "scroll": _scroll,
}


def render_layout(scene: PlannedScene, media: SceneMedia | None = None) -> str:
    """按 layout 分派。未知 layout 回落到 statement，不抛错。"""
    with_media = _MEDIA_RENDERERS.get(scene.layout)
    if with_media is not None:
        return with_media(scene, media)
    return _RENDERERS.get(scene.layout, _statement)(scene)
