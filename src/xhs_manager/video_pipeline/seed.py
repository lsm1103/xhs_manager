"""种子数据 — 手写选题和脚本，用于在不调用 LLM 的情况下验证下游阶段。

用途:
  1. LLM 限流/不可用时，仍能测试 Stage3-6
  2. 作为单元测试的夹具
  3. 提供脚本 schema 的标准范例
"""

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id
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

    return data


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
