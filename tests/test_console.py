"""控制台（P0 只读视图）测试。"""

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

from xhs_manager.api import create_app
from xhs_manager.console import queries
from xhs_manager.domain import new_id, utcnow
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


# ── P2：把视频选题认领进内容任务 ───────────────────────────


@pytest.fixture
def account(session_factory):
    from xhs_manager.video_pipeline import linking
    with session_factory() as s:
        account_id, _ = linking.ensure_default_account(s, name="测试账号")
        s.commit()
    return account_id


def test_adopting_does_not_enqueue_work_for_an_already_finished_video(
    session_factory, video_task, account
):
    """认领的是早就渲染完的片子，绝不能入队让 worker 重做一遍。

    这正是不能直接复用 services.create_content_task 的原因——它会顺手
    入队一个 collect_research。
    """
    from xhs_manager.models import WorkflowInstance, WorkItem
    from xhs_manager.video_pipeline import linking

    with session_factory() as s:
        result = linking.adopt_topic(s, video_task["topic"], account_id=account)
        s.commit()

    assert result.created_task is True
    with session_factory() as s:
        assert s.query(WorkItem).count() == 0
        assert s.query(WorkflowInstance).count() == 0


def test_adopted_task_state_matches_what_the_video_actually_did(
    session_factory, video_task, account
):
    """渲染完成还没发布 → pending_publish_approval，不是 pending_research。"""
    from xhs_manager.domain import TaskState
    from xhs_manager.models import ContentTask
    from xhs_manager.video_pipeline import linking

    with session_factory() as s:
        result = linking.adopt_topic(s, video_task["topic"], account_id=account)
        s.commit()

    assert result.state == TaskState.PENDING_PUBLISH_APPROVAL.value
    with session_factory() as s:
        assert s.get(ContentTask, result.task_id).state == result.state


def test_published_video_adopts_as_published(session_factory, video_task, account):
    from xhs_manager.domain import TaskState
    from xhs_manager.video_pipeline import linking

    with session_factory() as s:
        s.add(VideoPublication(
            id=new_id(), render_id=video_task["render"], topic_id=video_task["topic"],
            platform="xiaohongshu", title="标题", description="", tags=[],
            publish_method="playwright", status="published",
        ))
        s.commit()
    with session_factory() as s:
        result = linking.adopt_topic(s, video_task["topic"], account_id=account)
        s.commit()
    assert result.state == TaskState.PUBLISHED.value


def test_adopting_twice_is_idempotent(session_factory, video_task, account):
    from xhs_manager.models import ContentTask
    from xhs_manager.video_pipeline import linking

    with session_factory() as s:
        first = linking.adopt_topic(s, video_task["topic"], account_id=account)
        s.commit()
    with session_factory() as s:
        second = linking.adopt_topic(s, video_task["topic"], account_id=account)
        s.commit()

    assert second.task_id == first.task_id
    assert second.created_task is False
    with session_factory() as s:
        assert s.query(ContentTask).count() == 1


def test_unlink_keeps_the_task_but_frees_the_topic(session_factory, video_task, account):
    from xhs_manager.models import ContentTask
    from xhs_manager.video_pipeline import linking

    with session_factory() as s:
        linking.adopt_topic(s, video_task["topic"], account_id=account)
        s.commit()
    with session_factory() as s:
        linking.unlink_topic(s, video_task["topic"])
        s.commit()

    with session_factory() as s:
        assert s.query(ContentTask).count() == 1          # 任务留着
        assert len(linking.orphan_topics(s)) == 1         # 选题重新游离


def test_console_shows_owner_after_adoption_without_duplicating_the_row(
    session_factory, video_task, account
):
    """认领后不能一个任务在列表里出现两行（视频一行 + 图文一行）。"""
    from xhs_manager.video_pipeline import linking

    with session_factory() as s:
        before = queries.list_tasks(s)
        assert before["total"] == 1
        assert before["tasks"][0]["orphan"] is True

        linking.adopt_topic(s, video_task["topic"], account_id=account)
        s.commit()

    with session_factory() as s:
        after = queries.list_tasks(s)

    assert after["total"] == 1                            # 没有重复行
    row = after["tasks"][0]
    assert row["orphan"] is False
    assert row["account"] == "测试账号"
    assert row["format"] == "video"


def test_adopt_refuses_an_unknown_task(session_factory, video_task, account):
    from xhs_manager.video_pipeline import linking

    with session_factory() as s:
        with pytest.raises(linking.LinkError):
            linking.adopt_topic(s, video_task["topic"], account_id=account, task_id="nope")


def test_ensure_default_account_reuses_the_existing_one(session_factory):
    from xhs_manager.models import Account
    from xhs_manager.video_pipeline import linking

    with session_factory() as s:
        first, _ = linking.ensure_default_account(s, name="甲")
        s.commit()
    with session_factory() as s:
        second, _ = linking.ensure_default_account(s, name="乙")
        s.commit()

    assert first == second
    with session_factory() as s:
        assert s.query(Account).count() == 1


# ── P4：采集与资产浏览器 ────────────────────────────────────


def test_signals_show_each_platform_its_own_metric(session_factory, video_task):
    """B站是播放、V2EX 是回复、小红书是点赞——压成一个「点赞」列会让多数行变空。"""
    from xhs_manager.video_pipeline.models import VideoTrendSignal

    run_id = None
    with session_factory() as s:
        run_id = s.query(VideoTopic).one().pipeline_run_id
        for platform, eng in [
            ("bilibili", {"views": 124000}),
            ("v2ex", {"comments": 41}),
            ("xiaohongshu", {"likes": 8192}),
            ("wechat", {}),
        ]:
            s.add(VideoTrendSignal(
                id=new_id(), pipeline_run_id=run_id, platform=platform,
                source_url=f"https://{platform}.com/x", title=f"{platform} 标题",
                summary="", heat_score=50.0, engagement=eng,
                content_digest=new_id(), tags=[],
            ))
        s.commit()

    with session_factory() as s:
        data = queries.list_signals(s, run_id=run_id)

    got = {x["platform"]: x["engagement"] for x in data["signals"]}
    assert got["bilibili"] == "12.4万 播放"
    assert got["v2ex"] == "41 回复"
    assert got["xiaohongshu"] == "8,192 赞"
    assert got["wechat"] == "—"


def test_signals_flag_stale_ones_with_an_age(session_factory, video_task):
    """点赞第二高的那条发布于 434 天前——热度分不看时效，界面必须看得见。"""
    from datetime import timedelta

    from xhs_manager.video_pipeline.models import VideoTrendSignal

    with session_factory() as s:
        run_id = s.query(VideoTopic).one().pipeline_run_id
        s.add(VideoTrendSignal(
            id=new_id(), pipeline_run_id=run_id, platform="xiaohongshu",
            source_url="https://x/1", title="去年的爆款", summary="",
            heat_score=99.0, engagement={"likes": 4614},
            published_at=utcnow() - timedelta(days=434),
            content_digest=new_id(), tags=[],
        ))
        s.commit()

    with session_factory() as s:
        row = queries.list_signals(s)["signals"][0]
    assert row["age_days"] >= 430


def test_signals_mark_index_channel_separately(session_factory, video_task):
    """站外索引拿不到互动数据，来源要标出来，不能和站内样本混为一谈。"""
    from xhs_manager.video_pipeline.models import VideoTrendSignal

    with session_factory() as s:
        run_id = s.query(VideoTopic).one().pipeline_run_id
        s.add(VideoTrendSignal(
            id=new_id(), pipeline_run_id=run_id, platform="weibo",
            source_url="https://weibo.com/x", title="索引来的", summary="",
            heat_score=10.0, engagement={}, content_digest=new_id(),
            tags=["索引通道"],
        ))
        s.commit()

    with session_factory() as s:
        row = next(x for x in queries.list_signals(s)["signals"] if x["platform"] == "weibo")
    assert row["via"] == "索引"


def test_assets_are_grouped_by_task(session_factory, video_task):
    with session_factory() as s:
        data = queries.list_assets(s)

    assert len(data["groups"]) == 1
    kinds = [i["kind"] for i in data["groups"][0]["items"]]
    assert "成片" in kinds and "HTML 组合" in kinds
    assert data["groups"][0]["title"] == "测试选题"


def test_assets_skip_tasks_without_any_output(session_factory, video_task):
    """只有脚本、还没渲染的任务不该在资产页里占一行空组。"""
    from xhs_manager.video_pipeline.models import VideoRender

    with session_factory() as s:
        s.query(VideoRender).delete()
        s.query(VideoComposition).delete()
        s.commit()
    with session_factory() as s:
        assert queries.list_assets(s)["groups"] == []


# ── 写动作：审批与重跑 ──────────────────────────────────────


@pytest.fixture
def approvable(session_factory, video_task, account):
    """把选题认领进任务并提交发布审批，返回各种 id。"""
    from xhs_manager.video_pipeline import linking, promote

    with session_factory() as s:
        linking.adopt_topic(s, video_task["topic"], account_id=account)
        s.commit()
    with session_factory() as s:
        result = promote.promote_for_approval(s, video_task["topic"])
        s.commit()
        return {**video_task, "approval": result.approval_id}


HEADERS = {"X-Console-Action": "1"}


@pytest.mark.parametrize("path", [
    "/console/api/approvals/x/approve",
    "/console/api/approvals/x/reject",
    "/console/api/tasks/x/rerun",
])
def test_write_endpoints_refuse_requests_without_the_console_header(client, path):
    """跨源的页面发不出自定义头，预检又会被挡——这就是控制台的 CSRF 防线。"""
    assert client.post(path, json={}).status_code == 403


def test_write_endpoints_check_the_token_when_one_is_configured(
    client, approvable, monkeypatch
):
    monkeypatch.setenv("XHS_CONSOLE_TOKEN", "s3cret")
    url = f"/console/api/approvals/{approvable['approval']}/reject"
    assert client.post(url, json={}, headers=HEADERS).status_code == 403
    assert client.post(
        url, json={}, headers={**HEADERS, "X-Console-Token": "wrong"}
    ).status_code == 403
    ok = client.post(url, json={}, headers={**HEADERS, "X-Console-Token": "s3cret"})
    assert ok.status_code == 200


def test_approving_schedules_the_plan_and_queues_the_publish(
    client, session_factory, approvable
):
    from xhs_manager.models import WorkItem

    when = datetime(2026, 9, 20, 19, 30, tzinfo=timezone.utc)
    resp = client.post(
        f"/console/api/approvals/{approvable['approval']}/approve",
        json={"scheduled_at": when.isoformat(), "window_hours": 3},
        headers=HEADERS,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["scheduled_at"].startswith("2026-09-20T19:30")
    assert body["allowed_until"].startswith("2026-09-20T22:30")

    with session_factory() as s:
        items = s.query(WorkItem).all()
        assert [i.step_type for i in items] == ["video_publish"]
        # 到点之前不能被 worker 领走
        assert items[0].available_at.replace(tzinfo=timezone.utc) == when


def test_approving_twice_does_not_schedule_two_publishes(
    client, session_factory, approvable
):
    """手抖点两下不能发两次。幂等键落在计划上，工作项也认同一个键。"""
    from xhs_manager.models import WorkItem

    url = f"/console/api/approvals/{approvable['approval']}/approve"
    first = client.post(url, json={"delay_minutes": 10}, headers=HEADERS).json()
    second = client.post(url, json={"delay_minutes": 10}, headers=HEADERS).json()

    assert first["plan_id"] == second["plan_id"]
    with session_factory() as s:
        assert s.query(WorkItem).count() == 1


def test_rejecting_marks_the_approval_and_queues_nothing(
    client, session_factory, approvable
):
    from xhs_manager.models import WorkItem

    resp = client.post(
        f"/console/api/approvals/{approvable['approval']}/reject",
        json={"comment": "标题太标题党"}, headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"
    with session_factory() as s:
        assert s.query(WorkItem).count() == 0


def test_approving_a_rejected_request_is_refused(client, approvable):
    url_r = f"/console/api/approvals/{approvable['approval']}/reject"
    client.post(url_r, json={}, headers=HEADERS)
    resp = client.post(
        f"/console/api/approvals/{approvable['approval']}/approve",
        json={}, headers=HEADERS,
    )
    assert resp.status_code == 409
    assert "rejected" in resp.json()["detail"]


def test_rerun_refuses_the_publish_stage(client, approvable):
    """发布必须走审批和排期，不能在这里一键重来。"""
    resp = client.post(
        f"/console/api/tasks/{approvable['topic']}/rerun",
        json={"stage": "publishing"}, headers=HEADERS,
    )
    assert resp.status_code == 400
    assert "审批" in resp.json()["detail"]


@pytest.mark.parametrize("stage,code", [("", 400), ("renderingg", 400)])
def test_rerun_refuses_an_unknown_stage(client, approvable, stage, code):
    resp = client.post(
        f"/console/api/tasks/{approvable['topic']}/rerun",
        json={"stage": stage}, headers=HEADERS,
    )
    assert resp.status_code == code


def test_rerun_needs_the_topic_to_be_adopted_first(client, video_task):
    """没认领的选题没有 ContentTask，队列里无处可放。"""
    resp = client.post(
        f"/console/api/tasks/{video_task['topic']}/rerun",
        json={"stage": "rendering"}, headers=HEADERS,
    )
    assert resp.status_code == 409
    assert "adopt" in resp.json()["detail"]


def test_rerun_queues_the_stage(client, session_factory, approvable):
    from xhs_manager.models import WorkItem

    resp = client.post(
        f"/console/api/tasks/{approvable['topic']}/rerun",
        json={"stage": "rendering"}, headers=HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"
    with session_factory() as s:
        item = s.query(WorkItem).one()
        assert item.step_type == "video_render"


def test_rerun_revives_a_work_item_that_already_failed(
    client, session_factory, approvable
):
    """重跑一个失败的阶段必须真的重跑。

    幂等键让 enqueue_stage 直接返回那条旧记录，如果不复位，
    界面上按钮按了、后台什么也不会发生——这是最难被发现的一类 bug。
    """
    from xhs_manager.models import WorkItem

    url = f"/console/api/tasks/{approvable['topic']}/rerun"
    client.post(url, json={"stage": "rendering"}, headers=HEADERS)
    with session_factory() as s:
        item = s.query(WorkItem).one()
        item.status = "failed"
        item.attempt = 3
        item.error_code = "ffmpeg_exploded"
        s.commit()

    assert client.post(url, json={"stage": "rendering"}, headers=HEADERS).status_code == 200
    with session_factory() as s:
        item = s.query(WorkItem).one()
        assert (item.status, item.attempt, item.error_code) == ("pending", 0, None)


def test_task_detail_exposes_what_the_buttons_need(client, session_factory, approvable):
    """界面按钮的开关来自详情接口：有没有待审批、有没有排期、哪些阶段能重跑。"""
    detail = client.get(f"/console/api/tasks/{approvable['topic']}").json()
    assert detail["approval"]["status"] == "pending"
    assert detail["plan"] is None
    stages = [r["stage"] for r in detail["rerunnable"]]
    assert "publishing" not in stages
    assert "rendering" in stages

    client.post(
        f"/console/api/approvals/{approvable['approval']}/approve",
        json={"delay_minutes": 5}, headers=HEADERS,
    )
    detail = client.get(f"/console/api/tasks/{approvable['topic']}").json()
    assert detail["approval"]["status"] == "approved"
    assert detail["plan"]["scheduled_at"]


def test_cancelling_a_plan_stops_the_publish(client, session_factory, approvable):
    """撤销排期必须真的拦住发布。

    发布闸门只看「有没有计划」是不够的——计划还在，状态变了也得拦。
    """
    from xhs_manager.models import WorkItem
    from xhs_manager.video_pipeline import steps

    plan = client.post(
        f"/console/api/approvals/{approvable['approval']}/approve",
        json={"delay_minutes": 1}, headers=HEADERS,
    ).json()
    resp = client.post(f"/console/api/plans/{plan['plan_id']}/cancel",
                       json={}, headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json() == {"plan_id": plan["plan_id"], "status": "cancelled",
                           "cancelled_items": 1}

    with session_factory() as s:
        assert s.query(WorkItem).count() == 0

    # 就算队列里被人手动塞回一条，闸门也不放行
    with session_factory() as s:
        from xhs_manager.models import ContentTask
        from xhs_manager.video_pipeline.models import VideoTopic
        topic = s.get(VideoTopic, approvable["topic"])
        task = s.get(ContentTask, topic.task_id)
        item = steps.enqueue_publish(s, task=task, run_id=topic.pipeline_run_id)
        s.commit()
        with pytest.raises(Exception) as err:
            steps._assert_publishable(s, item)
        assert "cancelled" in str(err.value)


def test_cancelling_twice_is_harmless(client, approvable):
    plan = client.post(
        f"/console/api/approvals/{approvable['approval']}/approve",
        json={"delay_minutes": 1}, headers=HEADERS,
    ).json()
    url = f"/console/api/plans/{plan['plan_id']}/cancel"
    assert client.post(url, json={}, headers=HEADERS).status_code == 200
    second = client.post(url, json={}, headers=HEADERS)
    assert second.status_code == 200
    assert second.json()["cancelled_items"] == 0


def test_cancel_needs_the_console_header(client):
    assert client.post("/console/api/plans/x/cancel", json={}).status_code == 403


def test_a_local_wall_clock_schedule_survives_the_round_trip(client, session_factory,
                                                             approvable):
    """界面上的 datetime-local 没有时区，落库必须先转成 UTC。

    不转的话 SQLite 会把偏移量丢掉，读回来又被当成 UTC——
    排期整整错开一个时区，而页面上两处显示还会自相矛盾。
    """
    from xhs_manager.models import PublicationPlan

    typed = datetime(2026, 9, 20, 19, 30)            # 用户本机时间，无时区
    resp = client.post(
        f"/console/api/approvals/{approvable['approval']}/approve",
        json={"scheduled_at": typed.isoformat()}, headers=HEADERS,
    ).json()

    # 接口回执和库里存的，必须都指向本地 19:30 这个瞬间
    want = typed.astimezone(timezone.utc)
    assert datetime.fromisoformat(resp["scheduled_at"]) == want
    with session_factory() as s:
        stored = s.query(PublicationPlan).one().scheduled_at
        assert stored.replace(tzinfo=timezone.utc) == want


def test_a_cancelled_plan_can_be_scheduled_again(client, session_factory, approvable):
    """撤销不是死路。

    计划带幂等键，如果撤销后重新批准只是把那条 cancelled 的原样返回，
    这支片子就永远发不出去了——闸门认状态，而状态再也变不回来。
    """
    from xhs_manager.models import PublicationPlan, WorkItem

    url = f"/console/api/approvals/{approvable['approval']}/approve"
    plan = client.post(url, json={"delay_minutes": 1}, headers=HEADERS).json()
    client.post(f"/console/api/plans/{plan['plan_id']}/cancel", json={}, headers=HEADERS)

    again = client.post(
        url, json={"scheduled_at": "2026-09-25T08:00:00+00:00"}, headers=HEADERS,
    )
    assert again.status_code == 200
    assert again.json()["scheduled_at"].startswith("2026-09-25T08:00")

    with session_factory() as s:
        rows = s.query(PublicationPlan).all()
        assert len(rows) == 1 and rows[0].status == "scheduled"   # 不该多出一条
        assert s.query(WorkItem).count() == 1                     # 发布重新入队


# ── 人工标记状态 ────────────────────────────────────────────


def test_marking_a_topic_hides_it_without_deleting_it(client, session_factory, video_task):
    """「放弃」是人明确说过的话，不该被产物反推的状态盖掉。"""
    from xhs_manager.video_pipeline.models import VideoTopic

    url = f"/console/api/tasks/{video_task['topic']}/state"
    assert client.post(url, json={"state": "dropped"}, headers=HEADERS).status_code == 200

    detail = client.get(f"/console/api/tasks/{video_task['topic']}").json()
    assert (detail["state"], detail["state_label"]) == ("dropped", "放弃")

    listing = client.get("/console/api/tasks").json()
    row = next(t for t in listing["tasks"] if t["id"] == video_task["topic"])
    assert row["bucket"] == "off"                 # 收起来，但还在列表里
    assert listing["counts"]["off"] == 1

    with session_factory() as s:
        assert s.get(VideoTopic, video_task["topic"]) is not None   # 没被删


def test_unmarking_restores_the_previous_status(client, session_factory, video_task):
    """撤销标记要回到标记之前的样子，不能一律落回默认值。"""
    from xhs_manager.video_pipeline.models import VideoTopic

    url = f"/console/api/tasks/{video_task['topic']}/state"
    with session_factory() as s:
        s.get(VideoTopic, video_task["topic"]).status = "scripted"
        s.commit()

    client.post(url, json={"state": "expired"}, headers=HEADERS)
    client.post(url, json={"state": ""}, headers=HEADERS)

    with session_factory() as s:
        topic = s.get(VideoTopic, video_task["topic"])
        assert topic.status == "scripted"
        assert topic.previous_status is None


def test_marking_twice_does_not_lose_the_original_status(client, session_factory,
                                                         video_task):
    """连着标两次，previous 不能被第一个标记覆盖掉。"""
    from xhs_manager.video_pipeline.models import VideoTopic

    url = f"/console/api/tasks/{video_task['topic']}/state"
    with session_factory() as s:
        s.get(VideoTopic, video_task["topic"]).status = "scripted"
        s.commit()

    client.post(url, json={"state": "dropped"}, headers=HEADERS)
    client.post(url, json={"state": "expired"}, headers=HEADERS)
    client.post(url, json={"state": ""}, headers=HEADERS)

    with session_factory() as s:
        assert s.get(VideoTopic, video_task["topic"]).status == "scripted"


def test_marking_refuses_states_it_does_not_know(client, video_task):
    resp = client.post(f"/console/api/tasks/{video_task['topic']}/state",
                       json={"state": "随便写的"}, headers=HEADERS)
    assert resp.status_code == 400


def test_marking_needs_the_console_header(client, video_task):
    assert client.post(f"/console/api/tasks/{video_task['topic']}/state",
                       json={"state": "dropped"}).status_code == 403


def test_marking_cancels_the_content_task_and_clears_the_queue(
    client, session_factory, video_task, account
):
    """标记为放弃之后 worker 还在渲染，是这个界面上最容易让人误会的现象。"""
    from xhs_manager.models import ContentTask, WorkItem
    from xhs_manager.video_pipeline import linking, steps
    from xhs_manager.video_pipeline.domain import PipelineStatus
    from xhs_manager.video_pipeline.models import VideoTopic

    with session_factory() as s:
        linking.adopt_topic(s, video_task["topic"], account_id=account)
        s.commit()
    with session_factory() as s:
        topic = s.get(VideoTopic, video_task["topic"])
        task = s.get(ContentTask, topic.task_id)
        steps.enqueue_stage(s, task=task, run_id=topic.pipeline_run_id,
                            stage=PipelineStatus.RENDERING)
        s.commit()

    resp = client.post(f"/console/api/tasks/{video_task['topic']}/state",
                       json={"state": "dropped"}, headers=HEADERS)
    assert resp.json()["cancelled_items"] == 1
    with session_factory() as s:
        assert s.query(WorkItem).count() == 0
        topic = s.get(VideoTopic, video_task["topic"])
        assert s.get(ContentTask, topic.task_id).state == "cancelled"


# ── 人工发布 ────────────────────────────────────────────────


@pytest.fixture
def publication(session_factory, video_task):
    """一条失败的小红书发布记录，外加脚本里写好的平台文案。"""
    from xhs_manager.video_pipeline.models import VideoPublication, VideoScript

    with session_factory() as s:
        script = s.get(VideoScript, video_task["script"])
        script.platform_metadata = {"xiaohongshu": {
            "title": "5个词搞懂AI大脑",
            "desc": "一条时间线讲清 AI 黑话进化史。",
            "tags": ["AI科普", "LLM"],
        }}
        pub = VideoPublication(
            id=new_id(), render_id=video_task["render"], topic_id=video_task["topic"],
            platform="xiaohongshu", title="5个词搞懂AI大脑",
            description="一条时间线讲清 AI 黑话进化史。", tags=["AI科普", "LLM"],
            publish_method="opencli", status="failed",
            error_detail="第 3 步失败 (upload input[type=file])",
        )
        s.add(pub)
        s.commit()
        return pub.id


def test_the_checklist_carries_everything_you_must_copy(client, video_task, publication):
    """人工发布要的东西一项不能少：标题、带标签的正文、成片路径。"""
    c = client.get(f"/console/api/tasks/{video_task['topic']}").json()["checklist"]
    assert c["title"] == "5个词搞懂AI大脑"
    assert c["title_len"] == 9
    assert c["tags"] == ["AI科普", "LLM"]
    # 正文要是可以整段粘贴的成品：标签跟在后面，不用自己拼
    assert c["body"] == "一条时间线讲清 AI 黑话进化史。\n\n#AI科普 #LLM"
    assert c["video_path"].endswith("video.mp4")
    assert c["publication_id"] == publication
    assert c["status"] == "failed"


def test_there_is_no_checklist_before_the_video_is_rendered(client, session_factory,
                                                            video_task):
    """没有成片，复制文案也没用。"""
    from xhs_manager.video_pipeline.models import VideoRender

    with session_factory() as s:
        s.get(VideoRender, video_task["render"]).status = "rendering"
        s.commit()
    assert client.get(f"/console/api/tasks/{video_task['topic']}").json()["checklist"] is None


def test_marking_published_records_the_fact_without_publishing_anything(
    client, session_factory, video_task, publication
):
    """这个接口一行浏览器代码都不碰——它只是记下「我自己发过了」。"""
    from xhs_manager.video_pipeline.models import VideoPublication

    resp = client.post(f"/console/api/publications/{publication}/mark-published",
                       json={"url": "https://www.xiaohongshu.com/explore/abc123"},
                       headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["status"] == "published"

    with session_factory() as s:
        pub = s.get(VideoPublication, publication)
        assert pub.publish_method == "manual"
        assert pub.external_url.endswith("abc123")
        assert pub.published_at is not None
        assert pub.error_detail is None          # 旧的失败原因要擦掉

    detail = client.get(f"/console/api/tasks/{video_task['topic']}").json()
    assert detail["state_label"] == "已发布"


def test_marking_published_without_a_link_is_allowed(client, publication):
    """链接是可选的——人发完了不一定想回来贴链接。"""
    resp = client.post(f"/console/api/publications/{publication}/mark-published",
                       json={}, headers=HEADERS)
    assert resp.status_code == 200 and resp.json()["url"] is None


def test_a_link_that_is_not_a_link_is_refused(client, publication):
    resp = client.post(f"/console/api/publications/{publication}/mark-published",
                       json={"url": "xiaohongshu.com/explore/abc"}, headers=HEADERS)
    assert resp.status_code == 400


def test_retry_clears_the_failure_so_the_checklist_is_usable_again(
    client, session_factory, video_task, publication
):
    from xhs_manager.video_pipeline.models import VideoPublication

    resp = client.post(f"/console/api/publications/{publication}/retry",
                       json={}, headers=HEADERS)
    assert resp.status_code == 200
    with session_factory() as s:
        pub = s.get(VideoPublication, publication)
        assert (pub.status, pub.error_detail) == ("awaiting_manual", None)
        assert pub.publish_method == "manual"

    # 复位之后是「待人工发布」，属于等你动手，不是出错
    row = next(t for t in client.get("/console/api/tasks").json()["tasks"]
               if t["id"] == video_task["topic"])
    assert (row["state"], row["bucket"]) == ("awaiting_manual", "act")


def test_retry_will_not_quietly_wipe_a_published_record(client, publication):
    """已记为发布的那条是一条事实。要改，得说清楚是在改它。"""
    client.post(f"/console/api/publications/{publication}/mark-published",
                json={"url": "https://www.xiaohongshu.com/explore/x"}, headers=HEADERS)

    blocked = client.post(f"/console/api/publications/{publication}/retry",
                          json={}, headers=HEADERS)
    assert blocked.status_code == 409

    forced = client.post(f"/console/api/publications/{publication}/retry",
                         json={"force": True}, headers=HEADERS)
    assert forced.status_code == 200 and forced.json()["was"] == "published"


@pytest.mark.parametrize("path", [
    "/console/api/publications/x/retry",
    "/console/api/publications/x/mark-published",
])
def test_publication_writes_need_the_console_header(client, path):
    assert client.post(path, json={}).status_code == 403
