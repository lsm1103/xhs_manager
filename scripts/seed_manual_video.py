#!/usr/bin/env python
"""用一份手写的分镜 JSON 直接起一条视频，跳过热点采集和 LLM 编剧。

为什么要绕开 Stage1/Stage2：
  素材已经在手上（比如用户直接给了一段文案）时，再去跑一遍热点采集
  只会引入噪音——采到的热点和这条片子毫无关系，选题打分也没有意义。
  但 Stage3（找素材 + 肖像权过滤）和 Stage4（组 HTML）是通用的，
  所以这里只补齐它们需要的 run / topic / script 三条记录，然后照常往下走。

跑完拿到 script_id，再用 scripts/recut_video.py 做旁白对齐 + 配乐 + 渲染。

用法：
    uv run python scripts/seed_manual_video.py specs/xxx.json
"""

import argparse
import hashlib
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from xhs_manager.domain import new_id, utcnow  # noqa: E402
from xhs_manager.video_pipeline.cli import build_session_factory  # noqa: E402
from xhs_manager.video_pipeline.config import VideoPipelineSettings  # noqa: E402
from xhs_manager.video_pipeline.models import (  # noqa: E402
    VideoPipelineRun,
    VideoScript,
    VideoTopic,
)
from xhs_manager.video_pipeline.stages.stage3_materials import collect_materials  # noqa: E402
from xhs_manager.video_pipeline.stages.stage4_compose import compose_html  # noqa: E402


def _free_run_date(session) -> date:
    """找一个还没被占用的 run_date。

    `video_pipeline_runs.run_date` 上有唯一约束——每天一次自动运行的设计。
    手动起片一天可能起好几条，所以从今天往**过去**找空位：
    往未来找会占掉后面几天定时任务的位置，往过去找只是借用没跑过的日子。
    """
    d = date.today()
    for _ in range(3650):
        exists = (
            session.query(VideoPipelineRun.id)
            .filter(VideoPipelineRun.run_date == d)
            .first()
        )
        if not exists:
            return d
        d = d - timedelta(days=1)
    raise RuntimeError("十年内找不到空闲的 run_date")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("spec", help="分镜 JSON 路径")
    ap.add_argument("--no-materials", action="store_true", help="只建记录，不找素材")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    scenes = spec["scenes"]
    total = sum(float(s.get("duration") or 0) for s in scenes)

    # render_mode 强制 html：MPT 一站式路径会无视我们的 HTML 组合和 IndexTTS 旁白
    settings = VideoPipelineSettings(render_mode="html")
    session = build_session_factory()()
    try:
        run = VideoPipelineRun(
            id=new_id(),
            trigger_type="manual",
            run_date=_free_run_date(session),
            status="composing",
            topic_count=1,
            started_at=utcnow(),
            config_snapshot={"source": "seed_manual_video", "spec": str(args.spec)},
        )
        topic = VideoTopic(
            id=new_id(),
            pipeline_run_id=run.id,
            rank=1,
            title=spec["title"],
            angle=spec.get("angle", ""),
            why_now=spec.get("why_now", ""),
            target_audience=spec.get("target_audience", ""),
            video_type=spec.get("video_type", "commentary"),
            estimated_duration=int(total),
            scores={},
            total_score=0.0,
            source_signal_ids=[],
            status="scripted",
        )
        script = VideoScript(
            id=new_id(),
            topic_id=topic.id,
            total_duration=int(total),
            scenes=scenes,
            bgm_style=spec.get("bgm_style"),
            platform_metadata=spec.get("platform_metadata", {}),
            generation_model="manual",
            generation_prompt_hash=hashlib.sha256(
                json.dumps(spec, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest(),
            status="ready",
        )
        # 逐条 flush：模型之间没有声明 relationship，SQLAlchemy 推不出插入顺序，
        # 一次性 add_all 会先插 script 再插 topic，直接撞外键约束
        for obj in (run, topic, script):
            session.add(obj)
            session.flush()

        if not args.no_materials:
            print(json.dumps(
                collect_materials(session, run, settings),
                ensure_ascii=False, indent=2,
            ))
            print(json.dumps(
                compose_html(session, run, settings),
                ensure_ascii=False, indent=2, default=str,
            ))

        session.commit()
        print(f"\nrun_id    = {run.id}")
        print(f"topic_id  = {topic.id}")
        print(f"script_id = {script.id}")
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
