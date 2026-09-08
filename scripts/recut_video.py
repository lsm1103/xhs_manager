#!/usr/bin/env python
"""按真实旁白时长重切一条视频（含分段 BGM），只作用于指定的一个脚本。

跑整条 stage4/stage5 会把同一次运行下的 3 条视频全部重建、还会新增组合记录；
复盘单条片子时要的是"只动这一条"，所以有这个脚本。

做的事：
  1. 逐场景合成旁白，量出每段真实时长（`audio/narration.py`）
  2. 用实测时长覆盖脚本里的标称 duration，写回库
  3. 重建该组合的 index.html（时序跟着新时长走）
  4. 重新渲染，Stage5 会按新的场景时长重新规划 BGM 乐段

用法：
    uv run python scripts/recut_video.py <script_id> [--moods hook,explain,...]
"""

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from xhs_manager.video_pipeline.cli import build_session_factory  # noqa: E402
from xhs_manager.video_pipeline.audio.narration import build_aligned_narration  # noqa: E402
from xhs_manager.video_pipeline.config import VideoPipelineSettings  # noqa: E402
from xhs_manager.video_pipeline.models import (  # noqa: E402
    VideoComposition,
    VideoMaterial,
    VideoScript,
)
from xhs_manager.video_pipeline.stages.stage4_compose import (  # noqa: E402
    _build_composition_html,
)
from xhs_manager.video_pipeline.stages.stage5_render import _render_html_to_mp4  # noqa: E402

logger = logging.getLogger("recut")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script_id")
    ap.add_argument("--moods", help="逗号分隔，按场景顺序覆盖 bgm_mood")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = VideoPipelineSettings()

    session = build_session_factory()()
    try:
        script = session.get(VideoScript, args.script_id)
        if not script:
            print(f"脚本不存在: {args.script_id}")
            return 1
        comp = (
            session.query(VideoComposition)
            .filter(VideoComposition.script_id == script.id)
            .order_by(VideoComposition.created_at.desc())
            .first()
        )
        if not comp:
            print("该脚本没有 HTML 组合")
            return 1

        scenes = [dict(s) for s in script.scenes]

        if args.moods:
            moods = [m.strip() for m in args.moods.split(",")]
            if len(moods) != len(scenes):
                print(f"moods 数量({len(moods)}) 与场景数({len(scenes)}) 不符")
                return 1
            for sc, m in zip(scenes, moods):
                sc["bgm_mood"] = m

        comp_dir = Path(comp.composition_dir)
        old_total = float(script.total_duration)

        # 1+2. 逐场景合成 → 校准时长
        track, total = build_aligned_narration(
            scenes, comp_dir / "assets", settings,
        )
        if not track:
            print("旁白合成失败，中止")
            return 1
        print(f"时长校准: {old_total:.1f}s → {total:.1f}s")

        script.scenes = scenes
        script.total_duration = total
        comp.total_duration = total

        # 3. 重建 HTML（素材映射不变，只是时序跟着新时长走）
        materials = (
            session.query(VideoMaterial)
            .filter(VideoMaterial.script_id == script.id, VideoMaterial.selected == True)  # noqa: E712
            .all()
        )
        material_map: dict[str, list[VideoMaterial]] = {}
        for m in materials:
            material_map.setdefault(m.scene_id, []).append(m)

        script.scenes = scenes  # _build_composition_html 读的是 script.scenes
        html = _build_composition_html(script, material_map, settings)
        Path(comp.html_path).write_text(html, encoding="utf-8")

        session.flush()

        # 4. 重新渲染（Stage5 内部会按新场景时长重排 BGM 乐段）
        out = _render_html_to_mp4(comp, None, settings, scenes=scenes)
        if not out or not out.exists():
            print("渲染失败")
            return 1

        from xhs_manager.video_pipeline.integrations.renderer import probe_duration

        print(f"成片: {out} ({out.stat().st_size / 1024 / 1024:.1f}MB, "
              f"{probe_duration(out):.2f}s)")
        session.commit()
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
