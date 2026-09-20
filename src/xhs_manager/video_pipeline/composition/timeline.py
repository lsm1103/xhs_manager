"""时间轴规划：把脚本场景编译成「带绝对时间的可渲染单元」。

渲染器是逐帧 seek 的，所以这里算出来的每一个时间点都必须是
**脚本的纯函数**——不依赖浏览器墙钟、不依赖截图快慢。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# 版面模板。LLM 可直接在场景里指定 layout；没指定就用下面的启发式推断。
LAYOUTS = (
    "hook",       # 开场钩子：超大字 + 编号角标
    "statement",  # 通用陈述：主副标题
    "stat",       # 数据揭示：大数字 + 单位 + 说明
    "quote",      # 引用：引号 + 出处
    "bullets",    # 要点列表：逐条入场
    "compare",    # 对比：左右分屏 A vs B
    "screenshot", # 截图特写：整图 + 高亮框 + 局部放大卡
    "scroll",     # 长截图滚动：网页/文档整页在视窗里缓缓上移
    "terminal",   # 终端：命令逐字敲出来，输出随后出现
    "outro",      # 收尾：行动号召
)

# 一条字幕最多多少字。超过就断句——竖屏一行放不下太多字。
CAPTION_MAX_CHARS = 18

# 断句优先级：先按句末标点，再按逗号顿号
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")
_CLAUSE_SPLIT = re.compile(r"(?<=[，,、])")


@dataclass
class CaptionCue:
    """一条字幕：绝对起止时间 + 文本。"""

    start: float
    end: float
    text: str

    @property
    def duration(self) -> float:
        return max(0.01, self.end - self.start)


@dataclass
class FocusSpec:
    """截图上的一个特写点：框住哪块、什么时候出现、配什么说明。

    rect 是 (x, y, w, h)。作者按截图量出来的像素值最省事，所以这里原样留着，
    换算成比例要等到版面拿到素材真实尺寸的时候——只有那时才知道除以多少。
    四个数全都 <= 1 时视为已经是比例，不再换算（素材尺寸探不出来时的退路）。
    """

    at: float                      # 绝对秒
    duration: float                # 停留时长（含进出场）
    rect: tuple[float, float, float, float]
    label: str = ""
    note: str = ""
    zoom: float | None = None      # 不给就按框宽自动算，让框正好铺满放大卡

    @property
    def is_fraction(self) -> bool:
        return all(0.0 <= v <= 1.0 for v in self.rect)


@dataclass
class ScrollSpec:
    """长截图在视窗里走多远。

    end 是**图片自身高度的比例**：0.45 = 整张图上移了自身高度的 45%。
    选这个单位是因为它不依赖视窗多大，作者拿图片高度一除就能估出来。
    不给就走到底（图片底边对齐视窗底边）。
    """

    end: float | None = None
    aspect: float = 1.45      # 视窗宽高比，同时写进 inline style，只有这一个出处


@dataclass
class TerminalSpec:
    """终端画面：提示符 + 敲出来的命令 + 随后出现的输出。"""

    prompt: str = "$"
    command: str = ""
    output: list[str] = field(default_factory=list)


@dataclass
class PlannedScene:
    """编译后的场景：所有时间都是绝对秒。"""

    scene_id: str
    order: int
    start: float
    duration: float
    layout: str
    transition: str
    text_main: str
    text_sub: str
    animation: str
    narration: str
    bgm_mood: str
    visual_desc: str
    bullets: list[str] = field(default_factory=list)
    compare: tuple[str, str] | None = None
    stat_value: str = ""
    stat_unit: str = ""
    focus: list[FocusSpec] = field(default_factory=list)
    window: str = ""                       # 画成浏览器/窗口标题栏的那行字，空则不画
    scroll: ScrollSpec | None = None
    terminal: TerminalSpec | None = None
    captions: list[CaptionCue] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class Timeline:
    scenes: list[PlannedScene]
    total_duration: float

    @property
    def captions(self) -> list[CaptionCue]:
        return [c for s in self.scenes for c in s.captions]


# ── 文本切分 ──────────────────────────────────────────────────────


def split_caption_text(text: str, max_chars: int = CAPTION_MAX_CHARS) -> list[str]:
    """把旁白切成适合竖屏单行显示的片段。

    三级降级：句号级 → 逗号级 → 硬切。
    任何一级都可能切不动（比如一整句没有标点的长句），所以必须有硬切兜底，
    否则会出现一行 60 字糊满屏幕。
    """
    text = (text or "").strip()
    if not text:
        return []

    def _refine(
        chunks: list[str],
        splitter: re.Pattern[str] | None,
        hard: bool = False,
    ) -> list[str]:
        """按 splitter 细分过长的块。

        hard=False 时切不动就**原样留着**，交给下一级去切——
        只有最后一级才允许硬切。否则句号级切不动的长句会直接被按字数截断，
        标点级根本轮不上，结果就是 "说AI已经能写大" 这种切在词中间的字幕。
        """
        out: list[str] = []
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            if len(chunk) <= max_chars:
                out.append(chunk)
                continue
            if splitter is not None:
                parts = [p.strip() for p in splitter.split(chunk) if p.strip()]
                if len(parts) > 1:
                    out.extend(parts)
                    continue
            if not hard:
                out.append(chunk)
                continue
            out.extend(
                chunk[i : i + max_chars] for i in range(0, len(chunk), max_chars)
            )
        return out

    chunks = _refine([text], _SENTENCE_SPLIT)
    chunks = _refine(chunks, _CLAUSE_SPLIT)
    chunks = _refine(chunks, None, hard=True)
    return _merge_orphans([c for c in chunks if c], max_chars)


def _merge_orphans(chunks: list[str], max_chars: int, min_chars: int = 4) -> list[str]:
    """把过短的尾巴并回上一条。

    切分在边界上很容易掉出一个只剩 '。' 或两三个字的碎片，
    单独占一整条字幕会闪一下就没，很跳。允许轻微超长换取不闪。
    """
    out: list[str] = []
    for chunk in chunks:
        meaningful = any(c.isalnum() or "\u4e00" <= c <= "\u9fff" for c in chunk)
        too_short = len(chunk) < min_chars or not meaningful
        if out and too_short and len(out[-1]) + len(chunk) <= max_chars + 4:
            out[-1] += chunk
            continue
        out.append(chunk)
    return out


def build_captions(
    narration: str,
    start: float,
    duration: float,
    marks: list[dict[str, Any]] | None = None,
) -> list[CaptionCue]:
    """把旁白切成字幕条并排上时间。

    有 marks（TTS 回吐的句级真实时间）时以它为准：每一句都钉在人声真正
    开口的那一刻，误差不会跨句累积。没有 marks 才退回按字数比例估算。

    这个区别在长片上是决定性的：估算法下每句几百毫秒的偏差会一路累加，
    看起来就是"字幕追不上人声"。
    """
    if marks:
        cues = _captions_from_marks(narration, start, duration, marks)
        if cues:
            return cues

    pieces = split_caption_text(narration)
    if not pieces:
        return []

    weights = [max(1, len(p)) for p in pieces]
    total_w = sum(weights)

    cues: list[CaptionCue] = []
    cursor = start
    for piece, w in zip(pieces, weights):
        span = duration * (w / total_w)
        cues.append(CaptionCue(start=cursor, end=cursor + span, text=piece))
        cursor += span
    # 吸附末尾，避免浮点累积误差让最后一条字幕早退/超出场景
    if cues:
        cues[-1].end = start + duration
    return cues


def _captions_from_marks(
    narration: str,
    start: float,
    duration: float,
    marks: list[dict[str, Any]],
) -> list[CaptionCue]:
    """用引擎回吐的句级时间排字幕。

    一句话仍然可能超过单行字数上限，此时在**这一句自己的时间区间内**
    按字数二次切分——误差被关在一句之内（通常 2-4 秒），不会外溢。
    """
    cues: list[CaptionCue] = []
    for mark in marks:
        text = (mark.get("text") or "").strip()
        if not text:
            continue
        m_start = start + float(mark.get("start") or 0.0)
        m_dur = float(mark.get("duration") or 0.0)
        if m_dur <= 0:
            continue

        pieces = split_caption_text(text)
        if not pieces:
            continue

        weights = [max(1, len(p)) for p in pieces]
        total_w = sum(weights)
        cursor = m_start
        for piece, w in zip(pieces, weights):
            span = m_dur * (w / total_w)
            cues.append(CaptionCue(start=cursor, end=cursor + span, text=piece))
            cursor += span

    if not cues:
        return []

    # 最后一条不要超出场景（尾部还有留白），也不要早退太多
    cues[-1].end = min(max(cues[-1].end, cues[-1].start + 0.4), start + duration)
    return cues


# ── 版面推断 ──────────────────────────────────────────────────────

_NUMERIC = re.compile(r"^\s*([+\-]?[\d,]+(?:\.\d+)?\s*[%倍x×]?)\s*(.*)$")
_QUOTED = re.compile(r"[「『\"“].+[」』\"”]")
_VS = re.compile(r"\s*(?:vs\.?|VS\.?|对比|相比)\s*", re.IGNORECASE)
_BULLET_SEP = re.compile(r"[；;｜|]|(?<=[一-鿿])、")


def infer_layout(scene: dict[str, Any], index: int, total: int) -> str:
    """场景没显式给 layout 时，按内容特征推断。

    顺序有讲究：先看位置（首尾），再看内容特征，最后兜底 statement。
    """
    explicit = (scene.get("layout") or "").strip().lower()
    if explicit in LAYOUTS:
        return explicit

    overlay = scene.get("text_overlay") or {}
    if isinstance(overlay, str):
        overlay = {"main": overlay, "sub": ""}
    main = (overlay.get("main") or "").strip()
    sub = (overlay.get("sub") or "").strip()

    # focus 是硬信号：作者已经量好了要框哪块，比"它排在第几个"更能说明意图。
    # 所以它排在首尾规则前面——开场用一张截图特写钩人是很常见的写法。
    if scene.get("focus"):
        return "screenshot"
    if scene.get("scroll"):
        return "scroll"
    if scene.get("terminal"):
        return "terminal"
    if index == 0:
        return "hook"
    if index == total - 1:
        return "outro"
    if _VS.search(main) or _VS.search(sub):
        return "compare"
    if _NUMERIC.match(main) or overlay.get("animation") == "counter":
        return "stat"
    if _QUOTED.search(main) or _QUOTED.search(sub):
        return "quote"
    if len([p for p in _BULLET_SEP.split(sub) if p.strip()]) >= 2:
        return "bullets"
    return "statement"


def _split_bullets(sub: str) -> list[str]:
    return [p.strip() for p in _BULLET_SEP.split(sub or "") if p.strip()]


def _split_compare(main: str, sub: str) -> tuple[tuple[str, str], str] | None:
    """拆出对比双方，并告诉调用方它是从 main 还是 sub 里拆出来的。

    来源很重要：从 main 拆出来的话，main 就不能再当标题用了，
    否则「A 对比 B」会同时出现在标题和两张卡片里，一屏三份同样的字。
    """
    for source, text in (("main", main), ("sub", sub)):
        parts = [p.strip() for p in _VS.split(text or "") if p.strip()]
        if len(parts) == 2:
            return (parts[0], parts[1]), source
    return None


def _plan_scroll(raw: Any) -> ScrollSpec:
    """解析滚动配置。给了非法值就退回默认，不炸——手写脚本的硬校验在 seed 那边做。"""
    spec = ScrollSpec()
    if not isinstance(raw, dict):
        return spec
    end = raw.get("end")
    if isinstance(end, (int, float)) and 0 < float(end) <= 1:
        spec.end = float(end)
    aspect = raw.get("aspect")
    if isinstance(aspect, (int, float)) and float(aspect) > 0:
        spec.aspect = float(aspect)
    return spec


def _plan_terminal(raw: Any, fallback_command: str) -> TerminalSpec:
    """解析终端配置。命令不给就用主标题——绝大多数时候这俩就是同一句话。"""
    spec = TerminalSpec(command=fallback_command)
    if not isinstance(raw, dict):
        return spec
    spec.prompt = str(raw.get("prompt") or "$").strip() or "$"
    cmd = raw.get("command")
    if cmd:
        spec.command = str(cmd).strip()
    out = raw.get("output")
    if isinstance(out, list):
        spec.output = [str(x) for x in out if str(x).strip()]
    elif isinstance(out, str) and out.strip():
        spec.output = [out.strip()]
    return spec


def _plan_focus(
    raw: list[Any] | None, start: float, duration: float,
) -> list[FocusSpec]:
    """把场景里的 focus 列表编译成绝对时间的特写点。

    只给 at、不给 hold 时，停留到**下一个特写点出现**为止；最后一个停到场景结束前
    0.3 秒。这样作者只要排好出现顺序就行，不用手算每个框停多久——
    改一句旁白导致场景变长时，最后一个特写会自己跟着延长。
    """
    if not raw:
        return []

    items: list[dict[str, Any]] = []
    for it in raw:
        if not isinstance(it, dict):
            continue
        rect = it.get("rect")
        if not (isinstance(rect, (list, tuple)) and len(rect) == 4):
            continue
        try:
            rect_f = tuple(float(v) for v in rect)
        except (TypeError, ValueError):
            continue
        if rect_f[2] <= 0 or rect_f[3] <= 0:
            continue
        items.append({"raw": it, "rect": rect_f})

    if not items:
        return []

    # 没给 at 就按剩余时长均分，保证先来后到
    n = len(items)
    for i, it in enumerate(items):
        at = it["raw"].get("at")
        it["at"] = float(at) if at is not None else (duration * 0.18) + i * (
            duration * 0.72 / n
        )
    items.sort(key=lambda x: x["at"])

    out: list[FocusSpec] = []
    for i, it in enumerate(items):
        nxt = items[i + 1]["at"] if i + 1 < n else max(duration - 0.3, it["at"] + 0.6)
        hold = it["raw"].get("hold")
        span = float(hold) if hold is not None else max(0.6, nxt - it["at"])
        zoom = it["raw"].get("zoom")
        out.append(FocusSpec(
            at=start + it["at"],
            duration=span,
            rect=it["rect"],  # type: ignore[arg-type]
            label=str(it["raw"].get("label") or "").strip(),
            note=str(it["raw"].get("note") or "").strip(),
            zoom=float(zoom) if zoom else None,
        ))
    return out


def _split_stat(main: str) -> tuple[str, str]:
    """把 '87% 的人' 拆成 ('87%', '的人')。拆不出就整串当数值。"""
    m = _NUMERIC.match(main or "")
    if not m:
        return main or "", ""
    return m.group(1).strip(), m.group(2).strip()


# ── 编译 ──────────────────────────────────────────────────────────


def plan_timeline(scenes: list[dict[str, Any]]) -> Timeline:
    """把 Stage2 的场景列表编译成带绝对时间的 Timeline。"""
    planned: list[PlannedScene] = []
    cursor = 0.0
    total = len(scenes)

    for i, scene in enumerate(scenes):
        overlay = scene.get("text_overlay") or {}
        if isinstance(overlay, str):
            overlay = {"main": overlay, "sub": "", "animation": "pop_in"}

        main = (overlay.get("main") or "").strip()
        sub = (overlay.get("sub") or "").strip()
        duration = float(scene.get("duration") or 5)
        layout = infer_layout(scene, i, total)
        narration = (scene.get("narration") or "").strip()

        ps = PlannedScene(
            scene_id=scene.get("scene_id") or f"s{i + 1:02d}",
            order=int(scene.get("order") or i + 1),
            start=cursor,
            duration=duration,
            layout=layout,
            transition=(scene.get("transition") or "fade").strip(),
            text_main=main,
            text_sub=sub,
            animation=(overlay.get("animation") or "pop_in").strip(),
            narration=narration,
            bgm_mood=(scene.get("bgm_mood") or "explain").strip(),
            visual_desc=(scene.get("visual_desc") or "").strip(),
            captions=build_captions(
                narration, cursor, duration, scene.get("speech_marks"),
            ),
            window=str(scene.get("window") or "").strip(),
            raw=scene,
        )

        if layout == "bullets":
            ps.bullets = _split_bullets(sub) or _split_bullets(main)
        elif layout == "compare":
            found = _split_compare(main, sub)
            if found is None:               # 推断失败就退回普通陈述
                ps.layout = "statement"
            else:
                ps.compare, source = found
                # 标题取「没被拆成卡片」的那一半，避免同一句话出现三次
                ps.text_main = sub if source == "main" else main
        elif layout == "screenshot":
            ps.focus = _plan_focus(scene.get("focus"), cursor, duration)
        elif layout == "scroll":
            ps.scroll = _plan_scroll(scene.get("scroll"))
            # 滚动版面也吃 focus，但只出放大卡不出高亮框：
            # 画面一直在动，框跟不上（要跟就得逐帧算位置，那就不是纯 CSS 了）
            ps.focus = _plan_focus(scene.get("focus"), cursor, duration)
        elif layout == "terminal":
            ps.terminal = _plan_terminal(scene.get("terminal"), main)
        elif layout == "stat":
            ps.stat_value, ps.stat_unit = _split_stat(main)
            # stat 版面的巨号字是给数字用的。硬塞一整句中文进去会撑破版心，
            # 所以拆不出数字就退回普通陈述。
            if not any(c.isdigit() for c in ps.stat_value):
                ps.layout = "statement"
                ps.stat_value = ps.stat_unit = ""

        planned.append(ps)
        cursor += duration

    return Timeline(scenes=planned, total_duration=cursor)
