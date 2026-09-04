"""Stage 1: 热点采集 — 从小红书/抖音/B站/X 并行采集当日热门话题。

采集策略:
  1. 对每个平台，使用预设关键词搜索当日热门内容
  2. 提取标题、摘要、热度指标、标签
  3. 用 content_digest 去重
  4. 存入 video_trend_signals 表
"""

import hashlib
import json
import logging
import subprocess
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id, utcnow
from xhs_manager.video_pipeline.config import VideoPipelineSettings
from xhs_manager.video_pipeline.domain import Platform, StageError
from xhs_manager.video_pipeline.models import VideoPipelineRun, VideoTrendSignal

logger = logging.getLogger(__name__)

# ── 各平台搜索命令模板 ───────────────────────────────────────────

# 使用 opencli 进行各平台搜索
PLATFORM_SEARCH_COMMANDS: dict[str, str] = {
    "xiaohongshu": 'opencli xiaohongshu search "{query}" --limit {limit} --sort hot --json',
    "douyin": 'opencli douyin search "{query}" --limit {limit} --sort hot --json',
    "bilibili": 'opencli bilibili search "{query}" --limit {limit} --sort hot --json',
    "twitter": 'opencli twitter search "{query}" --limit {limit} --sort top --json',
}

# 平台 fallback：如果 opencli 不支持某个平台，使用 agent-reach 的搜索方式
FALLBACK_SEARCH_TEMPLATE = (
    'opencli search "{query}" --platform {platform} --limit {limit} --json'
)


def collect_trends(
    session: Session,
    run: VideoPipelineRun,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """采集各平台热点，返回采集统计。"""
    total_collected = 0
    total_deduplicated = 0
    platform_stats: dict[str, dict] = {}

    for platform_name in settings.trend_platforms:
        try:
            signals = _collect_platform(
                platform_name,
                settings.trend_keywords,
                settings.trends_per_platform,
            )
            saved, duped = _save_signals(session, run.id, platform_name, signals)
            total_collected += saved
            total_deduplicated += duped
            platform_stats[platform_name] = {
                "collected": saved,
                "deduplicated": duped,
                "total_raw": len(signals),
            }
            logger.info(
                "平台 %s: 采集 %d 条，去重 %d 条",
                platform_name, saved, duped,
            )
        except Exception as e:
            logger.warning("平台 %s 采集失败: %s", platform_name, e)
            platform_stats[platform_name] = {
                "collected": 0,
                "error": str(e)[:500],
            }

    # 更新运行记录
    run.trend_count = total_collected

    if total_collected == 0:
        raise StageError("collect_trends", "所有平台均未采集到热点信号")

    return {
        "total_collected": total_collected,
        "total_deduplicated": total_deduplicated,
        "platforms": platform_stats,
    }


def _collect_platform(
    platform: str,
    keywords: list[str],
    limit_per_keyword: int,
) -> list[dict[str, Any]]:
    """调用 opencli 搜索某个平台的热门内容。"""
    all_results: list[dict[str, Any]] = []

    for keyword in keywords:
        cmd_template = PLATFORM_SEARCH_COMMANDS.get(platform, FALLBACK_SEARCH_TEMPLATE)
        cmd = cmd_template.format(
            query=keyword,
            platform=platform,
            limit=limit_per_keyword,
        )

        try:
            result = subprocess.run(
                cmd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=60,
            )

            if result.returncode != 0:
                logger.warning(
                    "搜索命令失败 [%s/%s]: %s",
                    platform, keyword, result.stderr[:200],
                )
                continue

            # 尝试解析 JSON 输出
            output = result.stdout.strip()
            if not output:
                continue

            try:
                data = json.loads(output)
                if isinstance(data, list):
                    all_results.extend(data)
                elif isinstance(data, dict) and "items" in data:
                    all_results.extend(data["items"])
                elif isinstance(data, dict) and "results" in data:
                    all_results.extend(data["results"])
            except json.JSONDecodeError:
                # 如果不是 JSON，尝试按行解析
                for line in output.split("\n"):
                    line = line.strip()
                    if line:
                        try:
                            all_results.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue

        except subprocess.TimeoutExpired:
            logger.warning("搜索超时: %s/%s", platform, keyword)
        except Exception as e:
            logger.warning("搜索异常: %s/%s — %s", platform, keyword, e)

    return all_results


def _save_signals(
    session: Session,
    pipeline_run_id: str,
    platform: str,
    raw_signals: list[dict[str, Any]],
) -> tuple[int, int]:
    """将原始搜索结果标准化并存入数据库，返回 (保存数, 去重数)。"""
    saved = 0
    deduplicated = 0

    for raw in raw_signals:
        normalized = _normalize_signal(platform, raw)
        if not normalized:
            continue

        # 检查是否已存在（content_digest 去重）
        exists = (
            session.query(VideoTrendSignal.id)
            .filter(
                VideoTrendSignal.pipeline_run_id == pipeline_run_id,
                VideoTrendSignal.content_digest == normalized["content_digest"],
            )
            .first()
        )
        if exists:
            deduplicated += 1
            continue

        signal = VideoTrendSignal(
            id=new_id(),
            pipeline_run_id=pipeline_run_id,
            platform=platform,
            source_url=normalized["source_url"],
            title=normalized["title"],
            summary=normalized["summary"],
            heat_score=normalized.get("heat_score"),
            engagement=normalized.get("engagement", {}),
            author=normalized.get("author"),
            tags=normalized.get("tags", []),
            content_digest=normalized["content_digest"],
            collected_at=utcnow(),
        )
        session.add(signal)
        saved += 1

    return saved, deduplicated


def _normalize_signal(
    platform: str, raw: dict[str, Any]
) -> dict[str, Any] | None:
    """将各平台的原始数据标准化为统一格式。"""
    if not isinstance(raw, dict):
        return None

    # 尝试提取关键字段（兼容不同平台的字段名）
    title = (
        raw.get("title")
        or raw.get("name")
        or raw.get("desc", "")[:100]
        or raw.get("text", "")[:100]
    )
    if not title:
        return None

    summary = (
        raw.get("summary")
        or raw.get("description")
        or raw.get("desc")
        or raw.get("text", "")
        or raw.get("content", "")
        or title
    )

    source_url = (
        raw.get("url")
        or raw.get("link")
        or raw.get("source_url")
        or raw.get("share_url")
        or f"https://{platform}.com/unknown"
    )

    # 计算内容摘要（用于去重）
    digest_input = f"{platform}:{title}:{summary[:200]}"
    content_digest = hashlib.sha256(digest_input.encode()).hexdigest()[:64]

    # 提取互动数据
    engagement = {}
    for key in ("likes", "like_count", "digg_count", "favorite_count"):
        if key in raw:
            engagement["likes"] = raw[key]
            break
    for key in ("comments", "comment_count"):
        if key in raw:
            engagement["comments"] = raw[key]
            break
    for key in ("shares", "share_count", "forward_count", "retweet_count"):
        if key in raw:
            engagement["shares"] = raw[key]
            break
    for key in ("views", "view_count", "play_count"):
        if key in raw:
            engagement["views"] = raw[key]
            break

    # 计算热度分（标准化到 0-100）
    heat_score = raw.get("heat_score") or raw.get("hot_value")
    if heat_score is None:
        total_engagement = sum(
            int(v) for v in engagement.values() if isinstance(v, (int, float))
        )
        # 简单热度计算：log scale
        import math

        heat_score = min(100, math.log10(max(1, total_engagement)) * 20)

    # 提取标签
    tags = raw.get("tags") or raw.get("hashtags") or raw.get("topics") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    return {
        "source_url": source_url,
        "title": title[:500],
        "summary": summary[:2000],
        "heat_score": float(heat_score) if heat_score else None,
        "engagement": engagement,
        "author": raw.get("author") or raw.get("user", {}).get("name"),
        "tags": tags[:20],
        "content_digest": content_digest,
    }
