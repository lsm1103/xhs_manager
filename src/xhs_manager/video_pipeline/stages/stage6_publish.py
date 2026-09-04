"""Stage 6: 多平台发布 — 将渲染好的视频发布到小红书/抖音/B站/X。

发布方式:
  - 小红书: OpenCLI (`opencli xiaohongshu save_draft`)
  - 抖音/B站/X: ego-browser 浏览器自动化
"""

import logging
import subprocess
import time
from pathlib import Path
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

                # 执行发布
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

                # 平台间发布间隔：只在**成功发布后**等待。
                # 失败（如扩展未连接）立即返回，否则 3 视频×失败 会白等 15 分钟。
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


XHS_VIDEO_PUBLISH_URL = (
    "https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch"
)


def _xhs_upload_plan(
    session: str,
    video_path: str,
    title: str,
    content: str,
    mode: str,
) -> list[list[str]]:
    """生成小红书视频上传的 opencli browser 命令序列（纯函数，便于测试）。

    背景：`opencli xiaohongshu publish` 只支持 --images 图文笔记，**没有视频参数**，
    所以视频必须走创作者中心的 UI 自动化。步骤用语义定位器（role/name/text），
    比 CSS 选择器抗改版。
    """
    b = ["opencli", "browser", session]
    steps = [
        b + ["open", XHS_VIDEO_PUBLISH_URL],
        b + ["wait", "text", "上传视频", "--timeout", "20000"],
        # 文件输入通常是隐藏的 <input type=file>，按 CSS 直接挂文件最稳
        b + ["upload", "input[type=file]", video_path],
        # 等转码/上传完成：标题框出现即视为可编辑
        b + ["wait", "selector", "input[placeholder*='标题']", "--timeout", "180000"],
        b + ["fill", "--role", "textbox", "--name", "标题", title],
        b + ["fill", "--role", "textbox", "--name", "正文", content],
    ]
    if mode == "publish":
        steps.append(b + ["click", "--role", "button", "--name", "发布"])
        steps.append(b + ["wait", "text", "发布成功", "--timeout", "30000"])
    else:
        steps.append(b + ["click", "--role", "button", "--name", "暂存离线"])
        steps.append(b + ["wait", "time", "2"])
    return steps


def _publish_xiaohongshu(
    video_path: str,
    title: str,
    description: str,
    tags: list[str],
    cover_path: str | None,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """通过 opencli browser 自动化把视频存入小红书创作者中心（草稿或发布）。

    ⚠️ 尚未在真实浏览器上验证（依赖 OpenCLI Chrome 扩展）。
    失败时自动截图到视频同目录，便于对照页面调整定位器。
    """
    tags_str = " ".join(f"#{t}" for t in tags)
    content = f"{description}\n\n{tags_str}" if tags else description
    mode = settings.xhs_publish_mode if settings.xhs_publish_mode in ("draft", "publish") else "draft"
    session_name = settings.xhs_browser_session

    steps = _xhs_upload_plan(session_name, video_path, title, content, mode)
    for i, cmd in enumerate(steps, 1):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=240)
        except subprocess.TimeoutExpired:
            return {"success": False, "error": f"第 {i} 步超时: {' '.join(cmd[3:6])}"}
        if r.returncode != 0:
            shot = _xhs_debug_screenshot(session_name, video_path, i)
            return {
                "success": False,
                "error": (
                    f"第 {i} 步失败 ({' '.join(cmd[3:6])}): "
                    f"{(r.stderr or r.stdout)[-400:]}"
                    + (f" | 截图: {shot}" if shot else "")
                ),
            }

    logger.info("小红书视频已%s: %s", "发布" if mode == "publish" else "存草稿", title[:30])
    return {
        "success": True,
        "external_id": "draft" if mode == "draft" else None,
        "method": f"opencli_browser_{mode}",
    }


def _xhs_debug_screenshot(session: str, video_path: str, step: int) -> str | None:
    """失败时截图，落在视频同目录，方便人工对照页面调定位器。"""
    try:
        out = str(Path(video_path).with_name(f"xhs_publish_fail_step{step}.png"))
        subprocess.run(
            ["opencli", "browser", session, "screenshot", out],
            capture_output=True, timeout=30,
        )
        return out if Path(out).exists() else None
    except Exception:
        return None


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
