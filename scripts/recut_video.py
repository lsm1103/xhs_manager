#!/usr/bin/env python
"""按真实旁白时长重切一条视频（含分段 BGM），只作用于指定的一个脚本。

跑整条 stage4/stage5 会把同一次运行下的 3 条视频全部重建、还会新增组合记录；
复盘单条片子时要的是"只动这一条"，所以有这个脚本。

做的事：
  1. 逐场景合成旁白，量出每段真实时长（`audio/narration.py`）
  2. 用实测时长覆盖脚本里的标称 duration，写回库
  3. 重建该组合的 index.html（时序跟着新时长走）
  4. 重新渲染，Stage5 会按新的场景时长重新规划 BGM 乐段
  5. 落一条 VideoRender 记录（含封面），让成片在控制台里可见

第 5 步不能省：控制台判断「有没有成片」看的是 VideoRender，不是磁盘上
有没有 video.mp4。早先这里直接调 _render_html_to_mp4 出片、不写记录，
结果是片子渲出来了，任务台上那一行却一直停在「未渲染」，发布清单也不出。

用法：
    uv run python scripts/recut_video.py <script_id> [--moods hook,explain,...]
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from xhs_manager.domain import utcnow  # noqa: E402
from xhs_manager.video_pipeline.cli import build_session_factory  # noqa: E402
from xhs_manager.video_pipeline.audio.narration import build_aligned_narration  # noqa: E402
from xhs_manager.video_pipeline.config import VideoPipelineSettings  # noqa: E402
from xhs_manager.video_pipeline.models import (  # noqa: E402
    VideoComposition,
    VideoMaterial,
    VideoRender,
    VideoScript,
)
from xhs_manager.video_pipeline.stages.stage4_compose import (  # noqa: E402
    _build_composition_html,
)
from xhs_manager.video_pipeline.stages.stage5_render import (  # noqa: E402
    _generate_covers,
    _render_html_to_mp4,
)

logger = logging.getLogger("recut")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script_id")
    ap.add_argument("--moods", help="逗号分隔，按场景顺序覆盖 bgm_mood")
    ap.add_argument("--reuse-narration", action="store_true",
                    help="复用已合成的分段旁白（只想换 BGM 重渲时用，省掉几分钟 TTS）")
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
            reuse=args.reuse_narration,
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

        # 渲染要跑好几分钟，这中间绝不能攥着 SQLite 的写锁：全库只有一把，
        # 从第一条 INSERT 到 commit 之间它一直属于这个连接，worker 心跳想
        # UPDATE work_items 就只能等满 busy_timeout 然后报 database is locked。
        # 所以先把脚本和组合的改动落库，把锁放掉，再开渲染。
        session.commit()

        # 4. 重新渲染（Stage5 内部会按新场景时长重排 BGM 乐段）
        #
        # comp.status 在成功之前不动：把它改成 render_ready 会让正在跑的
        # worker 把这支片子当成待渲染任务捡走，两边同时渲同一个 comp_dir。
        render = VideoRender(
            composition_id=comp.id,
            fps=settings.render_fps,
            status="rendering",
            started_at=utcnow(),
        )
        session.add(render)
        session.commit()   # 同理：建完记录立刻放锁

        started = time.monotonic()
        try:
            out = _render_html_to_mp4(comp, render, settings, scenes=scenes)
        except Exception as e:
            # 失败可能来自数据库本身，先把会话清干净再写失败状态
            session.rollback()
            render.status = "failed"
            render.error_detail = str(e)[:2000]
            comp.status = "error"
            session.commit()
            print(f"渲染异常: {e}")
            return 1

        if not out or not out.exists():
            render.status = "failed"
            render.error_detail = "渲染输出文件不存在"
            comp.status = "error"
            session.commit()
            print("渲染失败")
            return 1

        from xhs_manager.video_pipeline.integrations.renderer import probe_duration

        render.output_path = str(out)
        render.file_size = out.stat().st_size
        # 以成片真实时长为准：旁白比脚本短时 -shortest 会把尾巴截掉
        render.duration = probe_duration(out) or comp.total_duration
        render.render_time = round(time.monotonic() - started, 2)
        render.status = "completed"
        render.completed_at = utcnow()

        covers = _generate_covers(out, comp, settings)
        render.cover_path = covers.get("default")
        render.covers = covers

        comp.status = "rendered"
        session.commit()

        print(f"成片: {out} ({render.file_size / 1024 / 1024:.1f}MB, "
              f"{render.duration:.2f}s)")
        print(f"render_id = {render.id}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
