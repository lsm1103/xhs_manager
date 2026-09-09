#!/usr/bin/env python
"""按场景逐个重取素材，让每个镜头的画面对得上它自己那句话。

Stage3 现在的做法是：把所有场景的搜索词汇成一个池子丢给 MoneyPrinterTurbo，
拿回一堆素材后按下标 `materials[i % len]` 分给各场景。后果有两个：
  1. 画面和台词无关——第 5 个场景拿到的可能是第 1 个搜索词的结果
  2. 素材数少于场景数时直接循环复用，包袱镜头和开头撞成同一段

这里改成一场景一次搜索，各拿各的。代价是 N 次 CLI 调用（约 10s/次），
换来画面语义对齐，对短视频来说这笔账是划算的。

用法：
    uv run python scripts/refetch_scene_materials.py <script_id> [--scenes s07,s08]
"""

import argparse
import logging
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from xhs_manager.video_pipeline.cli import build_session_factory  # noqa: E402
from xhs_manager.video_pipeline.config import VideoPipelineSettings  # noqa: E402
from xhs_manager.video_pipeline.integrations.moneyprinter import MoneyPrinterTurbo  # noqa: E402
from xhs_manager.video_pipeline.models import VideoMaterial, VideoScript  # noqa: E402
from xhs_manager.video_pipeline.stages.stage3_materials import (  # noqa: E402
    _reject_portrait_materials,
    _search_terms_for_scene,
)

logger = logging.getLogger("refetch")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("script_id")
    ap.add_argument("--scenes", help="逗号分隔的 scene_id，默认全部")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    settings = VideoPipelineSettings()
    mpt = MoneyPrinterTurbo(settings.moneyprinter_path)

    session = build_session_factory()()
    try:
        script = session.get(VideoScript, args.script_id)
        if not script:
            print(f"脚本不存在: {args.script_id}")
            return 1

        only = set(args.scenes.split(",")) if args.scenes else None
        # 素材原本落在脚本目录下（composition/assets 是它的副本）
        existing = (
            session.query(VideoMaterial)
            .filter(VideoMaterial.script_id == script.id)
            .all()
        )
        by_scene = {m.scene_id: m for m in existing if m.material_type != "audio"}
        if not by_scene:
            print("该脚本没有画面素材记录")
            return 1
        out_dir = Path(next(iter(by_scene.values())).local_path).parent

        # 已经在用的素材，避免不同场景搜到同一段
        used: set[str] = set()
        replaced = 0

        for scene in script.scenes:
            sid = scene.get("scene_id")
            if only and sid not in only:
                mat = by_scene.get(sid)
                if mat:
                    used.add(Path(mat.local_path).name)
                continue

            terms = _search_terms_for_scene(scene)
            if not terms:
                logger.warning("%s 无搜索词，跳过", sid)
                continue

            dur = int(scene.get("duration") or 5)
            res = mpt.search_materials(
                search_terms=terms, video_aspect="9:16",
                source="pexels", clip_duration=dur,
            )
            if not res["success"] or not res.get("materials"):
                logger.warning("%s 搜索无结果: %s", sid, str(res.get("error"))[:120])
                continue

            safe = _reject_portrait_materials(res["materials"])
            pick = next((m for m in safe if Path(m["path"]).name not in used), None)
            if not pick:
                logger.warning("%s 素材全被人脸检测拒绝或已被占用", sid)
                continue

            dst = out_dir / f"{sid}_clip.mp4"
            shutil.copy2(pick["path"], dst)
            used.add(Path(pick["path"]).name)

            # assets/ 里是素材的副本，Stage4 的 _link_assets 见到同名文件就跳过，
            # 不覆盖的话渲染出来的还是旧画面
            for assets in out_dir.glob("*/assets"):
                shutil.copy2(dst, assets / dst.name)

            mat = by_scene.get(sid)
            if mat:
                mat.local_path = str(dst)
                mat.source_url = pick.get("source_url")
                mat.file_size = pick.get("size")
                mat.extra_meta = {**(mat.extra_meta or {}), "search_term": terms[0]}
            replaced += 1
            logger.info("%s ← %s (%s)", sid, Path(pick["path"]).name, terms[0])

        session.commit()
        print(f"\n已替换 {replaced} 个场景的素材。记得重跑 recut_video.py 让 assets/ 同步。")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
