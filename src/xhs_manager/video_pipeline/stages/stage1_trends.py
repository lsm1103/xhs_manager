"""Stage 1: 热点采集 — 多后端采集各平台当日热门话题。

后端策略（见 integrations/collectors.py）:
  - B站 / V2EX: 公开 HTTP API，无需登录
  - 小红书 / 抖音 / X: opencli 浏览器通道，需 Chrome 扩展

单平台失败不阻塞其他平台；只要有一个平台采集到数据即视为成功。
"""

import hashlib
import logging
import math
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id, utcnow
from xhs_manager.video_pipeline.config import VideoPipelineSettings
from xhs_manager.video_pipeline.domain import StageError
from xhs_manager.video_pipeline.integrations.collectors import (
    TrendItem,
    collect_platform,
)
from xhs_manager.video_pipeline.models import VideoPipelineRun, VideoTrendSignal

logger = logging.getLogger(__name__)


def collect_trends(
    session: Session,
    run: VideoPipelineRun,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """采集各平台热点，返回采集统计。"""
    total_saved = 0
    total_duped = 0
    platform_stats: dict[str, dict] = {}

    for platform in settings.trend_platforms:
        try:
            items = collect_platform(
                platform,
                settings.trend_keywords,
                settings.trends_per_platform,
            )
            saved, duped = _save_items(session, run.id, items)
            total_saved += saved
            total_duped += duped
            platform_stats[platform] = {
                "collected": saved,
                "deduplicated": duped,
                "raw": len(items),
            }
            if saved:
                logger.info("平台 %s: 入库 %d 条（去重 %d）", platform, saved, duped)
            else:
                logger.warning("平台 %s: 未采集到数据", platform)
        except Exception as e:
            logger.warning("平台 %s 采集异常: %s", platform, e)
            platform_stats[platform] = {"collected": 0, "error": str(e)[:300]}

    run.trend_count = total_saved

    if total_saved == 0:
        available = ", ".join(settings.trend_platforms)
        raise StageError(
            "collect_trends",
            f"所有平台均未采集到热点信号（尝试的平台: {available}）。"
            f"若依赖 opencli 的平台不可用，请确认 Chrome 的 OpenCLI 扩展已启用。",
        )

    return {
        "total_collected": total_saved,
        "total_deduplicated": total_duped,
        "platforms": platform_stats,
    }


def _save_items(
    session: Session, pipeline_run_id: str, items: list[TrendItem],
) -> tuple[int, int]:
    """将采集条目写入数据库，返回 (入库数, 去重数)。

    热度分采用**平台内相对排名**：各平台互动量级差异巨大
    （B站播放量百万级，V2EX 回复数百级），绝对值无法横向比较。
    改为在平台内部按原始权重排名，映射到 0-100，
    这样每个平台的头部内容都能获得高分，供 Stage2 公平竞争。
    """
    saved = 0
    duped = 0

    # 先算每条的原始互动权重，再在平台内归一
    scored = [(item, _raw_weight(item)) for item in items if item.title]
    scores = _normalize_within_platform([w for _, w in scored])

    for (item, _), heat in zip(scored, scores):
        digest = _content_digest(item)

        exists = (
            session.query(VideoTrendSignal.id)
            .filter(
                VideoTrendSignal.pipeline_run_id == pipeline_run_id,
                VideoTrendSignal.content_digest == digest,
            )
            .first()
        )
        if exists:
            duped += 1
            continue

        session.add(VideoTrendSignal(
            id=new_id(),
            pipeline_run_id=pipeline_run_id,
            platform=item.platform,
            source_url=item.url or f"https://{item.platform}.com",
            title=item.title[:500],
            summary=(item.summary or item.title)[:2000],
            heat_score=heat,
            engagement=item.engagement,
            author=item.author,
            tags=[t for t in item.tags if t][:20],
            content_digest=digest,
            collected_at=utcnow(),
        ))
        saved += 1

    return saved, duped


def _content_digest(item: TrendItem) -> str:
    """内容摘要，用于同一次运行内去重。"""
    raw = f"{item.platform}:{item.title}:{item.summary[:200]}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


def _raw_weight(item: TrendItem) -> float:
    """单条内容的原始互动权重。

    评论 > 分享 > 点赞 > 播放：越"费力"的互动越能反映内容质量，
    播放量最易得（可能只是推荐位效果），权重最低。
    """
    return (
        item.comments * 5
        + item.shares * 4
        + item.likes * 3
        + item.views * 0.1
    )


def _normalize_within_platform(weights: list[float]) -> list[float]:
    """把一批（同平台）原始权重按排名映射到 0-100。

    用排名而非绝对值，避免头部内容因量级过大全部撞顶 100。
    最高分 100，最低分 10，中间线性分布；全部相同则都给 50。
    """
    n = len(weights)
    if n == 0:
        return []
    if n == 1:
        return [100.0 if weights[0] > 0 else 0.0]

    # 按权重升序排名（同值同名次）
    order = sorted(range(n), key=lambda i: weights[i])
    rank = [0] * n
    for pos, idx in enumerate(order):
        rank[idx] = pos

    lo, hi = min(weights), max(weights)
    if hi == lo:
        return [50.0] * n

    return [round(10.0 + (rank[i] / (n - 1)) * 90.0, 2) for i in range(n)]
