"""视频 Pipeline 单元测试 —— 覆盖纯逻辑部分，外部调用不在此测试。"""

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
    assert _search_terms_for_scene({"visual_desc": "person typing on laptop"}) == ["person typing on laptop"]


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
