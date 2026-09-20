"""把一条**手工制作**的成片登记进库，让它出现在控制台任务台。

用途很窄：片子是绕开流水线做的（自定义合成、手写动效），但你仍然希望它
进入统一的任务台、审批和发布清单。它不重新渲染，只是把现成的文件登记成
run → topic → script → composition → render 这条链。

用法:
    python scripts/register_manual_video.py videos/quick-logcat

目录里需要有:
    quick-logcat.mp4 或任意单个 .mp4   成片
    timeline.json                      逐句旁白与时间（build_audio.py 产出）
    index.html                         合成页（登记成 composition）
    cover.jpg                          可选，封面
    meta.json                          可选，覆盖选题标题/文案等元数据
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from xhs_manager.video_pipeline.cli import build_session_factory  # noqa: E402
from xhs_manager.video_pipeline.config import VideoPipelineSettings  # noqa: E402
from xhs_manager.video_pipeline.models import (  # noqa: E402
    VideoComposition,
    VideoPipelineRun,
    VideoRender,
    VideoScript,
    VideoTopic,
)
from xhs_manager.video_pipeline.seed import free_run_date  # noqa: E402

FFPROBE = "/opt/homebrew/bin/ffprobe"


def probe(path: Path) -> dict:
    """成片的真实时长/分辨率/帧率。控制台的「1:29 · 8 场景 · 3.0 MB」就靠这个。"""
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "format=duration:stream=width,height,r_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout
    d = json.loads(out)
    st = (d.get("streams") or [{}])[0]
    num, _, den = (st.get("r_frame_rate") or "30/1").partition("/")
    return {
        "duration": float(d["format"]["duration"]),
        "resolution": f"{st.get('width', 1080)}x{st.get('height', 1920)}",
        "fps": int(round(float(num) / float(den or 1))),
        "size": path.stat().st_size,
    }


def scenes_from_timeline(tl: dict) -> list[dict]:
    """把逐句旁白还原成场景列表。

    手工片子没有分镜脚本，但 timeline.json 里每句话的起止是实打实的。
    一句一场景。

    时长取「到下一句开口为止」，**不是**这句语音本身的长度。差别看着小，
    后果不小：句间有停顿、片尾还有留白，按净语音长度加起来会比成片短一截
    （实测 49.2s vs 52.0s），控制台的一致性校验会直接判红「时长对不上」。
    场景本来就该把整条时间轴铺满，中间没有无主的空档。
    """
    lines = tl.get("lines") or []
    total = float(tl.get("total") or 0) or (lines[-1]["end"] if lines else 0)
    scenes = []
    for i, ln in enumerate(lines):
        nxt = lines[i + 1]["start"] if i + 1 < len(lines) else total
        scenes.append({
            "scene_id": ln["id"],
            "order": i + 1,
            "duration": round(max(nxt - ln["start"], 0.1), 2),
            "visual_desc": "手工合成，画面见 index.html",
            "text_overlay": {"main": "", "sub": "", "animation": "none"},
            "transition": "fade",
            "narration": ln["text"],
            "material_hints": [],
            "bgm_mood": "explain",
        })
    return scenes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("directory", help="成片所在目录")
    ap.add_argument(
        "--date",
        help="挂到哪一天的 run（YYYY-MM-DD）。不给就自动找空位，"
             "规则同 seed_manual_video：从今天往过去找，不占用未来定时任务的日子",
    )
    ap.add_argument("--title", help="选题标题，默认取 meta.json 或目录名")
    args = ap.parse_args()

    d = Path(args.directory).resolve()
    if not d.is_dir():
        print(f"目录不存在: {d}")
        return 1

    mp4s = sorted(d.glob("*.mp4"))
    if len(mp4s) != 1:
        print(f"需要目录里正好有一个 .mp4，现在有 {len(mp4s)} 个")
        return 1
    mp4 = mp4s[0]

    tl_path = d / "timeline.json"
    if not tl_path.exists():
        print(f"缺 timeline.json: {tl_path}")
        return 1
    tl = json.loads(tl_path.read_text())
    scenes = scenes_from_timeline(tl)
    if not scenes:
        print("timeline.json 里没有 lines")
        return 1

    meta = {}
    if (d / "meta.json").exists():
        meta = json.loads((d / "meta.json").read_text())

    info = probe(mp4)
    title = args.title or meta.get("title") or d.name
    now = datetime.now(timezone.utc)

    factory = build_session_factory()
    with factory() as s:
        if args.date:
            run_date = date.fromisoformat(args.date)
            clash = s.query(VideoPipelineRun).filter(
                VideoPipelineRun.run_date == run_date).first()
            if clash:
                # run_date 按天唯一。硬挂到已有 run 上的后果是：对方下次
                # cli run --date 那天，会把这条也一起拖着过阶段。
                print(
                    f"{run_date} 已经有 run 了（{clash.id}，状态 {clash.status}）。"
                    f"换一天，或者不给 --date 让它自动找空位。"
                )
                return 1
        else:
            run_date = free_run_date(s)

        run = VideoPipelineRun(run_date=run_date, status="publishing", video_count=1)
        s.add(run)
        s.flush()

        topic = VideoTopic(
            pipeline_run_id=run.id,
            rank=1,
            title=title,
            angle=meta.get("angle", "手工制作，未经流水线选题"),
            why_now=meta.get("why_now", "手工制作，登记入库以纳入审批与发布流程"),
            target_audience=meta.get("target_audience", "—"),
            video_type=meta.get("video_type", "explainer"),
            estimated_duration=int(round(info["duration"])),
            scores={},
            total_score=0.0,
            source_signal_ids=[],
            status="selected",
        )
        s.add(topic)
        s.flush()

        script = VideoScript(
            topic_id=topic.id,
            version=1,
            total_duration=int(round(info["duration"])),
            scenes=scenes,
            bgm_style=meta.get("bgm_style", ""),
            platform_metadata=meta.get("platform_metadata", {}),
            # 这两个字段是可追溯性的关键：一眼看出它不是 LLM 生成、源头在哪个目录
            generation_model="manual",
            generation_prompt_hash=str(d),
            status="ready",
        )
        s.add(script)
        s.flush()

        # 成片必须**收进产物目录**再登记。
        # 控制台的 /api/file 只服务 output_base_dir 里的文件（防 ../ 和符号链接
        # 绕过），路径留在作者目录里的话，任务能列出来但点开播不了。
        dest_dir = Path(VideoPipelineSettings().output_base_dir).resolve() / run.id / "manual"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_mp4 = dest_dir / mp4.name
        shutil.copy2(mp4, dest_mp4)
        dest_cover = None
        if (d / "cover.jpg").exists():
            dest_cover = dest_dir / "cover.jpg"
            shutil.copy2(d / "cover.jpg", dest_cover)

        comp = VideoComposition(
            script_id=script.id,
            # 指向作者目录而不是拷贝目录：合成页真正的出处在那儿，
            # 这两个字段是可追溯性用的，不经过 /api/file。
            composition_dir=str(d),
            html_path=str(d / "index.html"),
            total_duration=info["duration"],
            resolution=info["resolution"],
            transition_effects=[],
            has_narration=True,
            has_bgm=False,
            template_id="manual",
            status="rendered",
        )
        s.add(comp)
        s.flush()

        render = VideoRender(
            composition_id=comp.id,
            output_path=str(dest_mp4),
            cover_path=str(dest_cover) if dest_cover else None,
            covers={"default": str(dest_cover)} if dest_cover else {},
            format="mp4",
            codec="h264",
            fps=info["fps"],
            file_size=info["size"],
            duration=info["duration"],
            status="completed",
            started_at=now,
            completed_at=now,
        )
        s.add(render)
        s.commit()

        print(json.dumps({
            "run_id": run.id, "run_date": str(run_date),
            "topic_id": topic.id, "script_id": script.id,
            "render_id": render.id,
            "title": title,
            "scenes": len(scenes),
            "duration": round(info["duration"], 2),
            "size_mb": round(info["size"] / 1024 / 1024, 2),
            "output": str(dest_mp4),
            "source": str(mp4),
        }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
