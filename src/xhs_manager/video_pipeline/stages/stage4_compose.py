"""Stage 4: HTML 动态构建 — 将分镜脚本和素材组合成可播放的 HTML 视频组合。

本阶段只做编排：查脚本、查素材、落盘、写 VideoComposition 记录。
真正的版面/动画/外壳在 video_pipeline.composition 包里。
"""

import logging
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id
from xhs_manager.video_pipeline.composition import (
    SceneMedia,
    build_composition_html,
    classify_media,
)
from xhs_manager.video_pipeline.config import VideoPipelineSettings
from xhs_manager.video_pipeline.domain import StageError
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
            # 下一支片子的 HTML 构建和素材软链还要跑一阵，别攥着写锁进去
            session.commit()
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
    """构建完整的 HTML 视频组合。

    具体的主题/版面/动画都在 video_pipeline.composition 里，
    这里只负责把「场景 → 素材」的映射喂进去。
    """
    width, height = (int(x) for x in settings.render_resolution.split("x"))

    # 每个场景取第一个非音频素材当背景
    visual_by_scene: dict[str, SceneMedia] = {}
    for scene_id, materials in material_map.items():
        for m in materials:
            if m.material_type == "audio":
                continue
            media = classify_media(m.local_path)
            if media is not None:
                visual_by_scene[scene_id] = media
                break

    html, _timeline = build_composition_html(
        list(script.scenes),
        media_lookup=visual_by_scene.get,
        width=width,
        height=height,
        fps=settings.render_fps,
        theme=settings.composition_theme,
        brand=settings.brand_name,
        handle=settings.brand_handle,
    )
    return html


def _link_assets(material_map: dict, comp_dir: Path) -> None:
    """将素材文件链接/复制到组合目录的 assets 子目录。"""
    assets_dir = comp_dir / "assets"
    assets_dir.mkdir(exist_ok=True)

    for materials in material_map.values():
        for m in materials:
            src = Path(m.local_path)
            if src.exists():
                dst = assets_dir / src.name
                if not dst.exists():
                    try:
                        shutil.copy2(str(src), str(dst))
                    except OSError as e:
                        logger.warning("素材复制失败 %s: %s", src, e)
