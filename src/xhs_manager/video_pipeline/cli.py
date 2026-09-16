"""视频 Pipeline CLI 入口 — 可手动触发流水线或单独执行某个阶段。

用法:
    # 运行完整流水线
    python -m xhs_manager.video_pipeline.cli run

    # 只运行某个阶段
    python -m xhs_manager.video_pipeline.cli stage collecting

    # 针对一个话题做定向采集（临时覆盖平台与关键词）
    python -m xhs_manager.video_pipeline.cli stage collecting \
        --platforms zhihu,weibo,wechat --keywords "AI 落地,AI 商业化" --limit 15

    # 查看各平台采集后端是否可用
    python -m xhs_manager.video_pipeline.cli collect-doctor
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


def _split_list(raw: str | None) -> list[str] | None:
    """把 "a,b, c" 解析成 ["a","b","c"]。"""
    if not raw:
        return None
    return [x.strip() for x in raw.split(",") if x.strip()]


def cmd_stage(args) -> None:
    """只执行流水线的某个阶段。"""
    settings = get_video_settings()

    # 采集阶段允许临时覆盖平台/关键词，方便针对单个话题做定向调研，
    # 不必为一次调研去改 .env。
    overrides: dict = {}
    platforms = _split_list(getattr(args, "platforms", None))
    keywords = _split_list(getattr(args, "keywords", None))
    if platforms:
        overrides["trend_platforms"] = platforms
    if keywords:
        overrides["trend_keywords"] = keywords
    if getattr(args, "limit", None):
        overrides["trends_per_platform"] = args.limit
    # 指定了关键词就是在做定向调研，默认不要泛热榜；--with-hot 可强制带上
    if keywords and not getattr(args, "with_hot", False):
        overrides["trend_include_hot"] = False
    if overrides:
        settings = settings.model_copy(update=overrides)

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
    from xhs_manager.video_pipeline.seed import load_script_file, seed_topic_and_script

    script_data = None
    if args.script:
        try:
            script_data = load_script_file(args.script)
        except (OSError, ValueError) as e:
            print(f"脚本文件有问题: {e}")
            sys.exit(1)

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

        topic, script = seed_topic_and_script(session, run, script_data=script_data)
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


def cmd_xhs_login(args) -> None:
    """一次性登录小红书，凭证保存到 pipeline 专用 Chrome profile。"""
    from pathlib import Path

    from xhs_manager.video_pipeline.integrations.xhs_publisher import (
        DEFAULT_PROFILE_DIR,
        XhsPublisher,
    )

    st = get_video_settings()
    pub = XhsPublisher(
        profile_dir=Path(st.xhs_profile_dir) if st.xhs_profile_dir else DEFAULT_PROFILE_DIR,
    )
    print(f"将打开浏览器窗口，请扫码或短信登录小红书。profile: {pub.profile_dir}")
    ok = pub.interactive_login(timeout_s=args.timeout)
    print("✅ 登录成功，之后可无人值守发布" if ok else "❌ 登录未完成")
    sys.exit(0 if ok else 1)


def cmd_xhs_check(args) -> None:
    """检查小红书登录态是否仍有效。"""
    from pathlib import Path

    from xhs_manager.video_pipeline.integrations.xhs_publisher import (
        DEFAULT_PROFILE_DIR,
        XhsPublisher,
    )

    st = get_video_settings()
    pub = XhsPublisher(
        profile_dir=Path(st.xhs_profile_dir) if st.xhs_profile_dir else DEFAULT_PROFILE_DIR,
    )
    ok, why = pub.available()
    if not ok:
        print(f"❌ {why}"); sys.exit(1)
    alive = pub.logged_in()
    print("✅ 登录态有效" if alive else "❌ 登录态已失效，请重新运行 xhs-login")
    sys.exit(0 if alive else 1)


def cmd_adopt(args) -> None:
    """把视频选题认领进内容任务模型。

    认领只是「承认这支片子已经走到哪儿了」，不会推进任何流程、
    不会入队工作项——被认领的往往是早就渲染完甚至发布完的片子。
    """
    from xhs_manager.video_pipeline import linking

    settings = get_video_settings()
    factory = build_session_factory()
    with factory() as session:
        if args.list:
            orphans = linking.orphan_topics(session)
            if not orphans:
                print("没有游离的视频选题")
                return
            print(f"{'选题 ID':<38}{'状态':<28}标题")
            print("-" * 104)
            for t in orphans:
                state = linking.task_state_for(session, t)
                print(f"{t.id}  {state:<26}{t.title[:38]}")
            print(f"\n共 {len(orphans)} 条。认领：--all 或 --topic <id>")
            return

        account_id = args.account
        if not account_id:
            account_id, _ = linking.ensure_default_account(
                session, name=settings.brand_name,
            )
            print(f"使用账号 {account_id}（{settings.brand_name}）")

        if args.all:
            results = linking.adopt_all_orphans(session, account_id=account_id)
        elif args.topic:
            results = [linking.adopt_topic(
                session, args.topic,
                account_id=account_id, task_id=args.task,
            )]
        else:
            print("要么 --all，要么 --topic <id>；先看清单用 --list")
            sys.exit(1)

        session.commit()
        for r in results:
            mark = "新建任务" if r.created_task else "挂到已有任务"
            print(f"  {r.topic_id[:8]} → {r.task_id[:8]}  {r.state:<24}{mark}")
        print(f"\n认领 {len(results)} 条")


def cmd_unlink(args) -> None:
    """撤销认领。任务本身留着。"""
    from xhs_manager.video_pipeline import linking

    with build_session_factory()() as session:
        linking.unlink_topic(session, args.topic)
        session.commit()
    print(f"已撤销 {args.topic} 的任务归属")


def cmd_site_login(args) -> None:
    """一次性扫码登录某个平台，凭证落在该平台自己的 Chrome profile。

    小红书/知乎/微博的站内搜索都要登录态。登录一次之后，
    采集就不再依赖任何第三方浏览器扩展。
    """
    from xhs_manager.video_pipeline.integrations.browser_collector import (
        SITE_SPECS,
        BrowserSession,
    )

    spec = SITE_SPECS.get(args.platform)
    if not spec:
        print(f"不支持的平台: {args.platform}（可选: {', '.join(SITE_SPECS)}）")
        sys.exit(1)

    session = BrowserSession(spec, headless=False)
    print(f"将打开浏览器窗口，请完成 {spec.name} 登录。profile: {spec.profile_dir}")
    ok = session.interactive_login(timeout_s=args.timeout)
    print(f"✅ {spec.name} 登录成功" if ok else f"❌ {spec.name} 登录未完成")
    sys.exit(0 if ok else 1)


def cmd_collect_doctor(args) -> None:
    """逐平台探测采集后端链的可用性，说明每个平台当前能拿到什么。"""
    from xhs_manager.video_pipeline.integrations.collectors import (
        SUPPORTED_PLATFORMS,
        probe_platform,
    )

    settings = get_video_settings()
    platforms = _split_list(args.platforms) or list(SUPPORTED_PLATFORMS)

    configured = set(settings.trend_platforms)
    print(f"{'平台':<14}{'状态':<8}{'后端链（✓=可用）'}")
    print("-" * 92)
    for platform in platforms:
        probes = probe_platform(platform)
        if not probes:
            print(f"{platform:<14}{'未知':<8}没有注册后端")
            continue
        first_ok = next((i for i, (_, ok) in enumerate(probes) if ok), None)
        if first_ok is None:
            status = "不可用"
        elif first_ok == 0:
            status = "正常"
        else:
            status = "降级"
        chain = "  →  ".join(
            f"{'✓' if ok else '✗'} {name}" for name, ok in probes
        )
        mark = "" if platform in configured else "（未列入 trend_platforms）"
        print(f"{platform:<14}{status:<8}{chain} {mark}")

    print()
    print("提示：")
    print("  • 站内数据（有互动量、是平台真实内容）> 站外索引（只有标题/链接/摘要）。")
    print("    想把小红书/知乎/微博拉回站内通道，执行一次：")
    print("      python -m xhs_manager.video_pipeline.cli site-login xiaohongshu")
    print("    登录态存在各自的 Chrome profile 里，之后采集无人值守。")
    print("  • 知乎/微博也可以直接配环境变量 ZHIHU_COOKIE / WEIBO_COOKIE 走站内 API。")
    print("  • opencli 通道需要 Chrome 的 OpenCLI 扩展已连接（opencli doctor 可验证）；")
    print("    不可用时会自动降级，不会让平台整体归零。")


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
    stage_parser.add_argument(
        "--platforms", help="仅采集这些平台（逗号分隔），临时覆盖配置",
    )
    stage_parser.add_argument(
        "--keywords", help="采集关键词（逗号分隔），临时覆盖配置",
    )
    stage_parser.add_argument(
        "--limit", type=int, help="每个平台/每个关键词的采集条数上限",
    )
    stage_parser.add_argument(
        "--with-hot", action="store_true",
        help="定向采集时仍然带上平台热榜（默认关键词模式下不取热榜）",
    )
    stage_parser.set_defaults(func=cmd_stage)

    # status
    status_parser = subparsers.add_parser("status", help="查看运行记录")
    status_parser.add_argument("-n", "--limit", type=int, default=10, help="显示数量")
    status_parser.set_defaults(func=cmd_status)

    # seed
    seed_parser = subparsers.add_parser(
        "seed", help="插入选题+脚本（跳过 LLM）；--script 可用手写脚本 JSON",
    )
    seed_parser.add_argument(
        "--script",
        help="脚本 JSON 文件；可以是完整脚本对象，也可以是裸 scenes 数组。"
             "不给则用内置示例。",
    )
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

    # xhs-login
    xl = subparsers.add_parser("xhs-login", help="一次性登录小红书（打开浏览器扫码）")
    xl.add_argument("--timeout", type=int, default=300, help="等待登录的秒数")
    xl.set_defaults(func=cmd_xhs_login)

    # xhs-check
    xc = subparsers.add_parser("xhs-check", help="检查小红书登录态")
    xc.set_defaults(func=cmd_xhs_check)

    # site-login
    sl = subparsers.add_parser(
        "site-login", help="扫码登录小红书/知乎/微博，打开站内采集通道",
    )
    sl.add_argument("platform", help="平台名: xiaohongshu | zhihu | weibo")
    sl.add_argument("--timeout", type=int, default=300, help="等待登录的秒数")
    sl.set_defaults(func=cmd_site_login)

    # adopt
    ad = subparsers.add_parser(
        "adopt", help="把视频选题认领进内容任务（打通图文线与视频线）",
    )
    ad.add_argument("--list", action="store_true", help="列出还没归属任务的视频选题")
    ad.add_argument("--all", action="store_true", help="认领全部游离选题")
    ad.add_argument("--topic", help="只认领这一个选题")
    ad.add_argument("--task", help="挂到这个已有任务，不新建")
    ad.add_argument("--account", help="归属账号，不给则复用/新建默认账号")
    ad.set_defaults(func=cmd_adopt)

    # unlink
    ul = subparsers.add_parser("unlink", help="撤销视频选题的任务归属")
    ul.add_argument("topic", help="视频选题 ID")
    ul.set_defaults(func=cmd_unlink)

    # collect-doctor
    cd_parser = subparsers.add_parser(
        "collect-doctor", help="探测各平台采集后端链的可用性",
    )
    cd_parser.add_argument("--platforms", help="只探测这些平台（逗号分隔）")
    cd_parser.set_defaults(func=cmd_collect_doctor)

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
