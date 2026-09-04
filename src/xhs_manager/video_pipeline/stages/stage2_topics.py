"""Stage 2: 选题 + 脚本生成 — 从热点信号中选出 3 个视频选题并生成分镜脚本。

通过 Claude Code headless 模式（`claude -p --json-schema`）调用，
走 Max 订阅通道，无需 API Key，响应严格匹配预定义 schema。

流程:
  1. 读取当次运行的所有热点信号
  2. 调用 Claude（结构化输出）进行选题评分
  3. 对每个选题调用 Claude（结构化输出）生成分镜脚本
  4. 各平台标题/说明/标签由 schema 约束格式
"""

import hashlib
import logging
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id, utcnow
from xhs_manager.video_pipeline.config import VideoPipelineSettings
from xhs_manager.video_pipeline.domain import StageError, VideoType
from xhs_manager.video_pipeline.integrations.llm_client import call_structured
from xhs_manager.video_pipeline.models import (
    VideoPipelineRun,
    VideoScript,
    VideoTopic,
    VideoTrendSignal,
)

logger = logging.getLogger(__name__)

# ── JSON Schema 定义 ─────────────────────────────────────────────

TOPIC_SELECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "topics": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "rank": {"type": "integer"},
                    "title": {"type": "string", "description": "视频标题"},
                    "angle": {"type": "string", "description": "切入角度说明"},
                    "why_now": {"type": "string", "description": "为什么现在做"},
                    "target_audience": {"type": "string", "description": "目标受众"},
                    "video_type": {
                        "type": "string",
                        "enum": ["explainer", "mashup", "data_viz", "commentary"],
                    },
                    "estimated_duration": {
                        "type": "integer",
                        "description": "预估时长（秒）",
                    },
                    "scores": {
                        "type": "object",
                        "properties": {
                            "heat": {"type": "number"},
                            "uniqueness": {"type": "number"},
                            "visual": {"type": "number"},
                            "timeliness": {"type": "number"},
                            "platform_fit": {"type": "number"},
                        },
                        "required": [
                            "heat", "uniqueness", "visual",
                            "timeliness", "platform_fit",
                        ],
                        "additionalProperties": False,
                    },
                    "source_signal_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                    "reasoning": {"type": "string"},
                },
                "required": [
                    "rank", "title", "angle", "why_now",
                    "target_audience", "video_type",
                    "estimated_duration", "scores",
                    "source_signal_ids", "reasoning",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["topics"],
    "additionalProperties": False,
}

SCRIPT_GENERATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "total_duration": {"type": "integer", "description": "总时长（秒）"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "scene_id": {"type": "string"},
                    "order": {"type": "integer"},
                    "duration": {"type": "integer", "description": "秒"},
                    "visual_desc": {"type": "string", "description": "画面描述"},
                    "text_overlay": {
                        "type": "object",
                        "properties": {
                            "main": {"type": "string"},
                            "sub": {"type": "string"},
                            "animation": {
                                "type": "string",
                                "enum": [
                                    "typewriter", "pop_in", "slide_up",
                                    "counter", "highlight", "none",
                                ],
                            },
                        },
                        "required": ["main", "sub", "animation"],
                        "additionalProperties": False,
                    },
                    "transition": {
                        "type": "string",
                        "enum": [
                            "fade", "slide_left", "slide_right",
                            "zoom_in", "zoom_out", "glitch",
                            "blur", "wipe", "flip", "none",
                        ],
                    },
                    "narration": {"type": "string", "description": "旁白文本"},
                    "bgm_mood": {
                        "type": "string",
                        "enum": ["hook", "explain", "tension", "reveal", "uplift", "closing"],
                        "description": "本场景的背景音乐情绪",
                    },
                    "material_hints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "素材搜索提示，格式: search:关键词 或 gen:生图prompt",
                    },
                },
                "required": [
                    "scene_id", "order", "duration",
                    "visual_desc", "text_overlay",
                    "transition", "narration", "material_hints", "bgm_mood",
                ],
                "additionalProperties": False,
            },
        },
        "bgm_style": {"type": "string", "description": "BGM 风格描述"},
        "platform_metadata": {
            "type": "object",
            "properties": {
                "xiaohongshu": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "≤20字"},
                        "desc": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "desc", "tags"],
                    "additionalProperties": False,
                },
                "douyin": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "≤55字"},
                        "desc": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "desc", "tags"],
                    "additionalProperties": False,
                },
                "bilibili": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "≤80字"},
                        "desc": {"type": "string"},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["title", "desc", "tags"],
                    "additionalProperties": False,
                },
                "twitter": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "≤280字符"},
                        "hashtags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["text", "hashtags"],
                    "additionalProperties": False,
                },
            },
            "required": ["xiaohongshu", "douyin", "bilibili", "twitter"],
            "additionalProperties": False,
        },
    },
    "required": ["total_duration", "scenes", "bgm_style", "platform_metadata"],
    "additionalProperties": False,
}

# ── 系统提示 ─────────────────────────────────────────────────────

TOPIC_SYSTEM_PROMPT = """\
你是一个短视频内容策划专家，擅长 AI 技术科普。
你需要从热门话题信号中筛选出最适合制作成 30-90 秒短视频的选题。

评分维度（每项 1-10 分）：
1. heat: 当前讨论热度和关注度
2. uniqueness: 是否有独特切入角度
3. visual: 是否容易用画面表达
4. timeliness: 话题的新鲜程度
5. platform_fit: 是否适合短视频平台

视频类型说明：
- explainer: 概念科普，适合用图表/动画讲解
- mashup: 多素材混剪，适合热点汇总
- data_viz: 数据可视化，适合趋势分析
- commentary: 评论型，适合热点评论"""

SCRIPT_SYSTEM_PROMPT = """\
你是一个短视频脚本创作专家，擅长 AI 技术科普短视频。

脚本要求：
1. 开头 3 秒必须有钩子（提问、数据冲击、悬念）
2. 每个场景 3-15 秒，画面描述要具体（用于搜索素材或生成图片）
3. 转场效果要多样但不花哨
4. 旁白要口语化，适合 TTS 朗读
5. material_hints **每个场景至少一条 "search:英文关键词"**（用于 Pexels 素材检索，必须是英文、2-5 个词的具体画面），可另加 "gen:英文生图描述"
6. **肖像权红线**：素材关键词绝不能索取可辨识个人的面部特写。
   禁用 close-up face / portrait / headshot / person smiling 这类词；
   改用 wide shot / crowd / silhouette / hands typing / over the shoulder /
   back view / blurred background people 等看不清脸的表达。
7. **每个场景标注 bgm_mood**，按该段的叙事功能选：
   hook=开场悬念 / explain=平稳讲解 / tension=问题矛盾 /
   reveal=数据揭示或转折 / uplift=积极展望 / closing=收尾总结
   相邻场景情绪相同就用同一个，音乐会自动合并成一段，不要频繁切换
8. 为每个平台生成适配的标题和标签"""


# ── 主入口 ────────────────────────────────────────────────────────


def select_topics(
    session: Session,
    run: VideoPipelineRun,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """选出 top N 选题并为每个生成分镜脚本。"""

    # 1. 读取热点信号
    signals = (
        session.query(VideoTrendSignal)
        .filter(VideoTrendSignal.pipeline_run_id == run.id)
        .order_by(VideoTrendSignal.heat_score.desc())
        .all()
    )

    if not signals:
        raise StageError("select_topics", "没有可用的热点信号")

    # 2. 调用结构化输出进行选题评分（走 claude -p 通道）
    signals_text = _format_signals_for_llm(signals)
    user_prompt = (
        f"以下是今天从多个平台采集到的热门话题信号：\n\n"
        f"{signals_text}\n\n"
        f"请从中筛选出 {settings.topics_per_run} 个最适合制作成短视频的选题。"
    )

    try:
        topics_data = call_structured(
            user_prompt,
            TOPIC_SELECTION_SCHEMA,
            system=TOPIC_SYSTEM_PROMPT,
            model=settings.claude_model,
        )
    except Exception as e:
        raise StageError("select_topics", f"选题评分调用失败: {e}") from e

    # 4. 保存选题
    topic_ids: list[str] = []
    for topic_item in topics_data.get("topics", [])[:settings.topics_per_run]:
        topic = VideoTopic(
            id=new_id(),
            pipeline_run_id=run.id,
            rank=topic_item["rank"],
            title=topic_item["title"],
            angle=topic_item["angle"],
            why_now=topic_item["why_now"],
            target_audience=topic_item["target_audience"],
            video_type=topic_item.get("video_type", VideoType.EXPLAINER.value),
            estimated_duration=topic_item.get("estimated_duration", 60),
            scores=topic_item.get("scores", {}),
            total_score=_calc_total_score(topic_item.get("scores", {})),
            source_signal_ids=topic_item.get("source_signal_ids", []),
            status="selected",
        )
        session.add(topic)
        topic_ids.append(topic.id)

    session.flush()

    # 5. 为每个选题生成脚本
    scripts_created = 0
    for topic_id in topic_ids:
        topic = session.get(VideoTopic, topic_id)
        if not topic:
            continue
        try:
            _generate_script(session, topic, settings)
            scripts_created += 1
        except Exception as e:
            logger.error("选题「%s」脚本生成失败: %s", topic.title[:20], e)
            topic.status = "script_failed"

    run.topic_count = len(topic_ids)

    if scripts_created == 0:
        raise StageError("select_topics", "所有选题的脚本生成均失败")

    return {
        "topics_selected": len(topic_ids),
        "scripts_created": scripts_created,
        "topics": [
            {"id": tid, "title": td["title"], "score": td.get("scores", {})}
            for tid, td in zip(topic_ids, topics_data.get("topics", []))
        ],
    }


# ── 脚本生成 ─────────────────────────────────────────────────────


def _generate_script(
    session: Session,
    topic: VideoTopic,
    settings: VideoPipelineSettings,
) -> VideoScript:
    """为单个选题生成分镜脚本，使用结构化输出。"""

    user_prompt = (
        f"为以下选题创作一个 {topic.estimated_duration} 秒的短视频分镜脚本：\n\n"
        f"选题: {topic.title}\n"
        f"切入角度: {topic.angle}\n"
        f"视频类型: {topic.video_type}\n"
        f"目标受众: {topic.target_audience}\n\n"
        f"时长要求: {settings.min_duration}-{settings.max_duration} 秒\n"
        f"场景数: 5-10 个"
    )

    prompt_hash = hashlib.sha256(user_prompt.encode()).hexdigest()[:32]

    script_data = call_structured(
        user_prompt,
        SCRIPT_GENERATION_SCHEMA,
        system=SCRIPT_SYSTEM_PROMPT,
        model=settings.claude_model,
    )

    script = VideoScript(
        id=new_id(),
        topic_id=topic.id,
        version=1,
        total_duration=script_data.get("total_duration", topic.estimated_duration),
        scenes=script_data.get("scenes", []),
        bgm_style=script_data.get("bgm_style"),
        platform_metadata=script_data.get("platform_metadata", {}),
        generation_model=settings.claude_model,
        generation_prompt_hash=prompt_hash,
        status="ready",
    )
    session.add(script)
    session.flush()

    logger.info(
        "脚本生成完成: 选题=「%s」, 场景=%d个, 时长=%ds",
        topic.title[:20],
        len(script.scenes),
        script.total_duration,
    )
    return script


# ── 辅助函数 ─────────────────────────────────────────────────────


def _format_signals_for_llm(signals: list[VideoTrendSignal]) -> str:
    """将热点信号格式化为 LLM 可读文本。"""
    lines = []
    for i, sig in enumerate(signals, 1):
        engagement_str = ", ".join(
            f"{k}={v}" for k, v in sig.engagement.items()
        )
        tags_str = ", ".join(sig.tags[:5]) if sig.tags else ""
        heat = f"{sig.heat_score:.1f}" if sig.heat_score else "N/A"
        lines.append(
            f"[{i}] (id={sig.id}) [{sig.platform}] {sig.title}\n"
            f"    摘要: {sig.summary[:200]}\n"
            f"    热度: {heat}  互动: {engagement_str}\n"
            f"    标签: {tags_str}"
        )
    return "\n\n".join(lines)


def _calc_total_score(scores: dict[str, Any]) -> float:
    """加权计算总分。"""
    weights = {
        "heat": 0.25,
        "uniqueness": 0.20,
        "visual": 0.25,
        "timeliness": 0.15,
        "platform_fit": 0.15,
    }
    total = 0.0
    for key, weight in weights.items():
        val = scores.get(key, 5)
        total += float(val) * weight
    return round(total, 2)
