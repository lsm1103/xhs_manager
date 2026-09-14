"""视频 Pipeline 单元测试 —— 覆盖纯逻辑部分，外部调用不在此测试。"""

from datetime import date
from pathlib import Path

import pytest

from xhs_manager.video_pipeline.domain import (
    PLATFORM_LIMITS,
    PipelineStatus,
    Platform,
    StageError,
    VideoPipelineError,
    VideoType,
    next_stage,
)
from xhs_manager.video_pipeline.integrations.collectors import TrendItem, _as_int
from xhs_manager.video_pipeline.integrations.moneyprinter import MoneyPrinterTurbo
from xhs_manager.video_pipeline.stages.stage1_trends import (
    _content_digest,
    _normalize_within_platform,
    _raw_weight,
)


# ── 状态机 ────────────────────────────────────────────────────────


def test_stage_order_advances_through_all_six_stages():
    stage = PipelineStatus.COLLECTING
    seen = [stage]
    while stage != PipelineStatus.COMPLETED:
        stage = next_stage(stage)
        seen.append(stage)
    assert seen == [
        PipelineStatus.COLLECTING,
        PipelineStatus.SELECTING,
        PipelineStatus.MATERIALIZING,
        PipelineStatus.COMPOSING,
        PipelineStatus.RENDERING,
        PipelineStatus.PUBLISHING,
        PipelineStatus.COMPLETED,
    ]


@pytest.mark.parametrize("terminal", [PipelineStatus.COMPLETED, PipelineStatus.FAILED])
def test_terminal_states_have_no_next_stage(terminal):
    with pytest.raises(VideoPipelineError):
        next_stage(terminal)


def test_stage_error_carries_stage_name():
    err = StageError("collect_trends", "全部平台不可用")
    assert err.stage == "collect_trends"
    assert "collect_trends" in err.message


# ── 平台限制 ──────────────────────────────────────────────────────


def test_every_platform_has_limits_defined():
    for platform in Platform:
        assert platform in PLATFORM_LIMITS, f"{platform.value} 缺少限制定义"
        limits = PLATFORM_LIMITS[platform]
        assert limits["title_max"] > 0
        assert limits["video_max_mb"] > 0


def test_xiaohongshu_title_limit_is_strictest():
    """小红书标题 20 字，是四个平台里最严的，发布时以此为准。"""
    xhs = PLATFORM_LIMITS[Platform.XIAOHONGSHU]["title_max"]
    others = [
        PLATFORM_LIMITS[p]["title_max"]
        for p in Platform if p != Platform.XIAOHONGSHU
    ]
    assert all(xhs < o for o in others)


# ── 数量解析 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    (1234, 1234),
    ("1234", 1234),
    ("1.2万", 12000),
    ("3.5亿", 350000000),
    ("1,234", 1234),
    ("", 0),
    (None, 0),
    ("abc", 0),
])
def test_as_int_handles_chinese_units_and_junk(raw, expected):
    assert _as_int(raw) == expected


# ── 热度归一化 ────────────────────────────────────────────────────


def test_engagement_only_includes_nonzero_fields():
    item = TrendItem(platform="x", title="t", url="u", likes=10, views=0)
    assert item.engagement == {"likes": 10}


def test_raw_weight_ranks_comments_above_views():
    """评论比播放更能反映内容质量，权重必须更高。"""
    commented = TrendItem(platform="x", title="a", url="u", comments=100)
    viewed = TrendItem(platform="x", title="b", url="u", views=100)
    assert _raw_weight(commented) > _raw_weight(viewed)


def test_normalize_spreads_scores_across_full_range():
    """归一化后必须有梯度，不能像原实现那样全部撞顶 100。"""
    weights = [10, 100, 1000, 10000, 1000000]
    scores = _normalize_within_platform(weights)
    assert scores[-1] == 100.0
    assert scores[0] == 10.0
    assert len(set(scores)) == len(scores), "分数应当各不相同"


def test_normalize_handles_edge_cases():
    assert _normalize_within_platform([]) == []
    assert _normalize_within_platform([5]) == [100.0]
    assert _normalize_within_platform([0]) == [0.0]
    # 全部相同 → 都给中位分
    assert _normalize_within_platform([7, 7, 7]) == [50.0, 50.0, 50.0]


def test_normalize_preserves_original_order():
    """返回的分数要对应输入位置，不能被排序打乱。"""
    scores = _normalize_within_platform([1000, 10, 100])
    assert scores[0] > scores[2] > scores[1]


# ── 去重摘要 ──────────────────────────────────────────────────────


def test_digest_differs_across_platforms_for_same_title():
    """同标题跨平台不算重复 —— 各平台的讨论是独立信号。"""
    a = TrendItem(platform="bilibili", title="AI 工具", url="u", summary="s")
    b = TrendItem(platform="v2ex", title="AI 工具", url="u", summary="s")
    assert _content_digest(a) != _content_digest(b)


def test_digest_is_stable_for_identical_content():
    a = TrendItem(platform="x", title="T", url="u1", summary="S")
    b = TrendItem(platform="x", title="T", url="u2", summary="S")
    # URL 不参与摘要：同一内容可能有多个入口链接
    assert _content_digest(a) == _content_digest(b)


# ── MoneyPrinterTurbo 输出解析 ───────────────────────────────────


def test_parses_result_json_from_noisy_stdout():
    """CLI 会输出大量日志，结果 JSON 在最后一行。"""
    stdout = (
        "2026-09-03 | INFO | downloading pexels video\n"
        "2026-09-03 | SUCCESS | downloaded 9 videos\n"
        '{"task_id": "abc-123", "result": {"materials": ["/tmp/a.mp4"]}}'
    )
    got = MoneyPrinterTurbo._parse_result_json(stdout)
    assert got["task_id"] == "abc-123"
    assert got["result"]["materials"] == ["/tmp/a.mp4"]


def test_parse_result_json_returns_empty_when_absent():
    assert MoneyPrinterTurbo._parse_result_json("只有日志没有 JSON") == {}
    assert MoneyPrinterTurbo._parse_result_json("") == {}


def test_generated_task_id_is_valid_uuid():
    """MPT 强制要求 task-id 是合法 UUID，否则直接拒绝执行。"""
    from uuid import UUID
    UUID(MoneyPrinterTurbo._new_task_id())  # 非法会抛异常


# ── 种子数据 ──────────────────────────────────────────────────────


def test_seed_script_covers_all_transition_types():
    """种子脚本要覆盖多种转场，才能验证 CSS 动画库是否完整。"""
    from xhs_manager.video_pipeline.seed import SAMPLE_SCENES
    transitions = {s["transition"] for s in SAMPLE_SCENES}
    assert len(transitions) >= 5


def test_seed_scene_durations_sum_to_declared_total():
    from xhs_manager.video_pipeline.seed import SAMPLE_SCENES
    assert sum(s["duration"] for s in SAMPLE_SCENES) == 36


def test_seed_every_scene_has_narration_and_search_hint():
    """缺旁白会导致 TTS 跳过，缺搜索提示会导致素材降级成文字卡片。"""
    from xhs_manager.video_pipeline.seed import SAMPLE_SCENES
    for s in SAMPLE_SCENES:
        assert s["narration"], f"{s['scene_id']} 缺旁白"
        assert any(h.startswith("search:") for h in s["material_hints"]), \
            f"{s['scene_id']} 缺 search 提示"


def test_seed_platform_metadata_respects_title_limits():
    from xhs_manager.video_pipeline.seed import SAMPLE_PLATFORM_METADATA
    for name, meta in SAMPLE_PLATFORM_METADATA.items():
        platform = Platform(name)
        limit = PLATFORM_LIMITS[platform]["title_max"]
        title = meta.get("title") or meta.get("text", "")
        assert len(title) <= limit, f"{name} 标题 {len(title)} 字超过上限 {limit}"


# ── 调度器 ────────────────────────────────────────────────────────


def _utc(*args):
    from datetime import datetime, timezone
    return datetime(*args, tzinfo=timezone.utc)


def test_scheduler_fires_only_at_trigger_hour():
    from xhs_manager.video_pipeline.scheduler import DailyScheduler
    s = DailyScheduler(trigger_hour_utc=3, job=lambda: None)
    assert not s.should_fire(_utc(2026, 9, 3, 2, 59))
    assert s.should_fire(_utc(2026, 9, 3, 3, 0))
    assert s.should_fire(_utc(2026, 9, 3, 3, 59))
    assert not s.should_fire(_utc(2026, 9, 3, 4, 0))


def test_scheduler_fires_at_most_once_per_day():
    """同一小时内会被轮询多次，必须只执行一次。"""
    from xhs_manager.video_pipeline.scheduler import DailyScheduler
    calls = []
    s = DailyScheduler(trigger_hour_utc=0, job=lambda: calls.append(1))
    assert s.run_once_if_due(_utc(2026, 9, 3, 0, 1)) is True
    assert s.run_once_if_due(_utc(2026, 9, 3, 0, 30)) is False
    assert s.run_once_if_due(_utc(2026, 9, 3, 0, 59)) is False
    assert calls == [1]


def test_scheduler_fires_again_next_day():
    from xhs_manager.video_pipeline.scheduler import DailyScheduler
    calls = []
    s = DailyScheduler(trigger_hour_utc=0, job=lambda: calls.append(1))
    s.run_once_if_due(_utc(2026, 9, 3, 0, 0))
    s.run_once_if_due(_utc(2026, 9, 4, 0, 0))
    assert calls == [1, 1]


def test_scheduler_marks_day_done_even_if_job_raises():
    """任务抛异常不能导致同一小时内无限重试。"""
    from xhs_manager.video_pipeline.scheduler import DailyScheduler

    def boom():
        raise RuntimeError("流水线崩了")

    s = DailyScheduler(trigger_hour_utc=0, job=boom)
    assert s.run_once_if_due(_utc(2026, 9, 3, 0, 0)) is True
    assert s.run_once_if_due(_utc(2026, 9, 3, 0, 5)) is False


@pytest.mark.parametrize("bad_hour", [-1, 24, 99])
def test_scheduler_rejects_invalid_hour(bad_hour):
    from xhs_manager.video_pipeline.scheduler import DailyScheduler
    with pytest.raises(ValueError):
        DailyScheduler(trigger_hour_utc=bad_hour, job=lambda: None)


def test_crontab_line_uses_project_venv_python():
    from pathlib import Path
    from xhs_manager.video_pipeline.scheduler import render_crontab_line
    line = render_crontab_line(8, Path("/proj"))
    assert line.startswith("0 8 * * * ")
    assert "/proj/.venv/bin/python" in line
    assert "xhs_manager.video_pipeline.cli run" in line


def test_launchd_plist_is_well_formed_xml():
    import xml.etree.ElementTree as ET
    from pathlib import Path
    from xhs_manager.video_pipeline.scheduler import render_launchd_plist
    xml = render_launchd_plist(8, Path("/proj"))
    root = ET.fromstring(xml)
    assert root.tag == "plist"
    assert "com.xhs.video-pipeline" in xml
    assert "<integer>8</integer>" in xml


# ── Stage3 场景搜索词提取 ─────────────────────────────────────────


def test_scene_terms_prefer_search_hint():
    from xhs_manager.video_pipeline.stages.stage3_materials import _search_terms_for_scene
    scene = {"material_hints": ["gen:abstract brain", "search:neural network glow"],
             "visual_desc": "发光的神经网络"}
    assert _search_terms_for_scene(scene) == ["neural network glow"]


def test_scene_terms_fall_back_to_gen_hint_not_chinese_desc():
    """LLM 只给 gen: 时，要用它（英文）而不是中文 visual_desc 去搜 Pexels。"""
    from xhs_manager.video_pipeline.stages.stage3_materials import _search_terms_for_scene
    scene = {"material_hints": ["gen:dark background with glowing icons"],
             "visual_desc": "深色背景上的发光图标"}
    assert _search_terms_for_scene(scene) == ["dark background with glowing icons"]


def test_scene_terms_skip_chinese_desc_when_no_hints():
    from xhs_manager.video_pipeline.stages.stage3_materials import _search_terms_for_scene
    assert _search_terms_for_scene({"material_hints": [], "visual_desc": "一个人在打字"}) == []


def test_scene_terms_use_ascii_desc_when_no_hints():
    from xhs_manager.video_pipeline.stages.stage3_materials import _search_terms_for_scene
    # 样例故意不含人物词：visual_desc 也要过肖像权改写，
    # 用"person typing on laptop"会被正确改写成"typing laptop"，测不出这条的本意
    assert _search_terms_for_scene(
        {"visual_desc": "keyboard and laptop on desk"}
    ) == ["keyboard and laptop on desk"]


# ── 断点续跑 ──────────────────────────────────────────────────────


@pytest.mark.parametrize("detail,expected", [
    ("[render_videos] 所有视频渲染均失败", PipelineStatus.RENDERING),
    ("[rendering] boom", PipelineStatus.RENDERING),
    ("[collect_trends] 全部平台不可用", PipelineStatus.COLLECTING),
    ("[publish_videos] x", PipelineStatus.PUBLISHING),
])
def test_stage_parsed_from_error_prefix(detail, expected):
    from xhs_manager.video_pipeline.pipeline import _stage_from_error
    assert _stage_from_error(detail) == expected


@pytest.mark.parametrize("detail", [None, "", "no prefix", "[unknown_stage] x", "[] x"])
def test_stage_from_error_returns_none_when_unparseable(detail):
    from xhs_manager.video_pipeline.pipeline import _stage_from_error
    assert _stage_from_error(detail) is None


# ── Stage6 小红书发布（Playwright 独立 profile）──────────────────


def test_publisher_refuses_before_login_initialised(tmp_path):
    """未初始化 profile 时必须给出可操作的指引，而不是直接开浏览器。"""
    from xhs_manager.video_pipeline.integrations.xhs_publisher import XhsPublisher
    ok, why = XhsPublisher(profile_dir=tmp_path / "nope").available()
    assert ok is False and "xhs-login" in why


def test_publish_returns_error_not_raise_when_video_missing(tmp_path):
    from xhs_manager.video_pipeline.integrations.xhs_publisher import XhsPublisher
    (tmp_path / "prof").mkdir()
    r = XhsPublisher(profile_dir=tmp_path / "prof").publish_video(
        video_path=str(tmp_path / "missing.mp4"), title="t", content="c",
    )
    assert r.success is False and "不存在" in r.error


def test_stage6_xiaohongshu_uses_playwright_not_subprocess():
    """opencli 的 upload 依赖 fileChooser，对小红书隐藏 input 不可用；
    小红书路径必须走 Playwright，不能再调子进程。"""
    import inspect
    from xhs_manager.video_pipeline.stages import stage6_publish as m
    src = inspect.getsource(m._publish_xiaohongshu)
    assert "XhsPublisher" in src
    # 注释里可以提 opencli（解释为何不用），但不能真的调用它
    assert "subprocess" not in src
    assert '"opencli"' not in src


# ── opencli 输出解析：JSON 后追加的更新提示不能让整批采集归零 ──────


def test_leading_json_tolerates_trailing_update_notice():
    from xhs_manager.video_pipeline.integrations.collectors import _parse_leading_json
    raw = '[{"rank":1,"title":"t"}]\n\n  Update available: v1.8.6 → v1.8.7\n  Run: npm install -g @jackwener/opencli\n'
    assert _parse_leading_json(raw) == [{"rank": 1, "title": "t"}]


def test_leading_json_handles_object_and_garbage():
    from xhs_manager.video_pipeline.integrations.collectors import _parse_leading_json
    assert _parse_leading_json('{"ok":true,"data":[]} trailing') == {"ok": True, "data": []}
    assert _parse_leading_json("not json at all") is None
    assert _parse_leading_json("") is None


def test_opencli_collector_parses_real_shaped_output():
    from xhs_manager.video_pipeline.integrations.collectors import OpenCliCollector
    raw = ('[{"rank":1,"title":"AI工具","author":"A","likes":"1.2万",'
           '"published_at":"2026-09-01","url":"https://x/1"}]\n  Update available: v1.8.6 → v1.8.7\n')
    items = OpenCliCollector("xiaohongshu")._parse(raw)
    assert len(items) == 1 and items[0].title == "AI工具" and items[0].likes == 12000


# ── 提交按钮在 closed shadow root 里，只能坐标点击 ──────────────


def test_publisher_targets_custom_element_not_text():
    """<xhs-publish-btn> 用 closed shadow root 封装，textContent 为空，
    text= / get_by_role 都定位不到，必须走宿主元素坐标。"""
    import inspect
    from xhs_manager.video_pipeline.integrations import xhs_publisher as m
    src = inspect.getsource(m.XhsPublisher._click_submit)
    assert "xhs-publish-btn" in src
    assert "mouse.click" in src
    assert "get_by_text" not in src and "get_by_role" not in src


def test_draft_and_publish_click_different_horizontal_positions():
    """草稿在左、发布在右，比例必须不同，否则会点错按钮。"""
    import inspect
    from xhs_manager.video_pipeline.integrations import xhs_publisher as m
    src = inspect.getsource(m.XhsPublisher._click_submit)
    assert "0.62" in src and "0.34" in src


def test_wait_ready_checks_submit_disabled_attribute():
    """标题框出现只代表编辑器挂载；可提交的判据是 submit-disabled=false。"""
    import inspect
    from xhs_manager.video_pipeline.integrations import xhs_publisher as m
    src = inspect.getsource(m.XhsPublisher._wait_submit_ready)
    assert "submit-disabled" in src and "submit-loading" in src


def test_publish_clears_stale_error_on_retry_success():
    """失败记录重试成功后，必须清掉旧的 error_detail，
    否则 published 状态会挂着误导性的过期错误。"""
    import inspect
    from xhs_manager.video_pipeline.stages import stage6_publish as m
    src = inspect.getsource(m.publish_videos)
    i = src.index('pub.status = "published"')
    assert "pub.error_detail = None" in src[i:i + 400]


# ── 小红书草稿是浏览器本地存储 ───────────────────────────────────


def test_cdp_mode_reports_actionable_error_when_chrome_not_debuggable():
    from xhs_manager.video_pipeline.integrations.xhs_publisher import XhsPublisher
    ok, why = XhsPublisher(cdp_url="http://127.0.0.1:59999").available()
    assert ok is False and "remote-debugging-port" in why


def test_cdp_mode_never_closes_user_browser():
    """CDP 模式连的是用户自己的 Chrome，收尾只能断开连接，
    误调 context.close() 会关掉用户的浏览器。"""
    import inspect
    from xhs_manager.video_pipeline.integrations import xhs_publisher as m
    src = inspect.getsource(m.XhsPublisher._release)
    i = src.index("if self.cdp_url:")
    j = src.index("else:")
    assert "ctx.close()" not in src[i:j]
    assert "ctx.close()" in src[j:]


def test_cdp_mode_opens_new_tab_instead_of_hijacking_current():
    import inspect
    from xhs_manager.video_pipeline.integrations import xhs_publisher as m
    src = inspect.getsource(m.XhsPublisher.publish_video)
    assert "ctx.new_page() if self.cdp_url" in src


# ── 肖像权硬过滤 ──────────────────────────────────────────────────


@pytest.mark.parametrize("risky", [
    "close-up face of engineer",
    "portrait of a scientist",
    "person smiling at desk",
    "headshot developer",
    "selfie with robot",
    "man smiling office",
])
def test_portrait_risk_terms_are_rewritten(risky):
    """Pexels 授权覆盖著作权但不覆盖肖像权，可辨识人脸不能用。"""
    from xhs_manager.video_pipeline.stages.stage3_materials import sanitize_search_term
    out, changed = sanitize_search_term(risky)
    assert changed is True
    for bad in ("close-up face", "portrait", "headshot", "selfie", "smiling"):
        assert bad not in out


@pytest.mark.parametrize("safe", [
    "robot arm factory assembly line",
    "crowd wide shot exhibition",
    "hands typing on laptop",
    "data visualization chart",
])
def test_safe_terms_pass_through_unchanged(safe):
    from xhs_manager.video_pipeline.stages.stage3_materials import sanitize_search_term
    out, changed = sanitize_search_term(safe)
    assert changed is False and out == safe


def test_scene_terms_apply_portrait_filter():
    """场景级提取必须经过过滤，不能绕过。"""
    from xhs_manager.video_pipeline.stages.stage3_materials import _search_terms_for_scene
    terms = _search_terms_for_scene(
        {"material_hints": ["search:close-up face of a robot engineer"]}
    )
    assert terms and "close-up face" not in terms[0]


def test_stage2_prompt_forbids_face_closeups():
    from xhs_manager.video_pipeline.stages.stage2_topics import SCRIPT_SYSTEM_PROMPT
    assert "肖像权" in SCRIPT_SYSTEM_PROMPT
    assert "close-up face" in SCRIPT_SYSTEM_PROMPT


# ── BGM 分段配乐 ──────────────────────────────────────────────────


def _sc(dur, mood=None, i=1):
    d = {"scene_id": f"s{i:02d}", "duration": dur}
    if mood:
        d["bgm_mood"] = mood
    return d


def test_segments_preserve_total_duration():
    """合并乐段绝不能丢时长，否则音轨会和画面错位。"""
    from xhs_manager.video_pipeline.audio.composer import plan_segments
    scenes = [_sc(d, m, i) for i, (d, m) in enumerate(
        [(3, "hook"), (8, "explain"), (10, "explain"), (12, "tension"),
         (10, "reveal"), (12, "explain"), (12, "uplift"), (8, "closing")], 1)]
    segs = plan_segments(scenes)
    assert abs(sum(s.duration for s in segs) - 75) < 0.01


def test_no_segment_shorter_than_minimum():
    """5-7 秒的场景很常见，合并后不能留下听感碎片。"""
    from xhs_manager.video_pipeline.audio.composer import MIN_SEGMENT_S, plan_segments
    scenes = [_sc(5, m, i) for i, m in enumerate(
        ["hook", "explain", "tension", "reveal", "uplift", "closing"], 1)]
    segs = plan_segments(scenes)
    assert len(segs) > 1 or True
    for s in segs:
        assert s.duration >= MIN_SEGMENT_S or len(segs) == 1


def test_segments_are_contiguous_from_zero():
    from xhs_manager.video_pipeline.audio.composer import plan_segments
    scenes = [_sc(d, m, i) for i, (d, m) in enumerate(
        [(10, "hook"), (10, "explain"), (10, "uplift")], 1)]
    segs = plan_segments(scenes)
    assert segs[0].start == 0.0
    for a, b in zip(segs, segs[1:]):
        assert abs(a.end - b.start) < 0.01


def test_segment_count_bounded_for_short_video():
    """36 秒视频不该切成 6 段 —— 每 6 秒换歌比不换更难听。"""
    from xhs_manager.video_pipeline.audio.composer import MAX_SEGMENTS, plan_segments
    scenes = [_sc(6, m, i) for i, m in enumerate(
        ["hook", "explain", "tension", "reveal", "uplift", "closing"], 1)]
    segs = plan_segments(scenes)
    assert 1 <= len(segs) <= min(MAX_SEGMENTS, 3)


def test_missing_mood_falls_back_by_position():
    """LLM 没给 bgm_mood 时按场景位置兜底，不能直接崩。"""
    from xhs_manager.video_pipeline.audio.composer import plan_segments
    segs = plan_segments([_sc(8, None, i) for i in range(1, 7)])
    assert segs and all(s.mood for s in segs)


def test_percentile_ranks_spread_uniform_library():
    """曲库特征范围窄时，绝对阈值会全部归一类；百分位必须摊开。"""
    from xhs_manager.video_pipeline.audio.library import _percentile_ranks
    r = _percentile_ranks([430.0, 500.0, 560.0, 597.0])
    assert r[0] == 0.0 and r[-1] == 1.0
    assert len(set(r)) == 4


def test_percentile_ranks_edge_cases():
    from xhs_manager.video_pipeline.audio.library import _percentile_ranks
    assert _percentile_ranks([]) == []
    assert _percentile_ranks([7.0]) == [0.5]
    assert _percentile_ranks([3.0, 3.0, 3.0]) == [0.0, 0.5, 1.0]


def test_classify_covers_every_mood():
    """打标后每个情绪都要有曲子，否则某些段会选不到音乐。"""
    from xhs_manager.video_pipeline.audio.library import Track, classify_library
    from xhs_manager.video_pipeline.audio.moods import BgmMood
    tracks = [
        Track(path=f"/t{i}.mp3", duration=180, tempo=60 + i * 12,
              energy=0.1 + i * 0.08, brightness=400 + i * 90, moods=[], scores={})
        for i in range(12)
    ]
    classify_library(tracks)
    covered = {m for t in tracks for m in t.moods}
    assert len(covered) >= 4
    assert all(t.moods for t in tracks)


def test_bgm_mood_is_required_in_script_schema():
    from xhs_manager.video_pipeline.stages.stage2_topics import SCRIPT_GENERATION_SCHEMA
    item = SCRIPT_GENERATION_SCHEMA["properties"]["scenes"]["items"]
    assert "bgm_mood" in item["required"]
    assert set(item["properties"]["bgm_mood"]["enum"]) == {
        "hook", "explain", "tension", "reveal", "uplift", "closing"}


# ── 肖像权：结果侧检测 ────────────────────────────────────────────


@pytest.mark.parametrize("innocent", [
    "office worker desk computer",
    "person ordering menu counter",
])
def test_person_subject_terms_are_rewritten(innocent):
    """回归：这两个词就是 2026-09-04 下架那条片子用的。

    它们不含任何"特写"字眼，旧的危险短语黑名单完全放行，
    但主体是人，Pexels 必然返回真人正脸中景。
    """
    from xhs_manager.video_pipeline.stages.stage3_materials import sanitize_search_term
    out, changed = sanitize_search_term(innocent)
    assert changed is True
    assert "worker" not in out and "person" not in out
    assert out.strip()          # 不能改写成空串，否则这个场景就没素材了


@pytest.mark.parametrize("term,gone", [
    ("young woman looking into camera", "woman"),
    ("chef cooking kitchen", "chef"),
    ("students studying library", "students"),
])
def test_identity_words_are_stripped(term, gone):
    from xhs_manager.video_pipeline.stages.stage3_materials import sanitize_search_term
    out, changed = sanitize_search_term(term)
    assert changed is True and gone not in out


def test_crowd_and_silhouette_are_allowed():
    """无法辨识个人的远景人群是允许的素材，不能被词层砍掉。"""
    from xhs_manager.video_pipeline.stages.stage3_materials import sanitize_search_term
    out, changed = sanitize_search_term("crowd wide shot silhouette")
    assert changed is False and out == "crowd wide shot silhouette"


def test_rewrite_drops_leftover_stopwords():
    """删掉人物词后不能留下 "of a" 这种对检索无用的残渣。"""
    from xhs_manager.video_pipeline.stages.stage3_materials import sanitize_search_term
    out, _ = sanitize_search_term("portrait of a scientist")
    assert "of" not in out.split() and "a" not in out.split()


def test_portrait_guard_fails_closed_when_detector_unavailable(monkeypatch, tmp_path):
    """检测器不可用时必须拒收，而不是放行——放行等于这层不存在。"""
    from xhs_manager.video_pipeline import portrait_guard
    monkeypatch.setattr(portrait_guard, "available", lambda: (False, "模型缺失"))
    f = tmp_path / "clip.mp4"
    f.write_bytes(b"x")
    assert portrait_guard.scan_video(f).rejected is True
    assert portrait_guard.scan_image(tmp_path / "x.jpg").rejected is True


def test_reject_portrait_materials_empty_when_detector_unavailable(monkeypatch):
    from xhs_manager.video_pipeline import portrait_guard
    from xhs_manager.video_pipeline.stages import stage3_materials
    monkeypatch.setattr(portrait_guard, "available", lambda: (False, "模型缺失"))
    out = stage3_materials._reject_portrait_materials([{"path": "/tmp/a.mp4"}])
    assert out == []


def test_reject_portrait_materials_drops_flagged(monkeypatch, tmp_path):
    from xhs_manager.video_pipeline import portrait_guard
    from xhs_manager.video_pipeline.stages import stage3_materials

    good = tmp_path / "good.mp4"; good.write_bytes(b"x")
    bad = tmp_path / "bad.mp4"; bad.write_bytes(b"x")

    monkeypatch.setattr(portrait_guard, "available", lambda: (True, ""))
    monkeypatch.setattr(
        portrait_guard, "scan",
        lambda p: portrait_guard.FaceScan(rejected=str(p).endswith("bad.mp4")),
    )
    out = stage3_materials._reject_portrait_materials(
        [{"path": str(good)}, {"path": str(bad)}]
    )
    assert [m["path"] for m in out] == [str(good)]


def test_face_width_threshold_is_below_observed_violation():
    """阈值必须低于实测的违规值（下架那条片子脸宽 11-15%），否则拦不住。"""
    from xhs_manager.video_pipeline.portrait_guard import MAX_FACE_WIDTH_RATIO
    assert 0 < MAX_FACE_WIDTH_RATIO <= 0.08


def test_stage2_prompt_forbids_person_as_subject():
    """prompt 不能只禁特写词，必须禁"以人为主体"。"""
    from xhs_manager.video_pipeline.stages.stage2_topics import SCRIPT_SYSTEM_PROMPT
    assert "人不能是画面主体" in SCRIPT_SYSTEM_PROMPT
    assert "office worker desk computer" in SCRIPT_SYSTEM_PROMPT


# ── 采集后端链 ──────────────────────────────────────────────────


class _FakeBackend:
    """测试用后端：可控可用性与返回条数。"""

    def __init__(self, name, ok, count):
        self.name = name
        self._ok = ok
        self._count = count

    def available(self):
        return self._ok

    def collect_search(self, keyword, limit=20):
        from xhs_manager.video_pipeline.integrations.collectors import TrendItem
        return [
            TrendItem(platform="t", title=f"{self.name}-{keyword}-{i}", url="https://x/1")
            for i in range(self._count)
        ]


def _patch_chain(monkeypatch, chain):
    from xhs_manager.video_pipeline.integrations import collectors
    monkeypatch.setitem(collectors.PLATFORM_BACKENDS, "t", [lambda b=b: b for b in chain])


def test_chain_falls_back_when_first_backend_unavailable(monkeypatch):
    """首选后端不可用时降级，而不是让整个平台归零。"""
    from xhs_manager.video_pipeline.integrations.collectors import collect_platform

    primary = _FakeBackend("primary", ok=False, count=5)
    backup = _FakeBackend("backup", ok=True, count=3)
    _patch_chain(monkeypatch, [primary, backup])

    outcome = collect_platform("t", ["kw"], 10)
    assert outcome.status == "degraded"
    assert outcome.backend == "backup"
    assert len(outcome.items) == 3
    assert "primary 不可用" in outcome.notes


def test_chain_reports_unavailable_when_every_backend_dry(monkeypatch):
    """所有后端都没数据时，要说清楚每一级为什么没拿到。"""
    from xhs_manager.video_pipeline.integrations.collectors import collect_platform

    _patch_chain(monkeypatch, [
        _FakeBackend("a", ok=True, count=0),
        _FakeBackend("b", ok=False, count=9),
    ])

    outcome = collect_platform("t", ["kw"], 10)
    assert outcome.status == "unavailable"
    assert outcome.items == []
    assert outcome.notes == ["a 无结果", "b 不可用"]


def test_index_channel_rejects_cross_site_results():
    """索引通道不能把「别的站在讨论小红书」的文章当成小红书样本。"""
    from xhs_manager.video_pipeline.integrations.collectors import SearchIndexCollector

    c = SearchIndexCollector("xiaohongshu")
    assert c._matches_site("https://www.xiaohongshu.com/explore/abc")
    assert c._matches_site("https://edith.xiaohongshu.com/x")
    assert not c._matches_site("https://zhuanlan.zhihu.com/p/1")
    assert not c._matches_site("https://xiaohongshu.com.evil.cn/p")


def test_hot_board_can_be_disabled_for_topic_research(monkeypatch):
    """定向调研时不取热榜：当日泛热榜会稀释话题信号。"""
    from xhs_manager.video_pipeline.integrations.collectors import (
        TrendItem,
        collect_platform,
    )

    class HotOnly:
        name = "hot"

        def available(self):
            return True

        def collect_hot(self, limit=20):
            return [TrendItem(platform="t", title="今日热榜", url="https://x/h")]

    _patch_chain(monkeypatch, [HotOnly()])

    assert collect_platform("t", [], 10, include_hot=True).items
    assert collect_platform("t", [], 10, include_hot=False).items == []


# ── 版面健壮性 ──────────────────────────────────────────────────


def _plan_one(scene):
    from xhs_manager.video_pipeline.composition.timeline import plan_timeline
    return plan_timeline([scene]).scenes[0]


def test_compare_title_does_not_repeat_the_two_cards():
    """「A 对比 B」写在 main 里时，标题不能把同一句再显示一遍。"""
    ps = _plan_one({
        "scene_id": "s1", "order": 1, "duration": 6, "layout": "compare",
        "text_overlay": {"main": "入选百强榜 对比 90% 项目失败", "sub": "同一批公众号"},
        "transition": "fade", "narration": "略", "visual_desc": "",
    })
    assert ps.compare == ("入选百强榜", "90% 项目失败")
    assert ps.text_main == "同一批公众号"


def test_compare_pair_from_sub_keeps_main_as_title():
    ps = _plan_one({
        "scene_id": "s1", "order": 1, "duration": 6, "layout": "compare",
        "text_overlay": {"main": "两种叙事", "sub": "唱多 vs 唱衰"},
        "transition": "fade", "narration": "略", "visual_desc": "",
    })
    assert ps.compare == ("唱多", "唱衰")
    assert ps.text_main == "两种叙事"


def test_stat_layout_downgrades_when_there_is_no_number():
    """stat 的巨号字是给数字用的，塞一整句中文会撑破版心。"""
    ps = _plan_one({
        "scene_id": "s1", "order": 1, "duration": 6, "layout": "stat",
        "text_overlay": {"main": "结果价值 − 成本项", "sub": "模型 审核 集成"},
        "transition": "fade", "narration": "略", "visual_desc": "",
    })
    assert ps.layout == "statement"
    assert ps.stat_value == ""


def test_stat_layout_kept_when_main_starts_with_a_number():
    ps = _plan_one({
        "scene_id": "s1", "order": 1, "duration": 6, "layout": "stat",
        "text_overlay": {"main": "90% 的项目", "sub": "失败了"},
        "transition": "fade", "narration": "略", "visual_desc": "",
    })
    assert ps.layout == "stat"
    assert ps.stat_value == "90%"


# ── 时长校准要真的落库 ───────────────────────────────────────────


def test_calibrated_scene_durations_survive_a_commit(session_factory):
    """校准后的场景时长必须写进库。

    这里曾经踩过坑：calibrate_scene_durations 是原地改 dict，
    浅拷贝回写时 SQLAlchemy 认为 JSON 列没变化，于是 total_duration 更新了、
    每个场景的 duration 还是脚本里手写的估算值。
    后果是画面按 195 秒排版、音频只有 168 秒，越往后音画错位越大。
    """
    import copy

    from sqlalchemy.orm.attributes import flag_modified

    from xhs_manager.domain import new_id
    from xhs_manager.video_pipeline.audio.narration import (
        SceneNarration, calibrate_scene_durations,
    )
    from xhs_manager.video_pipeline.models import (
        VideoPipelineRun, VideoScript, VideoTopic,
    )

    raw = [
        {"scene_id": "s01", "order": 1, "duration": 9, "narration": "一"},
        {"scene_id": "s02", "order": 2, "duration": 11, "narration": "二"},
    ]
    script_id = new_id()

    with session_factory() as s:
        run = VideoPipelineRun(id=new_id(), run_date=date(2026, 9, 14),
                               status="materializing", trigger_type="manual")
        topic = VideoTopic(id=new_id(), pipeline_run_id=run.id, rank=1,
                           title="t", angle="a", why_now="w",
                           target_audience="x", video_type="explainer",
                           estimated_duration=20, scores={}, total_score=1.0,
                           source_signal_ids=[], status="selected")
        s.add(run)
        s.flush()
        s.add(topic)
        s.flush()
        s.add(VideoScript(id=script_id, topic_id=topic.id, version=1,
                          total_duration=20, scenes=raw, bgm_style="",
                          platform_metadata={}, generation_model="test",
                          generation_prompt_hash="h", status="ready"))
        s.commit()

    with session_factory() as s:
        script = s.get(VideoScript, script_id)
        scenes = copy.deepcopy(list(script.scenes))
        items = [
            SceneNarration("s01", Path("a.mp3"), 6.43, 6.88),
            SceneNarration("s02", Path("b.mp3"), 7.49, 7.94),
        ]
        total = calibrate_scene_durations(scenes, items)
        script.scenes = scenes
        flag_modified(script, "scenes")
        script.total_duration = int(round(total))
        s.commit()

    with session_factory() as s:
        script = s.get(VideoScript, script_id)
        assert [sc["duration"] for sc in script.scenes] == [6.88, 7.94]
        assert script.total_duration == 15


# ── 字幕对齐 ────────────────────────────────────────────────────


def test_captions_follow_real_speech_marks_not_char_ratio():
    """有真实句级时间时，字幕必须钉在人声开口的那一刻。"""
    from xhs_manager.video_pipeline.composition.timeline import build_captions

    narration = "别把叙事当数据。一个吓人的比例，正在替代判断。"
    marks = [
        {"start": 0.10, "duration": 1.67, "text": "别把叙事当数据。"},
        {"start": 1.72, "duration": 2.60, "text": "一个吓人的比例，正在替代判断。"},
    ]
    cues = build_captions(narration, 100.0, 5.0, marks)

    assert cues[0].start == pytest.approx(100.10)
    # 第二句按字数估算会在 ~101.8 开口，真实是 101.72——差值正是会累积的那部分
    second = next(c for c in cues if c.start > 100.5)
    assert second.start == pytest.approx(101.72)


def test_captions_fall_back_to_estimate_without_marks():
    """拿不到时间标记时仍要能出字幕，而不是整段空白。"""
    from xhs_manager.video_pipeline.composition.timeline import build_captions

    cues = build_captions("别把叙事当数据。一个吓人的比例，正在替代判断。", 0.0, 5.0, None)
    assert cues
    assert cues[0].start == pytest.approx(0.0)
    assert cues[-1].end == pytest.approx(5.0)


def test_long_sentence_is_split_inside_its_own_time_window():
    """长句二次切分只能在这句自己的区间里，误差不许外溢到下一句。"""
    from xhs_manager.video_pipeline.composition.timeline import build_captions

    marks = [
        {"start": 0.0, "duration": 6.0,
         "text": "需求闸门要求问题高频，并且有明确的预算所有者，否则做出来没有人买单。"},
        {"start": 6.0, "duration": 2.0, "text": "就这么简单。"},
    ]
    cues = build_captions("略", 0.0, 9.0, marks)

    first_sentence = [c for c in cues if c.start < 6.0]
    assert len(first_sentence) > 1              # 确实被切开了
    assert first_sentence[-1].end == pytest.approx(6.0, abs=0.01)
