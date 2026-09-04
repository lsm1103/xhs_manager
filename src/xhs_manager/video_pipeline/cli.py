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

from xhs_manager.db import engine, SessionLocal
from xhs_manager.video_pipeline.config import get_video_settings
from xhs_manager.video_pipeline.domain import PipelineStatus
from xhs_manager.video_pipeline.models import VideoPipelineRun
from xhs_manager.video_pipeline.pipeline import VideoPipeline


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
    pipeline = VideoPipeline(SessionLocal, settings)

    run_date = date.fromisoformat(args.date) if args.date else None
    result = pipeline.run(run_date)

    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    sys.exit(0 if result.get("status") == "completed" else 1)


def cmd_stage(args) -> None:
    """只执行流水线的某个阶段。"""
    settings = get_video_settings()
    pipeline = VideoPipeline(SessionLocal, settings)

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
    with SessionLocal() as session:
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
