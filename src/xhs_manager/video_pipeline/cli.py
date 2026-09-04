"""视频 Pipeline CLI 入口 — 可手动触发流水线或单独执行某个阶段。

用法:
    # 运行完整流水线
    python -m xhs_manager.video_pipeline.cli run

    # 只运行某个阶段
    python -m xhs_manager.video_pipeline.cli stage collecting
    python -m xhs_manager.video_pipeline.cli stage selecting --run-id <id>

    # 查看最近的运行记录
    python -m xhs_manager.video_pipeline.cli status

    # 启动每日定时调度
    python -m xhs_manager.video_pipeline.cli schedule
"""

import argparse
import json
import logging
import sys
from datetime import date

from xhs_manager.config import get_settings
from xhs_manager.db import create_db_engine, create_session_factory
from xhs_manager.video_pipeline.config import get_video_settings
from xhs_manager.video_pipeline.domain import PipelineStatus
from xhs_manager.video_pipeline.models import VideoPipelineRun, VideoTopic
from xhs_manager.video_pipeline.pipeline import VideoPipeline


def build_session_factory():
    """按主配置构造数据库会话工厂。"""
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    return create_session_factory(engine)


def setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def cmd_run(args) -> None:
    """执行完整的视频流水线。"""
    settings = get_video_settings()
    pipeline = VideoPipeline(build_session_factory(), settings)

    run_date = date.fromisoformat(args.date) if args.date else None
    start_from = PipelineStatus(args.start_from) if args.start_from else None
    result = pipeline.run(run_date, start_from=start_from)

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    sys.exit(0 if result.get("status") == "completed" else 1)


def cmd_stage(args) -> None:
    """只执行流水线的某个阶段。"""
    settings = get_video_settings()
    pipeline = VideoPipeline(build_session_factory(), settings)

    try:
        stage = PipelineStatus(args.stage)
    except ValueError:
        print(f"未知阶段: {args.stage}")
        print(f"可选: {', '.join(s.value for s in PipelineStatus if s not in (PipelineStatus.COMPLETED, PipelineStatus.FAILED))}")
        sys.exit(1)

    run_id = args.run_id
    if not run_id:
        run_id = pipeline.create_run(trigger_type="manual")

    result = pipeline.run_stage(run_id, stage)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


def cmd_status(args) -> None:
    """查看最近的运行记录。"""
    with build_session_factory()() as session:
        runs = (
            session.query(VideoPipelineRun)
            .order_by(VideoPipelineRun.created_at.desc())
            .limit(args.limit)
            .all()
        )

        if not runs:
            print("没有运行记录")
            return

        print(f"{'ID':<36}  {'日期':<12}  {'状态':<14}  {'热点':>4}  {'选题':>4}  {'视频':>4}  {'发布':>4}")
        print("-" * 110)
        for run in runs:
            print(
                f"{run.id}  {run.run_date}  {run.status:<14}  "
                f"{run.trend_count:>4}  {run.topic_count:>4}  "
                f"{run.video_count:>4}  {run.published_count:>4}"
            )


def cmd_seed(args) -> None:
    """插入示例选题+脚本，用于在不调用 LLM 的情况下测试下游阶段。"""
    from xhs_manager.video_pipeline.seed import seed_topic_and_script

    factory = build_session_factory()
    with factory() as session:
        if args.run_id:
            run = session.get(VideoPipelineRun, args.run_id)
        else:
            run = (session.query(VideoPipelineRun)
                   .order_by(VideoPipelineRun.created_at.desc()).first())
        if not run:
            print("没有可用的流水线运行，请先执行 stage collecting")
            sys.exit(1)

        topic, script = seed_topic_and_script(session, run)
        run.topic_count = session.query(VideoTopic).filter_by(
            pipeline_run_id=run.id).count()
        session.commit()

        print(json.dumps({
            "run_id": run.id,
            "topic_id": topic.id,
            "script_id": script.id,
            "title": topic.title,
            "scenes": len(script.scenes),
            "duration": script.total_duration,
        }, indent=2, ensure_ascii=False))


def cmd_schedule(args) -> None:
    """常驻进程：每日定时触发完整流水线。"""
    from xhs_manager.video_pipeline.scheduler import DailyScheduler

    settings = get_video_settings()
    pipeline = VideoPipeline(build_session_factory(), settings)

    def job() -> None:
        result = pipeline.run()
        logging.getLogger(__name__).info(
            "流水线结束: status=%s run_id=%s",
            result.get("status"), result.get("run_id"),
        )

    hour = args.hour if args.hour is not None else settings.daily_trigger_hour
    DailyScheduler(trigger_hour_utc=hour, job=job).run_forever()


def cmd_schedule_config(args) -> None:
    """输出 cron / launchd 配置，用于系统级定时。"""
    from pathlib import Path

    from xhs_manager.video_pipeline.scheduler import (
        render_crontab_line,
        render_launchd_plist,
    )

    settings = get_video_settings()
    project = Path(__file__).resolve().parents[3]
    hour = args.hour if args.hour is not None else settings.daily_trigger_hour

    if args.kind == "cron":
        print("# 加入 crontab -e（注意 cron 用本机时区，此处小时按你的本地时间填）")
        print(render_crontab_line(hour, project))
    else:
        print(render_launchd_plist(hour, project))


def cmd_config(args) -> None:
    """显示当前配置。"""
    settings = get_video_settings()
    config_dict = settings.model_dump()
    # 隐藏敏感信息
    for key in ("claude_api_key", "pexels_api_key"):
        if config_dict.get(key):
            config_dict[key] = config_dict[key][:8] + "..."
    print(json.dumps(config_dict, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="视频自动化 Pipeline CLI",
        prog="python -m xhs_manager.video_pipeline.cli",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="详细日志输出")

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # run
    run_parser = subparsers.add_parser("run", help="执行完整的视频流水线")
    run_parser.add_argument("--date", help="运行日期 (YYYY-MM-DD)，默认今天")
    run_parser.add_argument(
        "--from", dest="start_from", metavar="STAGE",
        help="强制从指定阶段开始（默认按运行当前状态自动续跑）",
    )
    run_parser.set_defaults(func=cmd_run)

    # stage
    stage_parser = subparsers.add_parser("stage", help="只执行某个阶段")
    stage_parser.add_argument("stage", help="阶段名称")
    stage_parser.add_argument("--run-id", help="流水线运行 ID")
    stage_parser.set_defaults(func=cmd_stage)

    # status
    status_parser = subparsers.add_parser("status", help="查看运行记录")
    status_parser.add_argument("-n", "--limit", type=int, default=10, help="显示数量")
    status_parser.set_defaults(func=cmd_status)

    # seed
    seed_parser = subparsers.add_parser("seed", help="插入示例选题+脚本（跳过 LLM）")
    seed_parser.add_argument("--run-id", help="流水线运行 ID，默认最近一次")
    seed_parser.set_defaults(func=cmd_seed)

    # schedule
    sch = subparsers.add_parser("schedule", help="常驻进程，每日定时触发流水线")
    sch.add_argument("--hour", type=int, help="触发小时（UTC），默认读配置")
    sch.set_defaults(func=cmd_schedule)

    # schedule-config
    sc = subparsers.add_parser("schedule-config", help="输出 cron/launchd 配置")
    sc.add_argument("kind", choices=["cron", "launchd"])
    sc.add_argument("--hour", type=int, help="触发小时，默认读配置")
    sc.set_defaults(func=cmd_schedule_config)

    # config
    config_parser = subparsers.add_parser("config", help="显示当前配置")
    config_parser.set_defaults(func=cmd_config)

    args = parser.parse_args()
    setup_logging(args.verbose)

    if not args.command:
        parser.print_help()
        sys.exit(0)

    args.func(args)


if __name__ == "__main__":
    main()
