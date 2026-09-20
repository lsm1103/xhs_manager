"""Stage 5: 视频录制 — 将 HTML 组合渲染为 MP4 视频文件。

渲染方案:
  render_mode=auto 时自动选择:
    - mashup/commentary 类型 → MoneyPrinterTurbo 一站式生成（快速）
    - explainer/data_viz 类型 → HTML 组合渲染（创意灵活）

  HTML 渲染优先级:
    1. HyperFrames CLI (`hf render`)
    2. Playwright + ffmpeg

  MoneyPrinterTurbo 快速路径:
    直接用脚本旁白 + Pexels 素材生成完整视频
"""

import logging
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id, utcnow
from xhs_manager.video_pipeline.config import VideoPipelineSettings
from xhs_manager.video_pipeline.domain import StageError, VideoType
from xhs_manager.video_pipeline.integrations.moneyprinter import MoneyPrinterTurbo
from xhs_manager.video_pipeline.models import (
    VideoComposition,
    VideoPipelineRun,
    VideoRender,
    VideoScript,
    VideoTopic,
)

logger = logging.getLogger(__name__)


def render_videos(
    session: Session,
    run: VideoPipelineRun,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """渲染所有视频。根据 render_mode 选择 HTML 渲染或 MoneyPrinterTurbo 快速路径。"""

    compositions = (
        session.query(VideoComposition)
        .join(VideoScript, VideoComposition.script_id == VideoScript.id)
        .join(VideoTopic, VideoScript.topic_id == VideoTopic.id)
        .filter(
            VideoTopic.pipeline_run_id == run.id,
            # error 是上一次渲染失败留下的。失败状态现在会当场落库
            # （不再靠整段事务回滚），重跑必须能把它们捡回来，
            # 否则一次瞬时失败就永久卡住这支片子。
            VideoComposition.status.in_(("render_ready", "error")),
        )
        .all()
    )

    if not compositions:
        raise StageError("render_videos", "没有就绪的 HTML 组合")

    mpt = MoneyPrinterTurbo(settings.moneyprinter_path)
    renders_completed = 0
    results: list[dict] = []

    for comp in compositions:
        render = None
        try:
            start_time = time.monotonic()

            # 获取关联的脚本和选题信息
            script = session.get(VideoScript, comp.script_id)
            topic = session.get(VideoTopic, script.topic_id) if script else None

            # 创建渲染记录
            render = VideoRender(
                id=new_id(),
                composition_id=comp.id,
                fps=settings.render_fps,
                status="rendering",
                started_at=utcnow(),
            )
            session.add(render)
            # 立刻提交，不要攥着写锁去渲染。
            # SQLite 全库只有一把写锁，从第一条 INSERT 到 commit 之间它一直
            # 属于这个连接；而下面一支片子要渲好几分钟，期间 worker 心跳想
            # UPDATE work_items 只会等满 busy_timeout 然后报 database is locked。
            # 写库只在真正写的那一瞬间持锁。
            session.commit()

            # 根据 render_mode 选择渲染路径
            output_path = None
            render_method = "html"

            use_mpt = _should_use_moneyprinter(
                settings.render_mode, topic, mpt.available,
            )

            if use_mpt and script:
                # MoneyPrinterTurbo 快速路径
                output_path = _render_with_moneyprinter(
                    mpt, script, comp, settings,
                )
                if output_path:
                    render_method = "moneyprinter"

            if not output_path:
                # HTML 渲染路径
                output_path = _render_html_to_mp4(
                    comp, render, settings,
                    scenes=list(script.scenes) if script else None,
                )

            if output_path and output_path.exists():
                render_time = time.monotonic() - start_time
                file_size = output_path.stat().st_size

                from xhs_manager.video_pipeline.integrations.renderer import probe_duration

                render.output_path = str(output_path)
                render.file_size = file_size
                # 以成片真实时长为准（旁白比脚本短时 -shortest 会截断）
                render.duration = probe_duration(output_path) or comp.total_duration
                render.render_time = round(render_time, 2)
                render.status = "completed"
                render.completed_at = utcnow()

                # 生成封面图
                covers = _generate_covers(output_path, comp, settings)
                render.cover_path = covers.get("default")
                render.covers = covers

                comp.status = "rendered"
                renders_completed += 1
                results.append({
                    "topic": topic.title[:30] if topic else "?",
                    "render_id": render.id,
                    "render_method": render_method,
                    "output_path": str(output_path),
                    "file_size_mb": round(file_size / 1024 / 1024, 2),
                    "duration": render.duration,
                    "script_duration": comp.total_duration,
                    "render_time": round(render_time, 1),
                })
            else:
                render.status = "failed"
                render.error_detail = "渲染输出文件不存在"
                comp.status = "error"

        except Exception as e:
            logger.error("组合 %s 渲染失败: %s", comp.id, e)
            # 失败有可能来自数据库本身，先把会话清干净再写失败状态
            session.rollback()
            if render is not None:
                render.status = "failed"
                render.error_detail = str(e)[:2000]
            comp.status = "error"

        # 渲染跑完（成功或失败）才回填结果——又是一次短事务
        session.commit()

    if renders_completed == 0:
        raise StageError("render_videos", "所有视频渲染均失败")

    return {
        "renders_completed": renders_completed,
        "renders": results,
    }


def _render_html_to_mp4(
    comp: VideoComposition,
    render: VideoRender,
    settings: VideoPipelineSettings,
    scenes: list[dict[str, Any]] | None = None,
) -> Path | None:
    """用 Playwright + ffmpeg 把 HTML 组合渲染成 MP4。"""
    from xhs_manager.video_pipeline.integrations.renderer import HtmlVideoRenderer

    comp_dir = Path(comp.composition_dir)
    output_path = comp_dir / "video.mp4"
    width, height = settings.render_resolution.split("x")

    renderer = HtmlVideoRenderer(
        width=int(width), height=int(height), fps=settings.render_fps,
    )
    if not renderer.available():
        logger.error("渲染环境不可用（缺 playwright / Chrome / ffmpeg）")
        return None

    # 音轨：优先用「分段 BGM + 旁白 ducking」的混音，退化为裸旁白
    narration = next(iter(comp_dir.glob("assets/*narration*.mp3")), None)
    audio_path = _build_soundtrack(comp, scenes, narration, settings) or narration

    return renderer.render(
        html_path=Path(comp.html_path),
        output_path=output_path,
        duration=comp.total_duration,
        audio_path=audio_path,
    )



def _build_soundtrack(
    comp: VideoComposition,
    scenes: list[dict[str, Any]] | None,
    narration: Path | None,
    settings: VideoPipelineSettings,
) -> Path | None:
    """按场景情绪生成分段 BGM 并与旁白混音。失败返回 None（回落到裸旁白）。"""
    if not settings.bgm_enabled or not scenes:
        return None

    from xhs_manager.video_pipeline.audio.composer import compose_soundtrack
    from xhs_manager.video_pipeline.audio.library import MusicLibrary

    try:
        lib = MusicLibrary(
            dirs=[Path(d) for d in settings.bgm_dirs],
            cache_path=Path(settings.bgm_index_path),
        )
        if lib.load() == 0:
            logger.warning("曲库为空，跳过 BGM")
            return None

        out, segs = compose_soundtrack(
            scenes=scenes, library=lib,
            work_dir=Path(comp.composition_dir) / "audio",
            total_duration=float(comp.total_duration),
            narration_path=narration,
        )
        if out:
            logger.info(
                "配乐完成: %d 段 (%s)",
                len(segs), " → ".join(s.mood.value for s in segs),
            )
        return out
    except Exception as e:
        logger.warning("配乐失败，回落到裸旁白: %s", e)
        return None


def _generate_covers(
    video_path: Path,
    comp: VideoComposition,
    settings: VideoPipelineSettings,
) -> dict[str, str]:
    """从视频抽帧生成各平台封面。"""
    from xhs_manager.video_pipeline.integrations.renderer import extract_covers

    return extract_covers(video_path, video_path.parent)


# ═══════════════════════════════════════════════════════════════════
# MoneyPrinterTurbo 快速路径
# ═══════════════════════════════════════════════════════════════════


def _should_use_moneyprinter(
    render_mode: str,
    topic: "VideoTopic | None",
    mpt_available: bool,
) -> bool:
    """判断是否应该使用 MoneyPrinterTurbo 快速路径。"""
    if not mpt_available:
        return False
    if render_mode == "moneyprinter":
        return True
    if render_mode == "html":
        return False
    # auto 模式：混剪和评论型走 MPT，讲解和数据可视化走 HTML
    if render_mode == "auto" and topic:
        return topic.video_type in (
            VideoType.MASHUP.value,
            VideoType.COMMENTARY.value,
        )
    # 默认走 MPT（因为它更成熟稳定）
    return True


def _render_with_moneyprinter(
    mpt: MoneyPrinterTurbo,
    script: "VideoScript",
    comp: "VideoComposition",
    settings: "VideoPipelineSettings",
) -> Path | None:
    """使用 MoneyPrinterTurbo 从脚本直接生成完整视频。"""

    # 拼接旁白脚本
    narration_parts = []
    for scene in script.scenes:
        narration = scene.get("narration", "")
        if narration:
            narration_parts.append(narration)

    if not narration_parts:
        logger.warning("脚本无旁白内容，无法使用 MoneyPrinterTurbo 渲染")
        return None

    full_script = "\n\n".join(narration_parts)

    # 提取搜索关键词
    search_terms = []
    for scene in script.scenes:
        for hint in scene.get("material_hints", []):
            if hint.startswith("search:"):
                search_terms.append(hint[7:].strip())

    logger.info("MoneyPrinterTurbo 渲染: 脚本 %d 字, %d 个搜索词",
                len(full_script), len(search_terms))

    result = mpt.generate_from_script(
        script=full_script,
        search_terms=search_terms if search_terms else None,
        video_aspect="9:16",
        voice_name=settings.tts_voice.replace("Neural", "Neural-Female"),
        transition_mode="shuffle",
    )

    if result.get("success") and result.get("video_path"):
        src_video = Path(result["video_path"])
        if src_video.exists():
            # 复制到我们的输出目录
            output_dir = Path(comp.composition_dir)
            dst_video = output_dir / "video.mp4"
            shutil.copy2(str(src_video), str(dst_video))
            logger.info("MoneyPrinterTurbo 渲染成功: %s (%.1f MB)",
                        dst_video, dst_video.stat().st_size / 1024 / 1024)
            return dst_video

    logger.warning("MoneyPrinterTurbo 渲染失败: %s",
                    result.get("error", "未知错误")[:200])
    return None
