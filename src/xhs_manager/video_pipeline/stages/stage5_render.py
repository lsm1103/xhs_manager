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
            VideoComposition.status == "render_ready",
        )
        .all()
    )

    if not compositions:
        raise StageError("render_videos", "没有就绪的 HTML 组合")

    mpt = MoneyPrinterTurbo(settings.moneyprinter_path)
    renders_completed = 0
    results: list[dict] = []

    for comp in compositions:
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
            session.flush()

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
                output_path = _render_html_to_mp4(comp, render, settings)

            if output_path and output_path.exists():
                render_time = time.monotonic() - start_time
                file_size = output_path.stat().st_size

                render.output_path = str(output_path)
                render.file_size = file_size
                render.duration = comp.total_duration
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
                    "duration": comp.total_duration,
                    "render_time": round(render_time, 1),
                })
            else:
                render.status = "failed"
                render.error_detail = "渲染输出文件不存在"
                comp.status = "error"

        except Exception as e:
            logger.error("组合 %s 渲染失败: %s", comp.id, e)
            if render:
                render.status = "failed"
                render.error_detail = str(e)[:2000]
            comp.status = "error"

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
) -> Path | None:
    """选择最优渲染方案将 HTML 转换为 MP4。"""

    output_dir = Path(comp.composition_dir)
    output_path = output_dir / "video.mp4"

    # 方案 1: HyperFrames CLI
    if shutil.which("hf"):
        try:
            result = _render_with_hyperframes(comp, output_path, settings)
            if result:
                return result
        except Exception as e:
            logger.warning("HyperFrames 渲染失败，回退到 Playwright: %s", e)

    # 方案 2: Playwright 截帧 + ffmpeg 合成
    try:
        result = _render_with_playwright_ffmpeg(comp, output_path, settings)
        if result:
            return result
    except Exception as e:
        logger.error("Playwright + ffmpeg 渲染失败: %s", e)

    return None


def _render_with_hyperframes(
    comp: VideoComposition,
    output_path: Path,
    settings: VideoPipelineSettings,
) -> Path | None:
    """使用 HyperFrames CLI 渲染。"""
    cmd = [
        "hf", "render",
        str(comp.composition_dir),
        "--output", str(output_path),
        "--fps", str(settings.render_fps),
        "--width", settings.render_resolution.split("x")[0],
        "--height", settings.render_resolution.split("x")[1],
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode == 0 and output_path.exists():
        logger.info("HyperFrames 渲染成功: %s", output_path)
        return output_path

    logger.warning("HyperFrames 渲染失败: %s", result.stderr[:500])
    return None


def _render_with_playwright_ffmpeg(
    comp: VideoComposition,
    output_path: Path,
    settings: VideoPipelineSettings,
) -> Path | None:
    """使用 Playwright 逐帧截图 + ffmpeg 合成视频。"""
    width, height = settings.render_resolution.split("x")
    fps = settings.render_fps
    total_duration = comp.total_duration
    total_frames = int(total_duration * fps)

    # 创建帧输出目录
    frames_dir = Path(comp.composition_dir) / "frames"
    frames_dir.mkdir(exist_ok=True)

    # 1. 使用 Playwright 逐帧截图
    # 生成 Node.js 渲染脚本
    render_script = _generate_frame_capture_script(
        html_path=comp.html_path,
        frames_dir=str(frames_dir),
        width=int(width),
        height=int(height),
        fps=fps,
        total_frames=total_frames,
        total_duration=total_duration,
    )

    script_path = Path(comp.composition_dir) / "capture_frames.mjs"
    script_path.write_text(render_script, encoding="utf-8")

    logger.info(
        "开始逐帧截图: %d 帧 @ %dfps, 预计 %ds",
        total_frames, fps, total_duration,
    )

    result = subprocess.run(
        ["node", str(script_path)],
        capture_output=True,
        text=True,
        timeout=600,  # 10分钟超时
        cwd=str(Path(comp.composition_dir)),
    )

    if result.returncode != 0:
        logger.error("帧截图失败: %s", result.stderr[:500])
        return None

    # 检查帧文件数量
    frame_files = sorted(frames_dir.glob("frame_*.png"))
    if len(frame_files) < total_frames * 0.8:
        logger.warning(
            "帧文件不足: 预期 %d, 实际 %d",
            total_frames, len(frame_files),
        )

    # 2. 用 ffmpeg 合成视频
    # 收集音频文件
    audio_files = list(Path(comp.composition_dir).rglob("*_narration.mp3"))
    audio_args = []
    if audio_files:
        # 简单方案：合并所有音频
        concat_audio = Path(comp.composition_dir) / "narration_concat.mp3"
        if len(audio_files) == 1:
            concat_audio = audio_files[0]
        else:
            _concat_audio_files(audio_files, concat_audio)
        audio_args = ["-i", str(concat_audio), "-c:a", "aac", "-b:a", "192k"]

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-framerate", str(fps),
        "-i", str(frames_dir / "frame_%05d.png"),
        *audio_args,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-preset", "medium",
        "-crf", "23",
        "-t", str(total_duration),
        str(output_path),
    ]

    result = subprocess.run(
        ffmpeg_cmd,
        capture_output=True,
        text=True,
        timeout=300,
    )

    if result.returncode == 0 and output_path.exists():
        logger.info("ffmpeg 合成成功: %s", output_path)
        # 清理帧文件
        shutil.rmtree(frames_dir, ignore_errors=True)
        script_path.unlink(missing_ok=True)
        return output_path

    logger.error("ffmpeg 合成失败: %s", result.stderr[:500])
    return None


def _generate_frame_capture_script(
    html_path: str,
    frames_dir: str,
    width: int,
    height: int,
    fps: int,
    total_frames: int,
    total_duration: float,
) -> str:
    """生成逐帧截图的 Node.js 脚本。"""
    return f"""\
const pw = require('playwright-core');
const path = require('path');

(async () => {{
  const browser = await pw.chromium.launch({{
    executablePath: '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
    headless: true,
  }});

  const page = await browser.newPage();
  await page.setViewportSize({{ width: {width}, height: {height} }});
  await page.goto('file://{html_path}', {{ waitUntil: 'networkidle' }});

  // 等待字体加载
  await page.waitForTimeout(1000);

  const fps = {fps};
  const totalFrames = {total_frames};
  const totalDuration = {total_duration};
  const frameInterval = 1000 / fps;  // ms per frame

  // 暂停自动播放，改为手动控制时间
  await page.evaluate(() => {{
    window.__frameMode = true;
  }});

  for (let i = 0; i < totalFrames; i++) {{
    const elapsed = (i / fps) * 1000;  // 当前时间（毫秒）

    // 通过 JS 设置当前帧的时间，触发场景切换
    await page.evaluate((ms) => {{
      const elapsed = ms / 1000;
      const clips = document.querySelectorAll('.clip');
      let cumulative = 0;
      let activeIndex = -1;

      clips.forEach((clip, idx) => {{
        const dur = parseFloat(clip.dataset.duration || 5);
        if (elapsed >= cumulative && elapsed < cumulative + dur) {{
          activeIndex = idx;
        }}
        cumulative += dur;
      }});

      // 如果超出所有场景，激活最后一个
      if (activeIndex === -1 && clips.length > 0) {{
        activeIndex = clips.length - 1;
      }}

      clips.forEach((clip, idx) => {{
        clip.classList.remove('active', 'exiting');
        if (idx === activeIndex) {{
          clip.classList.add('active');
          clip.style.opacity = '1';
        }} else {{
          clip.style.opacity = '0';
        }}
      }});
    }}, elapsed);

    const framePath = path.join('{frames_dir}', `frame_${{String(i).padStart(5, '0')}}.png`);
    await page.screenshot({{ path: framePath, type: 'png' }});

    if (i > 0 && i % (fps * 5) === 0) {{
      console.log(`进度: ${{i}}/${{totalFrames}} (${{Math.round(i/totalFrames*100)}}%)`);
    }}
  }}

  await browser.close();
  console.log(`完成: 共 ${{totalFrames}} 帧`);
}})();
"""


def _concat_audio_files(audio_files: list[Path], output: Path) -> None:
    """用 ffmpeg 拼接多个音频文件。"""
    concat_list = output.parent / "audio_concat.txt"
    with open(concat_list, "w") as f:
        for af in sorted(audio_files):
            f.write(f"file '{af.resolve()}'\n")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            str(output),
        ],
        capture_output=True,
        timeout=60,
    )
    concat_list.unlink(missing_ok=True)


def _generate_covers(
    video_path: Path,
    comp: VideoComposition,
    settings: VideoPipelineSettings,
) -> dict[str, str]:
    """从视频中提取封面图，为各平台生成不同尺寸。"""
    covers: dict[str, str] = {}
    output_dir = video_path.parent

    # 默认封面：视频第 1 秒的截图
    default_cover = output_dir / "cover_default.jpg"
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(video_path),
                "-ss", "1",
                "-vframes", "1",
                "-q:v", "2",
                str(default_cover),
            ],
            capture_output=True,
            timeout=30,
        )
        if default_cover.exists():
            covers["default"] = str(default_cover)
    except Exception as e:
        logger.warning("封面截图失败: %s", e)

    # 各平台尺寸封面
    platform_sizes = {
        "xiaohongshu": (1080, 1440),   # 3:4
        "douyin": (1080, 1920),         # 9:16
        "bilibili": (1920, 1080),       # 16:9
        "twitter": (1920, 1080),        # 16:9
    }

    for platform, (w, h) in platform_sizes.items():
        cover_path = output_dir / f"cover_{platform}.jpg"
        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", str(default_cover) if default_cover.exists() else str(video_path),
                    *(["-ss", "1", "-vframes", "1"] if not default_cover.exists() else []),
                    "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                    "-q:v", "2",
                    str(cover_path),
                ],
                capture_output=True,
                timeout=30,
            )
            if cover_path.exists():
                covers[platform] = str(cover_path)
        except Exception as e:
            logger.debug("平台 %s 封面生成失败: %s", platform, e)

    return covers


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
