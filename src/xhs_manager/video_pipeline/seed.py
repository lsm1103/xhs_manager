"""种子数据 — 手写选题和脚本，用于在不调用 LLM 的情况下验证下游阶段。

用途:
  1. LLM 限流/不可用时，仍能测试 Stage3-6
  2. 作为单元测试的夹具
  3. 提供脚本 schema 的标准范例
"""

import json
import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id
from xhs_manager.video_pipeline.composition.timeline import LAYOUTS
from xhs_manager.video_pipeline.domain import VideoType
from xhs_manager.video_pipeline.models import (
    VideoPipelineRun,
    VideoScript,
    VideoTopic,
    VideoTrendSignal,
)

logger = logging.getLogger(__name__)


# 一份完整的示例脚本：6 场景 / 36 秒，覆盖全部转场和文字动画类型
SAMPLE_SCENES: list[dict[str, Any]] = [
    {
        "scene_id": "s01", "order": 1, "duration": 4,
        "visual_desc": "close-up of a person looking confused at a laptop screen full of AI chat messages",
        "text_overlay": {"main": "你还在复制粘贴 AI 回复？", "sub": "", "animation": "pop_in"},
        "transition": "none",
        "narration": "每天花两小时跟 AI 聊天，结果全丢在聊天记录里。",
        "material_hints": ["search:person confused laptop screen"],
    },
    {
        "scene_id": "s02", "order": 2, "duration": 6,
        "visual_desc": "abstract data flowing through digital pipelines, blue tones",
        "text_overlay": {"main": "问题不在 AI", "sub": "在于你没有把它变成流程", "animation": "slide_up"},
        "transition": "fade",
        "narration": "问题不在模型不够强，而在于你每次都从零开始描述需求。",
        "material_hints": ["search:data flow digital pipeline"],
    },
    {
        "scene_id": "s03", "order": 3, "duration": 7,
        "visual_desc": "four connected blocks forming a workflow diagram, clean minimal design",
        "text_overlay": {"main": "四个问题", "sub": "输入 · 步骤 · 验收 · 归档", "animation": "typewriter"},
        "transition": "slide_left",
        "narration": "把重复任务压缩成四个问题：输入是什么，分几步做，怎么算合格，结果存哪。",
        "material_hints": ["search:workflow diagram blocks"],
    },
    {
        "scene_id": "s04", "order": 4, "duration": 7,
        "visual_desc": "split screen comparison showing chaos versus organized system",
        "text_overlay": {"main": "2小时 → 15分钟", "sub": "同样的任务", "animation": "counter"},
        "transition": "zoom_in",
        "narration": "回答清楚这四个问题，同样的活儿从两小时压到十五分钟。",
        "material_hints": ["search:before after comparison productivity"],
    },
    {
        "scene_id": "s05", "order": 5, "duration": 7,
        "visual_desc": "automated assembly line with glowing nodes, futuristic",
        "text_overlay": {"main": "关键是可复用", "sub": "一次定义，永久使用", "animation": "highlight"},
        "transition": "blur",
        "narration": "更重要的是，这套流程写一次就能一直用，不用每次重新解释。",
        "material_hints": ["search:automation assembly line technology"],
    },
    {
        "scene_id": "s06", "order": 6, "duration": 5,
        "visual_desc": "person confidently working with multiple screens, warm lighting",
        "text_overlay": {"main": "从今天开始", "sub": "把聊天变成工作流", "animation": "pop_in"},
        "transition": "wipe",
        "narration": "别再把聊天记录当工作流，从今天开始给 AI 一份说明书。",
        "material_hints": ["search:productive person multiple monitors"],
    },
]

SAMPLE_PLATFORM_METADATA: dict[str, Any] = {
    "xiaohongshu": {
        "title": "别把聊天记录当工作流",
        "desc": "每天跟 AI 聊两小时，结果全丢在聊天记录里？\n\n"
                "问题不在模型不够强，在于你每次都从零开始描述需求。\n\n"
                "把重复任务压缩成四个问题：输入是什么 / 分几步做 / 怎么算合格 / 结果存哪。\n\n"
                "回答清楚这四个，同样的活儿从两小时压到十五分钟，而且写一次能一直用。",
        "tags": ["AI工作流", "AI效率工具", "提示词工程", "自动化", "AI实战"],
    },
    "douyin": {
        "title": "别把聊天记录当工作流：四个问题让 AI 效率翻八倍",
        "desc": "输入·步骤·验收·归档，把重复任务变成可复用流程",
        "tags": ["AI工具", "效率", "职场"],
    },
    "bilibili": {
        "title": "别把聊天记录当工作流 | 四个问题把 AI 对话变成可复用流程",
        "desc": "每天花两小时跟 AI 聊天，结果全丢在聊天记录里。\n"
                "本期讲清楚如何用四个问题把重复任务压缩成标准流程。",
        "tags": ["AI", "人工智能", "效率工具", "工作流", "科技"],
    },
    "twitter": {
        "text": "Stop treating chat logs as workflows.\n\n"
                "Compress repetitive tasks into 4 questions:\n"
                "→ What's the input?\n"
                "→ What are the steps?\n"
                "→ What counts as done?\n"
                "→ Where does it go?\n\n"
                "2 hours → 15 minutes.",
        "hashtags": ["AI", "productivity", "workflow"],
    },
}


REQUIRED_SCENE_KEYS = (
    "scene_id", "order", "duration", "visual_desc",
    "text_overlay", "transition", "narration",
)


def free_run_date(session: Session) -> date:
    """找一个还没被占用的 run_date。

    `video_pipeline_runs.run_date` 上有唯一约束——每天一次自动运行的设计。
    手动起片一天可能起好几条，所以从今天往**过去**找空位：
    往未来找会占掉后面几天定时任务的位置，往过去找只是借用没跑过的日子。
    """
    d = date.today()
    for _ in range(3650):
        exists = (
            session.query(VideoPipelineRun.id)
            .filter(VideoPipelineRun.run_date == d)
            .first()
        )
        if not exists:
            return d
        d = d - timedelta(days=1)
    raise RuntimeError("十年内找不到空闲的 run_date")


def load_script_file(path: str | Path) -> dict[str, Any]:
    """读入一份手写的脚本 JSON 并做基本校验。

    接受两种形态：完整脚本对象（含 scenes/topic 等），或裸的 scenes 数组。
    校验在这里做而不是等 Stage3 崩——流水线跑到一半才发现少字段最难查。
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {"scenes": data}

    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("脚本里没有 scenes，或 scenes 不是数组")

    for i, scene in enumerate(scenes):
        missing = [k for k in REQUIRED_SCENE_KEYS if k not in scene]
        if missing:
            raise ValueError(f"scenes[{i}] 缺字段: {', '.join(missing)}")
        if not isinstance(scene.get("duration"), (int, float)) or scene["duration"] <= 0:
            raise ValueError(f"scenes[{i}] 的 duration 必须是正数")
        scene.setdefault("bgm_mood", "explain")
        scene.setdefault("material_hints", [])
        _validate_hints(i, scene)
        _validate_layout(i, scene)
        _validate_focus(i, scene)
        _validate_scroll(i, scene)
        _validate_terminal(i, scene)

    return data


def _validate_hints(i: int, scene: dict[str, Any]) -> None:
    """素材提示的自洽性。

    "none"（不要背景图）和其它提示同时出现是自相矛盾的：运行期会以 none 为准，
    但作者显然不是这个意思，多半是改脚本时忘了删旧的那一行。
    静默取其一的话，你要渲染完整片才发现背景图没了。
    """
    # 函数内 import：seed 是给 CLI 和测试用的轻量模块，
    # 模块级依赖 stages 会把整条流水线的 import 链都拖进来。
    from xhs_manager.video_pipeline.stages.stage3_materials import NO_MATERIAL_HINT

    hints = scene.get("material_hints") or []
    if not isinstance(hints, list):
        raise ValueError(f"scenes[{i}] 的 material_hints 必须是数组")

    texts = [h.strip() for h in hints if isinstance(h, str) and h.strip()]
    if NO_MATERIAL_HINT in [t.lower() for t in texts] and len(texts) > 1:
        others = [t for t in texts if t.lower() != NO_MATERIAL_HINT]
        raise ValueError(
            f"scenes[{i}] 同时写了 \"none\"（不要背景图）和 {others}，"
            f"二选一"
        )


def _validate_layout(i: int, scene: dict[str, Any]) -> None:
    """版面名拼错要当场报错。

    infer_layout 的兜底是「不认识就当 statement」，对 LLM 产物来说这是对的
    （宁可退化也不要炸掉整条流水线）；但手写脚本不一样：把 "screenshot" 写成
    "screenshoot" 会安静地退回一屏纯文字，素材和高亮框全部不出，
    而你要渲染完整片才看得出来。
    """
    layout = scene.get("layout")
    if layout is None:
        return
    if layout not in LAYOUTS:
        raise ValueError(
            f"scenes[{i}] 的 layout 「{layout}」不认识，可选：{', '.join(LAYOUTS)}"
        )


def _validate_scroll(i: int, scene: dict[str, Any]) -> None:
    """滚动配置的取值范围。

    end 超出 (0, 1] 最坑：_plan_scroll 会当它没给、改成走到底，
    你以为只滚三分之一，成片里整页刷到了底。
    """
    scroll = scene.get("scroll")
    if scroll is None:
        return
    if not isinstance(scroll, dict):
        raise ValueError(f"scenes[{i}] 的 scroll 必须是对象")

    end = scroll.get("end")
    if end is not None:
        if not isinstance(end, (int, float)) or not 0 < float(end) <= 1:
            raise ValueError(
                f"scenes[{i}] 的 scroll.end 要在 (0, 1] 之间"
                f"（图片自身高度的比例），现在是 {end!r}"
            )
    aspect = scroll.get("aspect")
    if aspect is not None and (
        not isinstance(aspect, (int, float)) or float(aspect) <= 0
    ):
        raise ValueError(f"scenes[{i}] 的 scroll.aspect 必须是正数，现在是 {aspect!r}")

    has_media = any(
        isinstance(h, str) and h.startswith("local:")
        for h in scene.get("material_hints", [])
    )
    if not has_media:
        logger.warning(
            "scenes[%d] 是滚动版面却没有 local: 素材提示——"
            "滚动的会是一张搜索来的素材，多半不是你想要的", i,
        )


def _validate_terminal(i: int, scene: dict[str, Any]) -> None:
    """终端配置。命令为空时版面会整个退化成纯文字，必须拦住。"""
    term = scene.get("terminal")
    if term is None:
        return
    if not isinstance(term, dict):
        raise ValueError(f"scenes[{i}] 的 terminal 必须是对象")

    overlay = scene.get("text_overlay") or {}
    if isinstance(overlay, str):
        overlay = {"main": overlay}
    command = str(term.get("command") or overlay.get("main") or "").strip()
    if not command:
        raise ValueError(
            f"scenes[{i}] 的 terminal 没有命令：要么给 terminal.command，"
            f"要么在 text_overlay.main 里写"
        )

    out = term.get("output")
    if out is not None and not isinstance(out, (list, str)):
        raise ValueError(f"scenes[{i}] 的 terminal.output 必须是字符串或字符串数组")


def _validate_focus(i: int, scene: dict[str, Any]) -> None:
    """截图特写框的基本形状校验。

    同理：_plan_focus 会静默丢掉不合法的条目，那对手写脚本是最坏的反馈方式。
    """
    focus = scene.get("focus")
    if focus is None:
        return
    if not isinstance(focus, list) or not focus:
        raise ValueError(f"scenes[{i}] 的 focus 必须是非空数组")

    for j, item in enumerate(focus):
        where = f"scenes[{i}].focus[{j}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where} 必须是对象")
        rect = item.get("rect")
        if not (isinstance(rect, (list, tuple)) and len(rect) == 4):
            raise ValueError(f"{where} 缺 rect，或 rect 不是 [x, y, w, h] 四个数")
        try:
            vals = [float(v) for v in rect]
        except (TypeError, ValueError):
            raise ValueError(f"{where} 的 rect 里有非数字") from None
        if vals[2] <= 0 or vals[3] <= 0:
            raise ValueError(f"{where} 的 rect 宽高必须为正")

    has_local = any(
        isinstance(h, str) and h.startswith("local:")
        for h in scene.get("material_hints", [])
    )
    if not has_local:
        # 不是硬错误：素材也可能由别的途径进库（比如手工登记）。
        # 但 95% 的情况下这就是忘了写素材，而后果是框贴在一张 Pexels 素材上。
        logger.warning(
            "scenes[%d] 给了 focus 却没有 local: 素材提示——"
            "高亮框会贴到搜索来的素材上，多半不是你想要的", i,
        )


def seed_topic_and_script(
    session: Session,
    run: VideoPipelineRun,
    *,
    rank: int = 1,
    script_data: dict[str, Any] | None = None,
) -> tuple[VideoTopic, VideoScript]:
    """为指定运行插入一份选题 + 脚本。

    script_data 为空时用内置示例；传入时用它（load_script_file 的返回值），
    这样「手写脚本 → 直接出片」不必绕过 LLM 阶段去改代码。
    """
    signal_ids = [
        s.id for s in session.query(VideoTrendSignal.id)
        .filter(VideoTrendSignal.pipeline_run_id == run.id)
        .limit(3).all()
    ]

    data = script_data or {}
    scenes = data.get("scenes", SAMPLE_SCENES)
    meta = data.get("topic", {})
    total = data.get("total_duration") or sum(s["duration"] for s in scenes)

    topic = VideoTopic(
        id=new_id(),
        pipeline_run_id=run.id,
        rank=rank,
        title=meta.get("title", "别把聊天记录当工作流"),
        angle=meta.get("angle", "从「每次重新描述需求」这个具体痛点切入，给出四问框架"),
        why_now=meta.get(
            "why_now", "AI 工具普及后，多数人停留在对话阶段，没有沉淀成可复用流程",
        ),
        target_audience=meta.get("target_audience", "已经在用 AI 但效率没有质变的知识工作者"),
        video_type=meta.get("video_type", VideoType.EXPLAINER.value),
        estimated_duration=total,
        scores={"heat": 8, "uniqueness": 9, "visual": 8, "timeliness": 7, "platform_fit": 9},
        total_score=8.3,
        source_signal_ids=signal_ids,
        status="selected",
    )
    session.add(topic)
    session.flush()

    script = VideoScript(
        id=new_id(),
        topic_id=topic.id,
        version=1,
        total_duration=total,
        scenes=scenes,
        bgm_style=data.get("bgm_style", "轻快科技感，BPM 110-120"),
        platform_metadata=data.get("platform_metadata", SAMPLE_PLATFORM_METADATA),
        generation_model="seed",
        generation_prompt_hash="seed",
        status="ready",
    )
    session.add(script)
    session.flush()

    logger.info(
        "种子数据已插入: 选题「%s」, %d 场景, %d 秒",
        topic.title, len(scenes), total,
    )
    return topic, script
