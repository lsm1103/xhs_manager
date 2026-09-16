"""控制台（P0 只读视图）测试。"""

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from xhs_manager.api import create_app
from xhs_manager.console import queries
from xhs_manager.domain import new_id
from xhs_manager.video_pipeline.models import (
    VideoComposition,
    VideoPipelineRun,
    VideoPublication,
    VideoRender,
    VideoScript,
    VideoTopic,
)


def _scene(sid, dur, marks=0):
    return {
        "scene_id": sid, "order": 1, "duration": dur,
        "visual_desc": "", "transition": "fade", "layout": "statement",
        "text_overlay": {"main": f"{sid} 主标题", "sub": "副标题"},
        "narration": "一段旁白。",
        **({"speech_marks": [{"start": 0, "duration": dur, "text": "一段旁白。"}] * marks}
           if marks else {}),
    }


@pytest.fixture
def video_task(session_factory):
    """造一条完整的视频任务：run → topic → script → composition → render。"""
    ids = {}
    with session_factory() as s:
        run = VideoPipelineRun(
            id=new_id(), run_date=date(2026, 9, 14), status="publishing",
            trigger_type="manual", trend_count=26, topic_count=1, video_count=1,
        )
        s.add(run)
        s.flush()
        topic = VideoTopic(
            id=new_id(), pipeline_run_id=run.id, rank=1, title="测试选题",
            angle="角度", why_now="时机", target_audience="读者",
            video_type="explainer", estimated_duration=120,
            scores={}, total_score=8.0, source_signal_ids=[], status="selected",
        )
        s.add(topic)
        s.flush()
        script = VideoScript(
            id=new_id(), topic_id=topic.id, version=1, total_duration=120,
            scenes=[_scene("s01", 60.0, marks=2), _scene("s02", 60.0, marks=1)],
            bgm_style="", platform_metadata={}, generation_model="seed",
            generation_prompt_hash="h", status="ready",
        )
        s.add(script)
        s.flush()
        comp = VideoComposition(
            id=new_id(), script_id=script.id, composition_dir="/tmp/x",
            html_path="/tmp/x/index.html", total_duration=120.0,
            resolution="1080x1920", transition_effects=["fade", "wipe"],
            has_narration=True, has_bgm=True, status="render_ready",
        )
        s.add(comp)
        s.flush()
        render = VideoRender(
            id=new_id(), composition_id=comp.id, output_path="/tmp/x/video.mp4",
            fps=30, duration=120.0, file_size=10 * 1024 * 1024, status="completed",
            completed_at=datetime(2026, 9, 14, 10, 0, tzinfo=timezone.utc),
        )
        s.add(render)
        s.commit()
        ids = {"run": run.id, "topic": topic.id, "script": script.id,
               "render": render.id}
    return ids


def test_rendered_but_unpublished_task_lands_in_the_action_bucket(session_factory, video_task):
    """渲染完成、还没发布的片子是「等你动手」，不能和已完成的混在一起。"""
    with session_factory() as s:
        data = queries.list_tasks(s)

    task = next(t for t in data["tasks"] if t["id"] == video_task["topic"])
    assert task["bucket"] == "act"
    assert task["state_label"] == "待发布"
    assert task["format"] == "video"
    assert task["orphan"] is True          # 还没挂到 content_tasks
    assert "2:00" in task["output"]
    assert data["counts"]["act"] >= 1


def test_failed_publication_moves_the_task_to_the_error_bucket(session_factory, video_task):
    with session_factory() as s:
        s.add(VideoPublication(
            id=new_id(), render_id=video_task["render"], topic_id=video_task["topic"],
            platform="xiaohongshu", title="标题", description="", tags=[],
            publish_method="playwright", status="failed", error_detail="登录态失效",
        ))
        s.commit()

    with session_factory() as s:
        task = next(t for t in queries.list_tasks(s)["tasks"] if t["id"] == video_task["topic"])
    assert task["bucket"] == "err"
    assert task["state_label"] == "发布失败"


def test_tasks_are_ordered_by_bucket_not_by_time(session_factory, video_task):
    """要你动手的排最前面——这是任务台存在的意义。"""
    with session_factory() as s:
        s.add(VideoPublication(
            id=new_id(), render_id=video_task["render"], topic_id=video_task["topic"],
            platform="xiaohongshu", title="标题", description="", tags=[],
            publish_method="playwright", status="published",
        ))
        s.commit()
        # 再造一条「待发布」的，它更旧，但应该排在已发布的前面
        older = _second_topic(s, video_task["run"])
        s.commit()

    with session_factory() as s:
        order = [t["id"] for t in queries.list_tasks(s)["tasks"]]
    assert order.index(older) < order.index(video_task["topic"])


def _second_topic(s, run_id):
    topic = VideoTopic(
        id=new_id(), pipeline_run_id=run_id, rank=2, title="更旧的选题",
        angle="", why_now="", target_audience="", video_type="explainer",
        estimated_duration=60, scores={}, total_score=7.0,
        source_signal_ids=[], status="selected",
        created_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )
    s.add(topic)
    s.flush()
    script = VideoScript(
        id=new_id(), topic_id=topic.id, version=1, total_duration=60,
        scenes=[_scene("s01", 60.0)], bgm_style="", platform_metadata={},
        generation_model="seed", generation_prompt_hash="h", status="ready",
    )
    s.add(script)
    s.flush()
    comp = VideoComposition(
        id=new_id(), script_id=script.id, composition_dir="/tmp/y",
        html_path="/tmp/y/index.html", total_duration=60.0, resolution="1080x1920",
        transition_effects=[], has_narration=True, has_bgm=False, status="render_ready",
    )
    s.add(comp)
    s.flush()
    s.add(VideoRender(
        id=new_id(), composition_id=comp.id, output_path="/tmp/y/video.mp4",
        fps=30, duration=60.0, status="completed",
    ))
    return topic.id


# ── 一致性校验：这一条是为一次真实事故加的 ──────────────────────


def test_consistency_passes_when_all_four_durations_agree(session_factory, video_task):
    with session_factory() as s:
        task = queries.get_task(s, video_task["topic"])
    c = task["consistency"]
    assert c["measured"] == 4
    assert c["pass"] is True
    assert c["drift"] == pytest.approx(0.0)


def test_consistency_catches_the_uncalibrated_duration_bug(session_factory, video_task):
    """场景时长没落库时，画面按 195 秒排、音频只有 168 秒——必须被抓出来。"""
    with session_factory() as s:
        script = s.get(VideoScript, video_task["script"])
        script.scenes = [_scene("s01", 100.0), _scene("s02", 95.0)]   # 合计 195
        s.commit()

    with session_factory() as s:
        c = queries.get_task(s, video_task["topic"])["consistency"]

    assert c["pass"] is False
    assert c["drift"] == pytest.approx(75.0)
    assert [i["value"] for i in c["items"]] == [120.0, 195.0, 120.0, 120.0]


def test_consistency_tolerates_sub_second_encoding_drift(session_factory, video_task):
    """成片时长和脚本差零点几秒是编码取整，不该报警。"""
    with session_factory() as s:
        s.get(VideoRender, s.query(VideoRender).one().id).duration = 120.7
        s.commit()
    with session_factory() as s:
        c = queries.get_task(s, video_task["topic"])["consistency"]
    assert c["pass"] is True


def test_task_detail_exposes_stages_and_scenes(session_factory, video_task):
    with session_factory() as s:
        task = queries.get_task(s, video_task["topic"])

    stages = {s["stage"]: s for s in task["stages"]}
    assert stages["rendering"]["state"] == "done"
    assert stages["rendering"]["output_path"] == "/tmp/x/video.mp4"
    assert stages["composing"]["html_path"] == "/tmp/x/index.html"
    assert len(task["scenes"]) == 2
    assert task["scenes"][0]["marks"] == 2          # 旁白时间标记条数


def test_missing_task_returns_none(session_factory):
    with session_factory() as s:
        assert queries.get_task(s, "does-not-exist") is None


# ── HTTP 层 ────────────────────────────────────────────────


@pytest.fixture
def client(engine, settings):
    """engine 建表，settings 指向同一个库文件。"""
    return TestClient(create_app(settings=settings))


def test_console_page_and_api_are_reachable(client):
    assert client.get("/console").status_code == 200
    body = client.get("/console/api/tasks").json()
    assert "tasks" in body and "counts" in body
    assert client.get("/console/api/runs").json()["runs"] == []


def test_unknown_task_is_404(client):
    assert client.get("/console/api/tasks/nope").status_code == 404


@pytest.mark.parametrize("bad", [
    "../../../etc/passwd",
    "/etc/passwd",
    "../../.env",
])
def test_file_endpoint_refuses_paths_outside_the_output_dir(client, bad):
    """控制台能播成片，但不能变成任意文件读取器。"""
    res = client.get("/console/api/file", params={"path": bad})
    assert res.status_code in (403, 404)


def test_file_endpoint_refuses_unlisted_extensions(client, tmp_path, monkeypatch):
    from xhs_manager.console import app as console_app

    root = tmp_path / "out"
    root.mkdir()
    secret = root / "notes.py"
    secret.write_text("x = 1")
    monkeypatch.setattr(console_app, "_media_root", lambda: root.resolve())

    res = client.get("/console/api/file", params={"path": "notes.py"})
    assert res.status_code == 403


def test_file_endpoint_serves_media_inside_the_output_dir(client, tmp_path, monkeypatch):
    from xhs_manager.console import app as console_app

    root = tmp_path / "out"
    (root / "run").mkdir(parents=True)
    clip = root / "run" / "video.mp4"
    clip.write_bytes(b"\x00\x01\x02")
    monkeypatch.setattr(console_app, "_media_root", lambda: root.resolve())

    res = client.get("/console/api/file", params={"path": "run/video.mp4"})
    assert res.status_code == 200
    assert res.headers["content-type"] == "video/mp4"


def test_file_endpoint_accepts_paths_stored_relative_to_the_project_root(
    client, tmp_path, monkeypatch
):
    """库里存的是 data/video_pipeline/<run>/... —— 相对工作目录，不是相对产物目录。"""
    from xhs_manager.console import app as console_app

    root = tmp_path / "data" / "video_pipeline"
    (root / "run").mkdir(parents=True)
    (root / "run" / "video.mp4").write_bytes(b"\x00")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(console_app, "_media_root", lambda: root.resolve())

    res = client.get("/console/api/file",
                     params={"path": "data/video_pipeline/run/video.mp4"})
    assert res.status_code == 200


# ── 工具体检（P1）──────────────────────────────────────────


def test_a_probe_that_explodes_still_names_itself(monkeypatch):
    """探测器自己炸掉时，页面上要看到是哪一项炸了，不能显示成「未知检查」。"""
    from xhs_manager.console import probes

    def boom():
        raise RuntimeError("网线被拔了")

    p = probes._timed(("ffmpeg", "ffmpeg", boom))
    assert p.key == "ffmpeg"
    assert p.name == "ffmpeg"
    assert p.status == "unknown"
    assert "网线被拔了" in p.detail
    assert p.fix                       # 要说清这是控制台的 bug，不是工具坏了


def test_probes_are_sorted_by_severity(session_factory, settings, monkeypatch):
    """不可用的排最上面——这是工具页存在的唯一意义。"""
    from xhs_manager.console import probes

    fake = [
        probes.Probe("a", "正常项", "ok", ""),
        probes.Probe("b", "坏掉的", "down", ""),
        probes.Probe("c", "降级的", "degraded", ""),
        probes.Probe("d", "说不准的", "unknown", ""),
    ]
    monkeypatch.setattr(probes, "_timed", lambda job: fake.pop(0) if fake else
                        probes.Probe("z", "z", "ok", ""))
    probes.reset_cache()

    with session_factory() as s:
        out = probes.run_all(s, _video_settings())

    got = [p["status"] for p in out["probes"]]
    assert got == sorted(got, key=lambda st: probes.SEVERITY[st])
    assert got[0] == "down"


def test_results_are_cached_and_refresh_bypasses_the_cache(session_factory, monkeypatch):
    """连点刷新不该把 opencli 和各平台再打一遍。"""
    from xhs_manager.console import probes

    calls = {"n": 0}

    def counted(job):
        calls["n"] += 1
        return probes.Probe("x", "x", "ok", "")

    monkeypatch.setattr(probes, "_timed", counted)
    probes.reset_cache()

    with session_factory() as s:
        first = probes.run_all(s, _video_settings())
        after = calls["n"]
        second = probes.run_all(s, _video_settings())
        assert calls["n"] == after            # 命中缓存，没再探测
        assert second["cached"] is True
        assert first["cached"] is False

        probes.run_all(s, _video_settings(), force=True)
        assert calls["n"] > after             # force 绕过缓存


def _video_settings():
    from xhs_manager.video_pipeline.config import VideoPipelineSettings
    return VideoPipelineSettings()


def test_collect_backends_reports_degraded_when_a_platform_falls_back(monkeypatch):
    """首选后端不可用、靠兜底跑起来的平台，要算「降级」而不是「正常」。"""
    from xhs_manager.console import probes

    chains = {
        "bilibili": [("站内 API", True), ("索引", True)],          # 首选就通 → ok
        "zhihu": [("cookie", False), ("索引", True)],              # 靠兜底 → degraded
        "douyin": [("opencli", False), ("索引", False)],           # 全挂 → down
    }
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.integrations.collectors.probe_platform",
        lambda p: chains[p],
    )
    settings = _video_settings()
    settings.trend_platforms = list(chains)

    p = probes.probe_collect_backends(settings)
    assert p.status == "degraded"
    assert p.facts["platforms"] == {"bilibili": "ok", "zhihu": "degraded", "douyin": "down"}
    assert "site-login" in p.fix


def test_collect_backends_is_down_only_when_nothing_works(monkeypatch):
    from xhs_manager.console import probes

    monkeypatch.setattr(
        "xhs_manager.video_pipeline.integrations.collectors.probe_platform",
        lambda p: [("任意后端", False)],
    )
    settings = _video_settings()
    settings.trend_platforms = ["bilibili", "zhihu"]

    assert probes.probe_collect_backends(settings).status == "down"


@pytest.mark.parametrize("free_gb,expected", [(0.5, "down"), (3.0, "degraded"), (50.0, "ok")])
def test_disk_thresholds(monkeypatch, tmp_path, free_gb, expected):
    """渲染中间帧能占好几个 GB，空间见底要在成片失败之前就说。"""
    import shutil as _shutil

    from xhs_manager.console import probes

    settings = _video_settings()
    settings.output_base_dir = str(tmp_path)
    monkeypatch.setattr(
        _shutil, "disk_usage",
        lambda p: type("U", (), {"free": int(free_gb * 1024 ** 3), "total": 0, "used": 0})(),
    )
    assert probes.probe_disk(settings).status == expected


def test_tools_endpoint_returns_sorted_probes(client, monkeypatch):
    from xhs_manager.console import probes

    monkeypatch.setattr(probes, "_timed", lambda job: probes.Probe("x", "x", "ok", ""))
    probes.reset_cache()

    body = client.get("/console/api/tools").json()
    assert body["counts"]["ok"] >= 1
    assert all("elapsed_ms" in p for p in body["probes"])
