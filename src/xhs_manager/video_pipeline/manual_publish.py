"""人工发布：不经审批排期，直接备好一条待人工发布的记录。

为什么可以跳过审批
------------------
审批和排期这道闸门是给「自动操作真实账号」设的：到点由 worker 驱动浏览器
把片子推出去，这件事有对外后果，所以要有人按一下、要有时间窗口、要能收回。

人工模式没有这层后果。控制台一行浏览器代码都不碰，它只是把文案和成片摆
出来，发是人自己去小红书发的。让这件事也去排队等一个时间点，等来的只是
同一份清单——闸门拦不住任何东西，只是让「我已发布」这个按钮没地方落。

所以这里给人工模式开一条直路：渲染完成就能落一条 awaiting_manual 记录。

平台字段的计算（标题截断、标签上限）和 stage6 共用同一份实现，不另写一
套——两处算出不同的标题，是迟早会发生的事。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import DomainError, new_id
from xhs_manager.video_pipeline.config import VideoPipelineSettings
from xhs_manager.video_pipeline.domain import (
    PLATFORM_LIMITS,
    Platform,
    PublishMethod,
)
from xhs_manager.video_pipeline.models import (
    VideoPublication,
    VideoRender,
    VideoScript,
    VideoTopic,
)


class ManualPublishError(DomainError):
    code = "VIDEO_MANUAL_PUBLISH_ERROR"


def platform_fields(
    script: VideoScript,
    topic: VideoTopic,
    render: VideoRender,
    platform: Platform,
) -> dict[str, Any]:
    """按平台限额算出要提交的标题、正文、标签和封面。"""
    meta = (script.platform_metadata or {}).get(platform.value, {}) or {}
    limits = PLATFORM_LIMITS.get(platform, {})

    title = (meta.get("title") or topic.title)[: limits.get("title_max", 100)]

    desc = meta.get("desc", meta.get("description", "")) or ""
    desc_max = limits.get("desc_max", 1000)
    if desc_max > 0:
        desc = desc[:desc_max]

    tags = meta.get("tags", meta.get("hashtags", [])) or []
    tags = list(tags)[: limits.get("tags_max", 10)]

    covers = render.covers or {}
    return {
        "title": title,
        "description": desc,
        "tags": tags,
        "cover_path": covers.get(platform.value) or render.cover_path,
    }


def is_manual(platform: Platform, settings: VideoPipelineSettings) -> bool:
    """这个平台是不是走人工发布。

    目前只有小红书有自动化实现，也只有它有这个开关；
    其余平台的自动发布根本没实现，一律按人工处理，
    好过让它们在队列里反复失败。
    """
    if platform is Platform.XIAOHONGSHU:
        return settings.xhs_publish_mode not in ("draft", "publish")
    return platform is not Platform.XIAOHONGSHU


def ensure_manual_publication(
    session: Session,
    topic: VideoTopic,
    settings: VideoPipelineSettings,
    *,
    platform: Platform = Platform.XIAOHONGSHU,
) -> VideoPublication:
    """确保这条选题有一条可供人工发布的记录，返回它。

    已有记录直接复用——包括自动发布失败留下的那条：人接手发的还是同一支
    片子，不该再造一条记录让清单上出现两份。
    """
    from xhs_manager.video_pipeline import promote

    if not is_manual(platform, settings):
        raise ManualPublishError(
            f"{platform.value} 当前是自动发布模式，要走审批与排期"
        )

    script = promote.latest_script(session, topic.id)
    if script is None:
        raise ManualPublishError("这条选题还没有脚本")
    render = promote.render_for(session, script)
    if render is None or render.status != "completed" or not render.output_path:
        raise ManualPublishError("还没有渲染完成的成片，没法发布")

    existing = (
        session.query(VideoPublication)
        .filter(
            VideoPublication.render_id == render.id,
            VideoPublication.platform == platform.value,
        )
        .first()
    )
    if existing is not None:
        return existing

    fields = platform_fields(script, topic, render, platform)
    pub = VideoPublication(
        id=new_id(),
        render_id=render.id,
        topic_id=topic.id,
        platform=platform.value,
        publish_method=PublishMethod.MANUAL.value,
        status="awaiting_manual",
        **fields,
    )
    session.add(pub)
    session.flush()
    return pub
