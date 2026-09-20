"""Stage 6: 多平台发布 — 将渲染好的视频发布到小红书/抖音/B站/X。

发布方式:
  - 小红书: Playwright 独立 Chrome profile（见 integrations/xhs_publisher.py）
  - 抖音/B站/X: 尚未实现
"""

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id, utcnow
from xhs_manager.video_pipeline.config import VideoPipelineSettings
from xhs_manager.video_pipeline.domain import (
    PLATFORM_LIMITS,
    PLATFORM_PUBLISH_METHOD,
    Platform,
    PublishMethod,
    StageError,
)
from xhs_manager.video_pipeline.models import (
    VideoComposition,
    VideoPipelineRun,
    VideoPublication,
    VideoRender,
    VideoScript,
    VideoTopic,
)

logger = logging.getLogger(__name__)


def publish_videos(
    session: Session,
    run: VideoPipelineRun,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """将所有渲染完成的视频发布到各平台。"""

    renders = (
        session.query(VideoRender)
        .join(VideoComposition, VideoRender.composition_id == VideoComposition.id)
        .join(VideoScript, VideoComposition.script_id == VideoScript.id)
        .join(VideoTopic, VideoScript.topic_id == VideoTopic.id)
        .filter(
            VideoTopic.pipeline_run_id == run.id,
            VideoRender.status == "completed",
        )
        .all()
    )

    if not renders:
        raise StageError("publish_videos", "没有已完成渲染的视频")

    published_count = 0
    results: list[dict] = []

    # 浏览器类平台的预检只做一次（opencli doctor 约 8s）。
    # 扩展未连接时所有浏览器平台直接判失败，不进入会挂住的 browser 流程。
    for render in renders:
        # 获取关联的脚本和选题
        comp = session.get(VideoComposition, render.composition_id)
        if not comp:
            continue
        script = session.get(VideoScript, comp.script_id)
        if not script:
            continue
        topic = session.get(VideoTopic, script.topic_id)
        if not topic:
            continue

        # 对每个目标平台发布
        for platform_name in settings.publish_platforms:
            try:
                platform = Platform(platform_name)
            except ValueError:
                logger.warning("未知平台: %s", platform_name)
                continue

            # 检查是否已发布
            existing = (
                session.query(VideoPublication)
                .filter(
                    VideoPublication.render_id == render.id,
                    VideoPublication.platform == platform_name,
                )
                .first()
            )
            if existing and existing.status == "published":
                continue

            try:
                # 获取平台适配的元数据
                platform_meta = script.platform_metadata.get(platform_name, {})
                limits = PLATFORM_LIMITS.get(platform, {})

                title = platform_meta.get("title", topic.title)
                title = title[:limits.get("title_max", 100)]

                desc = platform_meta.get("desc", platform_meta.get("description", ""))
                desc_max = limits.get("desc_max", 1000)
                if desc_max > 0:
                    desc = desc[:desc_max]

                tags = platform_meta.get("tags", platform_meta.get("hashtags", []))
                tags_max = limits.get("tags_max", 10)
                tags = tags[:tags_max]

                # 获取平台封面
                cover_path = render.covers.get(platform_name, render.cover_path)

                # 创建发布记录
                pub = existing or VideoPublication(
                    id=new_id(),
                    render_id=render.id,
                    topic_id=topic.id,
                    platform=platform_name,
                    title=title,
                    description=desc,
                    tags=tags,
                    cover_path=cover_path,
                    publish_method=PLATFORM_PUBLISH_METHOD.get(
                        platform, PublishMethod.EGO_BROWSER
                    ).value,
                    status="uploading",
                )
                if not existing:
                    session.add(pub)
                session.flush()

                if _is_manual(platform, settings):
                    # 人工发布：文案和成片都已经备好，剩下的交给人。
                    # 这里一行浏览器代码都不能碰——自动化操作真实账号
                    # 已经导致过一次封号。
                    pub.status = "awaiting_manual"
                    pub.publish_method = "manual"
                    pub.error_detail = None
                    results.append({
                        "topic": topic.title[:30], "platform": platform_name,
                        "status": pub.status, "url": None, "error": None,
                    })
                    logger.info("等待人工发布: %s / %s", platform_name, title[:24])
                    continue

                publish_result = _publish_to_platform(
                    platform=platform,
                    video_path=render.output_path,
                    title=title,
                    description=desc,
                    tags=tags,
                    cover_path=cover_path,
                    settings=settings,
                )

                if publish_result.get("success"):
                    pub.status = "published"
                    pub.external_id = publish_result.get("external_id")
                    pub.external_url = publish_result.get("external_url")
                    pub.published_at = utcnow()
                    # 重试成功时必须清掉上一次的失败原因，
                    # 否则 published 记录会一直挂着过期的错误文本
                    pub.error_detail = None
                    published_count += 1
                else:
                    pub.status = "failed"
                    pub.error_detail = publish_result.get("error", "")[:2000]

                results.append({
                    "topic": topic.title[:30],
                    "platform": platform_name,
                    "status": pub.status,
                    "url": pub.external_url,
                    "error": pub.error_detail,
                })

                # 平台间发布间隔：只在**成功发布后**等待，失败立即返回。
                if pub.status == "published" and settings.publish_delay_minutes > 0:
                    time.sleep(settings.publish_delay_minutes * 60)

            except Exception as e:
                logger.error(
                    "视频 %s 发布到 %s 失败: %s",
                    topic.title[:20], platform_name, e,
                )
                results.append({
                    "topic": topic.title[:30],
                    "platform": platform_name,
                    "status": "error",
                    "error": str(e)[:200],
                })

    run.published_count = published_count

    return {
        "published_count": published_count,
        "total_attempts": len(results),
        "publications": results,
    }


def _is_manual(platform: Platform, settings: VideoPipelineSettings) -> bool:
    """这个平台是不是走人工发布。

    目前只有小红书有自动化实现，也只有它有这个开关；
    其余平台的自动发布根本没实现，一律按人工处理，
    好过让它们在队列里反复失败。
    """
    if platform is Platform.XIAOHONGSHU:
        return settings.xhs_publish_mode not in ("draft", "publish")
    return platform is not Platform.XIAOHONGSHU


def _publish_to_platform(
    platform: Platform,
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    cover_path: str | None,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """路由到各平台的具体发布实现。"""

    publishers = {
        Platform.XIAOHONGSHU: _publish_xiaohongshu,
        Platform.DOUYIN: _publish_douyin,
        Platform.BILIBILI: _publish_bilibili,
        Platform.TWITTER: _publish_twitter,
    }

    publisher = publishers.get(platform)
    if not publisher:
        return {"success": False, "error": f"不支持的平台: {platform.value}"}

    return publisher(
        video_path=video_path,
        title=title,
        description=description,
        tags=tags,
        cover_path=cover_path,
        settings=settings,
    )


def _publish_xiaohongshu(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    cover_path: str | None,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """用 Playwright 独立 profile 上传视频到小红书创作者中心。

    不走 opencli browser：它的 upload 依赖「点击 → fileChooser」，而小红书的
    file input 是隐藏的，Chrome 要求真实用户手势才开选择器，扩展合成点击不满足。
    """
    from pathlib import Path as _P

    from xhs_manager.video_pipeline.integrations.xhs_publisher import (
        DEFAULT_PROFILE_DIR,
        XhsPublisher,
    )

    tags_str = " ".join(f"#{t}" for t in tags)
    content = f"{description}\n\n{tags_str}" if tags else description
    mode = settings.xhs_publish_mode if settings.xhs_publish_mode in ("draft", "publish") else "draft"

    pub = XhsPublisher(
        profile_dir=_P(settings.xhs_profile_dir) if settings.xhs_profile_dir else DEFAULT_PROFILE_DIR,
        cdp_url=settings.xhs_cdp_url,
    )
    res = pub.publish_video(video_path=video_path, title=title, content=content, mode=mode)

    if res.success:
        return {"success": True, "external_id": mode, "method": f"playwright_{mode}"}
    return {
        "success": False,
        "error": res.error + (f" | 截图: {res.screenshot}" if res.screenshot else ""),
    }


def _publish_douyin(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    cover_path: str | None,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """通过浏览器自动化发布到抖音。"""
    # TODO: 使用 ego-browser 实现抖音创作者平台上传
    # 抖音创作者平台: https://creator.douyin.com/
    logger.info("抖音发布 [待实现]: %s", title[:30])
    return {
        "success": False,
        "error": "抖音发布尚未实现，需要 ego-browser 集成",
    }


def _publish_bilibili(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    cover_path: str | None,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """通过浏览器自动化发布到 B 站。"""
    # TODO: 使用 ego-browser 实现 B 站投稿
    # B 站创作中心: https://member.bilibili.com/
    logger.info("B站发布 [待实现]: %s", title[:30])
    return {
        "success": False,
        "error": "B站发布尚未实现，需要 ego-browser 集成",
    }


def _publish_twitter(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    cover_path: str | None,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """通过浏览器自动化发布到 X (Twitter)。"""
    # TODO: 使用 ego-browser 或 Twitter API 发布
    logger.info("X 发布 [待实现]: %s", title[:30])
    return {
        "success": False,
        "error": "X 发布尚未实现，需要 ego-browser 或 API 集成",
    }
