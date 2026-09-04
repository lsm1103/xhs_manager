"""Stage 4: HTML 动态构建 — 将分镜脚本和素材组合成可播放的 HTML 视频组合。

核心思路:
  每个视频 = 一个 HTML 文件，每个场景 = 一个 clip div
  使用 CSS animation + JS 控制时序和转场
  HyperFrames 渲染或 Playwright + ffmpeg 录制
"""

import json
import logging
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id, utcnow
from xhs_manager.video_pipeline.config import VideoPipelineSettings
from xhs_manager.video_pipeline.domain import StageError, Transition, TextAnimation
from xhs_manager.video_pipeline.models import (
    VideoComposition,
    VideoMaterial,
    VideoPipelineRun,
    VideoScript,
    VideoTopic,
)

logger = logging.getLogger(__name__)


def compose_html(
    session: Session,
    run: VideoPipelineRun,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """为每个视频脚本构建 HTML 组合。"""

    scripts = (
        session.query(VideoScript)
        .join(VideoTopic, VideoScript.topic_id == VideoTopic.id)
        .filter(
            VideoTopic.pipeline_run_id == run.id,
            VideoScript.status == "ready",
        )
        .all()
    )

    if not scripts:
        raise StageError("compose_html", "没有可处理的视频脚本")

    compositions_created = 0
    results: list[dict] = []

    for script in scripts:
        try:
            # 获取该脚本的所有素材
            materials = (
                session.query(VideoMaterial)
                .filter(
                    VideoMaterial.script_id == script.id,
                    VideoMaterial.selected == True,
                )
                .all()
            )

            # 按 scene_id 索引素材
            material_map: dict[str, list[VideoMaterial]] = {}
            for m in materials:
                material_map.setdefault(m.scene_id, []).append(m)

            # 构建 HTML
            comp_dir = Path(settings.output_base_dir) / run.id / script.id / "composition"
            comp_dir.mkdir(parents=True, exist_ok=True)

            html_content = _build_composition_html(script, material_map, settings)
            html_path = comp_dir / "index.html"
            html_path.write_text(html_content, encoding="utf-8")

            # 复制/链接素材到组合目录
            _link_assets(material_map, comp_dir)

            # 收集使用的转场效果
            transitions_used = list({
                scene.get("transition", "none")
                for scene in script.scenes
            })

            # 检查是否有旁白和 BGM
            has_narration = any(
                m.material_type == "audio"
                for mats in material_map.values()
                for m in mats
            )

            composition = VideoComposition(
                id=new_id(),
                script_id=script.id,
                composition_dir=str(comp_dir),
                html_path=str(html_path),
                total_duration=float(script.total_duration),
                resolution=settings.render_resolution,
                transition_effects=transitions_used,
                has_narration=has_narration,
                has_bgm=bool(script.bgm_style),
                status="render_ready",
            )
            session.add(composition)
            compositions_created += 1

            topic = session.get(VideoTopic, script.topic_id)
            results.append({
                "topic": topic.title[:30] if topic else "?",
                "composition_id": composition.id,
                "html_path": str(html_path),
                "scenes": len(script.scenes),
                "duration": script.total_duration,
                "transitions": transitions_used,
            })

        except Exception as e:
            logger.error("脚本 %s HTML 构建失败: %s", script.id, e)

    if compositions_created == 0:
        raise StageError("compose_html", "所有视频的 HTML 构建均失败")

    return {
        "compositions_created": compositions_created,
        "compositions": results,
    }


def _build_composition_html(
    script: VideoScript,
    material_map: dict[str, list],
    settings: VideoPipelineSettings,
) -> str:
    """构建完整的 HTML 视频组合。"""
    width, height = settings.render_resolution.split("x")
    total_duration = script.total_duration

    # 构建场景 HTML
    scenes_html = []
    cumulative_time = 0.0

    for scene in script.scenes:
        scene_id = scene.get("scene_id", f"s{scene.get('order', 0):02d}")
        duration = scene.get("duration", 5)
        transition = scene.get("transition", "fade")
        text_overlay = scene.get("text_overlay", {})
        visual_desc = scene.get("visual_desc", "")

        # 查找该场景的素材
        scene_materials = material_map.get(scene_id, [])
        visual_material = None
        audio_material = None

        for m in scene_materials:
            if m.material_type == "audio":
                audio_material = m
            elif not visual_material:
                visual_material = m

        # 构建场景 div
        bg_style, media_html = _build_scene_media(visual_material)
        text_html = _build_text_overlay(text_overlay)
        transition_class = f"transition-{transition.replace('_', '-')}"

        scene_html = f"""\
    <div class="clip {transition_class}"
         id="{scene_id}"
         data-start="{cumulative_time:.1f}"
         data-duration="{duration}"
         style="{bg_style}">
      {media_html}
      <div class="scene-content">
        {text_html}
      </div>
    </div>"""

        scenes_html.append(scene_html)
        cumulative_time += duration

    scenes_block = "\n\n".join(scenes_html)

    # 整篇旁白作为单一音轨挂在 composition 上（不再分散到各场景）
    narration_tag = ""
    for mats in material_map.values():
        for m in mats:
            if m.material_type == "audio":
                narration_tag = (
                    f'<audio id="narration" src="assets/{Path(m.local_path).name}" '
                    f'preload="auto"></audio>'
                )
                break
        if narration_tag:
            break

    return f"""\
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width={width}, height={height}">
  <title>Video Composition</title>
  <style>
{_get_base_css(int(width), int(height))}
{_get_transition_css()}
{_get_animation_css()}
  </style>
</head>
<body>
  <div class="composition"
       data-duration="{total_duration}"
       data-fps="{settings.render_fps}"
       style="width: {width}px; height: {height}px;">

{scenes_block}

    {narration_tag}
  </div>

  <script>
{_get_playback_js()}
  </script>
</body>
</html>"""


def _build_scene_media(material) -> tuple[str, str]:
    """返回 (背景 CSS, 媒体元素 HTML)。

    视频素材用 <video> 标签（渲染时由 JS 精确 seek 到对应帧），
    图片用 background-image，都没有则回退渐变背景。
    """
    if material is not None:
        path = Path(material.local_path)
        suffix = path.suffix.lower()

        if suffix in (".mp4", ".webm", ".mov"):
            # muted + playsinline 保证无头浏览器能加载；
            # preload="auto" 让首帧尽快就绪，避免截图截到黑屏
            video = (
                f'<video class="bg-video" src="assets/{path.name}" '
                f'muted playsinline preload="auto"></video>'
            )
            return "", video

        if suffix in (".png", ".jpg", ".jpeg", ".webp"):
            return (
                f"background-image: url('assets/{path.name}'); "
                f"background-size: cover; background-position: center;",
                "",
            )

    return (
        "background: linear-gradient(135deg, "
        "#0f0c29 0%, #302b63 50%, #24243e 100%);",
        "",
    )


def _build_text_overlay(text_overlay: dict | str) -> str:
    """构建文字叠加 HTML。"""
    if isinstance(text_overlay, str):
        if text_overlay:
            return f'<h1 class="text-main animate-pop-in">{text_overlay}</h1>'
        return ""

    main = text_overlay.get("main", "")
    sub = text_overlay.get("sub", "")
    animation = text_overlay.get("animation", "pop_in")
    anim_class = f"animate-{animation.replace('_', '-')}"

    def wrap(text: str) -> str:
        # 打字机需要内层 span 承载 inline-block，其余动画直接作用于块元素
        return f"<span>{text}</span>" if animation == "typewriter" else text

    parts = []
    if main:
        parts.append(f'<h1 class="text-main {anim_class}">{wrap(main)}</h1>')
    if sub:
        parts.append(
            f'<p class="text-sub {anim_class}" '
            f'style="animation-delay: 0.3s">{wrap(sub)}</p>'
        )
    return "\n        ".join(parts)


def _link_assets(material_map: dict, comp_dir: Path) -> None:
    """将素材文件链接/复制到组合目录的 assets 子目录。"""
    assets_dir = comp_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    for scene_id, materials in material_map.items():
        for m in materials:
            src = Path(m.local_path)
            if src.exists():
                dst = assets_dir / src.name
                if not dst.exists():
                    try:
                        import shutil
                        shutil.copy2(str(src), str(dst))
                    except Exception as e:
                        logger.warning("素材复制失败 %s: %s", src, e)


def _get_base_css(width: int, height: int) -> str:
    """基础 CSS 样式。"""
    return f"""\
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}

    body {{
      width: {width}px;
      height: {height}px;
      overflow: hidden;
      background: #000;
      font-family: 'Noto Sans SC', 'PingFang SC', 'Helvetica Neue', sans-serif;
    }}

    .composition {{
      position: relative;
      width: {width}px;
      height: {height}px;
      overflow: hidden;
    }}

    .clip {{
      position: absolute;
      top: 0; left: 0;
      width: 100%;
      height: 100%;
      opacity: 0;
      display: flex;
      align-items: center;
      justify-content: center;
    }}

    .clip.active {{
      opacity: 1;
      z-index: 10;
    }}

    .clip.exiting {{
      z-index: 5;
    }}

    .scene-content {{
      position: relative;
      z-index: 2;
      width: 100%;
      padding: 60px 80px;
      text-align: center;
    }}

    .text-main {{
      font-size: 64px;
      font-weight: 700;
      color: #ffffff;
      line-height: 1.4;
      text-shadow: 0 2px 20px rgba(0,0,0,0.5);
      margin-bottom: 20px;
    }}

    .text-sub {{
      font-size: 36px;
      font-weight: 400;
      color: rgba(255,255,255,0.85);
      line-height: 1.5;
      text-shadow: 0 1px 10px rgba(0,0,0,0.4);
    }}

    .scene-audio {{
      display: none;
    }}

    /* 背景视频 */
    .clip video.bg-video {{
      position: absolute;
      top: 0; left: 0;
      width: 100%;
      height: 100%;
      object-fit: cover;
      z-index: 0;
    }}

    /* 暗层叠加，让文字更清晰 */
    .clip::after {{
      content: '';
      position: absolute;
      top: 0; left: 0;
      width: 100%;
      height: 100%;
      background: rgba(0, 0, 0, 0.35);
      z-index: 1;
    }}"""


def _get_transition_css() -> str:
    """转场效果 CSS。"""
    return """\
    /* ── 转场效果 ── */

    .transition-fade.active {
      animation: fadeIn 0.6s ease-out forwards;
    }
    .transition-fade.exiting {
      animation: fadeOut 0.6s ease-out forwards;
    }

    .transition-slide-left.active {
      animation: slideInLeft 0.5s ease-out forwards;
    }
    .transition-slide-left.exiting {
      animation: slideOutLeft 0.5s ease-out forwards;
    }

    .transition-slide-right.active {
      animation: slideInRight 0.5s ease-out forwards;
    }
    .transition-slide-right.exiting {
      animation: slideOutRight 0.5s ease-out forwards;
    }

    .transition-zoom-in.active {
      animation: zoomIn 0.6s ease-out forwards;
    }
    .transition-zoom-in.exiting {
      animation: zoomOut 0.4s ease-out forwards;
    }

    .transition-zoom-out.active {
      animation: zoomOutIn 0.6s ease-out forwards;
    }

    .transition-glitch.active {
      animation: glitchIn 0.4s steps(4) forwards;
    }

    .transition-blur.active {
      animation: blurIn 0.5s ease-out forwards;
    }
    .transition-blur.exiting {
      animation: blurOut 0.5s ease-out forwards;
    }

    .transition-wipe.active {
      animation: wipeIn 0.6s ease-out forwards;
    }

    .transition-flip.active {
      animation: flipIn 0.6s ease-out forwards;
    }

    .transition-none.active {
      opacity: 1;
    }

    @keyframes fadeIn { from { opacity: 0 } to { opacity: 1 } }
    @keyframes fadeOut { from { opacity: 1 } to { opacity: 0 } }
    @keyframes slideInLeft { from { transform: translateX(100%); opacity: 0 } to { transform: translateX(0); opacity: 1 } }
    @keyframes slideOutLeft { from { transform: translateX(0); opacity: 1 } to { transform: translateX(-100%); opacity: 0 } }
    @keyframes slideInRight { from { transform: translateX(-100%); opacity: 0 } to { transform: translateX(0); opacity: 1 } }
    @keyframes slideOutRight { from { transform: translateX(0); opacity: 1 } to { transform: translateX(100%); opacity: 0 } }
    @keyframes zoomIn { from { transform: scale(1.5); opacity: 0 } to { transform: scale(1); opacity: 1 } }
    @keyframes zoomOut { from { transform: scale(1); opacity: 1 } to { transform: scale(0.5); opacity: 0 } }
    @keyframes zoomOutIn { from { transform: scale(0.5); opacity: 0 } to { transform: scale(1); opacity: 1 } }
    @keyframes glitchIn {
      0% { opacity: 0; transform: translate(5px, -5px) skewX(5deg) }
      25% { opacity: 0.5; transform: translate(-3px, 3px) skewX(-3deg) }
      50% { opacity: 0.8; transform: translate(2px, -2px) skewX(2deg) }
      75% { opacity: 0.9; transform: translate(-1px, 1px) skewX(-1deg) }
      100% { opacity: 1; transform: translate(0, 0) skewX(0) }
    }
    @keyframes blurIn { from { opacity: 0; filter: blur(20px) } to { opacity: 1; filter: blur(0) } }
    @keyframes blurOut { from { opacity: 1; filter: blur(0) } to { opacity: 0; filter: blur(20px) } }
    @keyframes wipeIn {
      from { clip-path: inset(0 100% 0 0); opacity: 1 }
      to { clip-path: inset(0 0 0 0); opacity: 1 }
    }
    @keyframes flipIn {
      from { transform: perspective(800px) rotateY(-90deg); opacity: 0 }
      to { transform: perspective(800px) rotateY(0); opacity: 1 }
    }"""


def _get_animation_css() -> str:
    """文字动画 CSS。"""
    return """\
    /* ── 文字动画 ── */

    /* 打字机效果作用在内层 span 上。
       若直接给 h1/p 设 display:inline-block，主副标题会并排而非上下堆叠。 */
    .animate-typewriter > span {
      display: inline-block;
      overflow: hidden;
      white-space: nowrap;
      vertical-align: bottom;
      border-right: 3px solid rgba(255,255,255,0.85);
      animation: typing 1.8s steps(24) forwards,
                 blink-caret 0.75s step-end infinite;
      max-width: 100%;
    }

    .animate-pop-in {
      animation: popIn 0.5s cubic-bezier(0.68, -0.55, 0.265, 1.55) forwards;
      opacity: 0;
    }

    .animate-slide-up {
      animation: slideUp 0.6s ease-out forwards;
      opacity: 0;
    }

    .animate-counter {
      animation: popIn 0.3s ease-out forwards;
    }

    .animate-highlight {
      background: linear-gradient(120deg, transparent 0%, transparent 50%, #ffd700 50%, #ffd700 100%);
      background-size: 200% 100%;
      animation: highlightSwipe 0.8s ease-out forwards;
      -webkit-background-clip: text;
    }

    .animate-none { opacity: 1; }

    @keyframes typing { from { max-width: 0 } to { max-width: 100% } }
    @keyframes blink-caret { from, to { border-color: transparent } 50% { border-color: rgba(255,255,255,0.8) } }
    @keyframes popIn { from { opacity: 0; transform: scale(0.5) } to { opacity: 1; transform: scale(1) } }
    @keyframes slideUp { from { opacity: 0; transform: translateY(40px) } to { opacity: 1; transform: translateY(0) } }
    @keyframes highlightSwipe { from { background-position: 200% 0 } to { background-position: 0 0 } }"""


def _get_playback_js() -> str:
    """播放控制 JS — 负责场景切换的时序控制。"""
    return """\
    (function() {
      const composition = document.querySelector('.composition');
      const clips = Array.from(document.querySelectorAll('.clip'));
      const totalDuration = parseFloat(composition.dataset.duration || 60);

      let currentIndex = -1;
      let startTime = null;

      function getActiveClipIndex(elapsed) {
        let cumulative = 0;
        for (let i = 0; i < clips.length; i++) {
          const dur = parseFloat(clips[i].dataset.duration || 5);
          if (elapsed < cumulative + dur) return i;
          cumulative += dur;
        }
        return clips.length - 1;
      }

      function update(timestamp) {
        if (!startTime) startTime = timestamp;
        const elapsed = (timestamp - startTime) / 1000;

        if (elapsed >= totalDuration) {
          // 播放结束
          return;
        }

        const targetIndex = getActiveClipIndex(elapsed);
        if (targetIndex !== currentIndex) {
          // 切换场景
          if (currentIndex >= 0 && currentIndex < clips.length) {
            clips[currentIndex].classList.remove('active');
            clips[currentIndex].classList.add('exiting');
            const oldAudio = clips[currentIndex].querySelector('.scene-audio');
            if (oldAudio) oldAudio.pause();

            // 移除 exiting 状态
            setTimeout(() => {
              clips[currentIndex]?.classList.remove('exiting');
            }, 700);
          }

          currentIndex = targetIndex;
          const activeClip = clips[currentIndex];
          activeClip.classList.add('active');

          // 播放场景音频
          const audio = activeClip.querySelector('.scene-audio');
          if (audio) {
            audio.currentTime = 0;
            audio.play().catch(() => {});
          }

          // 重新触发文字动画
          activeClip.querySelectorAll('[class*="animate-"]').forEach(el => {
            el.style.animation = 'none';
            el.offsetHeight; // trigger reflow
            el.style.animation = '';
          });
        }

        requestAnimationFrame(update);
      }

      // 自动播放
      requestAnimationFrame(update);
    })();"""
