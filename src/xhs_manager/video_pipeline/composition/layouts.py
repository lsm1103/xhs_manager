"""版面模板：把一个 PlannedScene 渲染成 .scene-content 内部的 HTML。

每个模板只关心「这一屏长什么样」，时间由 render_motion() 统一注入。
"""

from __future__ import annotations

from html import escape

from xhs_manager.video_pipeline.composition.timeline import PlannedScene

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
    head = _text_node(s.text_main, s.animation, "title", t0 + 0.1, 0.6)
    grid = (
        f'<div class="compare-grid">'
        f'<div class="compare-card m m-slide-up" style="--s:{t0 + 0.5:.3f};--d:0.5">'
        f"{escape(left)}</div>"
        f'<div class="compare-vs m m-pop-in" style="--s:{t0 + 0.75:.3f};--d:0.4">VS</div>'
        f'<div class="compare-card is-b m m-slide-up" style="--s:{t0 + 0.95:.3f};--d:0.5">'
        f"{escape(right)}</div>"
        f"</div>"
    )
    return head + "\n" + grid


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
    "outro": _outro,
}


def render_layout(scene: PlannedScene) -> str:
    """按 layout 分派。未知 layout 回落到 statement，不抛错。"""
    return _RENDERERS.get(scene.layout, _statement)(scene)
