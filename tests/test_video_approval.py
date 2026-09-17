"""P4：视频走审批与排期。"""

from datetime import date, datetime, timedelta, timezone

import pytest

from xhs_manager.domain import new_id, utcnow
from xhs_manager.models import (
    ApprovalRequest,
    ContentTask,
    ContentVersion,
    PublicationPlan,
    TopicProposal,
    WorkItem,
)
from xhs_manager.video_pipeline import linking, promote, steps
from xhs_manager.video_pipeline.domain import PipelineStatus, StageError
from xhs_manager.video_pipeline.models import (
    VideoComposition,
    VideoPipelineRun,
    VideoRender,
    VideoScript,
    VideoTopic,
)
from xhs_manager.worker import Worker


@pytest.fixture
def rendered(session_factory):
    """一支渲染完成、已认领进任务的片子。"""
    with session_factory() as s:
        run = VideoPipelineRun(id=new_id(), run_date=date(2026, 9, 16),
                               status=PipelineStatus.RENDERING.value, trigger_type="manual")
        s.add(run)
        s.flush()
        topic = VideoTopic(
            id=new_id(), pipeline_run_id=run.id, rank=1, title="四道商业闸门",
            angle="热点价值不等于产品价值", why_now="公开讨论正在从概念转向兑现",
            target_audience="垂类软件从业者", video_type="explainer",
            estimated_duration=156, scores={"heat": 8}, total_score=8.4,
            source_signal_ids=["sig-1"], status="selected",
        )
        s.add(topic)
        s.flush()
        script = VideoScript(
            id=new_id(), topic_id=topic.id, version=1, total_duration=156,
            scenes=[{"scene_id": "s01", "duration": 78.0, "narration": "一"},
                    {"scene_id": "s02", "duration": 78.0, "narration": "二"}],
            bgm_style="", generation_model="seed", generation_prompt_hash="h",
            status="ready",
            platform_metadata={"xiaohongshu": {
                "title": "AI+ 是添头还是主菜", "desc": "正文", "tags": ["AI落地"]}},
        )
        s.add(script)
        s.flush()
        comp = VideoComposition(
            id=new_id(), script_id=script.id, composition_dir="/tmp/x",
            html_path="/tmp/x/index.html", total_duration=156.0,
            resolution="1080x1920", transition_effects=[], has_narration=True,
            has_bgm=True, status="render_ready",
        )
        s.add(comp)
        s.flush()
        s.add(VideoRender(
            id=new_id(), composition_id=comp.id, output_path="/tmp/x/video.mp4",
            fps=30, duration=155.7, file_size=58 * 1024 * 1024, status="completed",
            covers={"xiaohongshu": "/tmp/x/cover.png"},
        ))
        account_id, _ = linking.ensure_default_account(s, name="测试账号")
        s.flush()
        res = linking.adopt_topic(s, topic.id, account_id=account_id)
        s.commit()
        return {"topic": topic.id, "task": res.task_id, "run": run.id, "script": script.id}


# ── 提升为审批对象 ───────────────────────────────────────────


def test_promotion_materialises_the_main_system_rows(session_factory, rendered):
    """视频产物映射成主系统认识的对象，而不是另造一套审批。"""
    with session_factory() as s:
        result = promote.promote_for_approval(s, rendered["topic"])
        s.commit()

    with session_factory() as s:
        proposal = s.get(TopicProposal, result.proposal_id)
        version = s.get(ContentVersion, result.content_version_id)
        approval = s.get(ApprovalRequest, result.approval_id)
        topic = s.get(VideoTopic, rendered["topic"])
        script = s.get(VideoScript, rendered["script"])

    assert proposal.working_title == "四道商业闸门"
    assert proposal.recommended_format == "video"
    # 设计里定的映射：视频的分镜就是这条内容的「屏」
    assert len(version.slide_scripts) == 2
    assert version.selected_title == "AI+ 是添头还是主菜"
    assert version.asset_paths == ["/tmp/x/video.mp4", "/tmp/x/cover.png"]
    assert version.generation_manifest["source"] == "video_pipeline"
    assert approval.approval_type == "publish"
    assert approval.status == "pending"
    # 三个外键都挂上了——P2 时它们还没有写入方
    assert topic.topic_proposal_id == proposal.id
    assert script.content_version_id == version.id


def test_promotion_is_idempotent(session_factory, rendered):
    with session_factory() as s:
        first = promote.promote_for_approval(s, rendered["topic"])
        s.commit()
    with session_factory() as s:
        second = promote.promote_for_approval(s, rendered["topic"])
        s.commit()

    assert second.approval_id == first.approval_id
    assert second.created is False
    with session_factory() as s:
        assert s.query(ApprovalRequest).count() == 1
        assert s.query(ContentVersion).count() == 1


def test_promotion_refuses_before_the_render_is_done(session_factory, rendered):
    with session_factory() as s:
        s.query(VideoRender).one().status = "rendering"
        s.commit()
    with session_factory() as s:
        with pytest.raises(promote.PromoteError, match="还没渲染完"):
            promote.promote_for_approval(s, rendered["topic"])


# ── 发布闸门：这是 P4 最重要的一条 ───────────────────────────


def test_publishing_without_approval_is_refused(session_factory, rendered, monkeypatch):
    """队列里凭空出现一个 video_publish 也不能把片子发出去。"""
    published = []
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage",
        lambda self, run_id, stage: published.append(stage) or {"status": "ok"},
    )
    handlers = steps.make_handlers(session_factory)

    with session_factory() as s:
        task = s.get(ContentTask, rendered["task"])
        steps.enqueue_stage(s, task=task, run_id=rendered["run"],
                            stage=PipelineStatus.PUBLISHING)
        s.commit()

    Worker(session_factory, handlers, worker_id="w1").run_once()

    assert published == []                     # 一次都没真的发
    with session_factory() as s:
        item = s.query(WorkItem).filter_by(step_type="video_publish").one()
        assert "需要先经过审批" in item.error_detail


def test_publishing_before_the_scheduled_time_is_refused(session_factory, rendered, monkeypatch):
    published = []
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage",
        lambda self, run_id, stage: published.append(stage) or {"status": "ok"},
    )
    handlers = steps.make_handlers(session_factory)

    with session_factory() as s:
        result = promote.promote_for_approval(s, rendered["topic"])
        promote.approve_and_schedule(
            s, result.approval_id, scheduled_at=utcnow() + timedelta(hours=3))
        task = s.get(ContentTask, rendered["task"])
        steps.enqueue_stage(s, task=task, run_id=rendered["run"],
                            stage=PipelineStatus.PUBLISHING)
        s.commit()

    Worker(session_factory, handlers, worker_id="w1").run_once()

    assert published == []
    with session_factory() as s:
        item = s.query(WorkItem).filter_by(step_type="video_publish").one()
        assert "还没到排期时间" in item.error_detail


def test_publishing_after_the_window_closed_is_refused(session_factory, rendered, monkeypatch):
    """过了窗口宁可不发——半夜把片子推出去比晚一天更糟。"""
    published = []
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage",
        lambda self, run_id, stage: published.append(stage) or {"status": "ok"},
    )
    handlers = steps.make_handlers(session_factory)

    with session_factory() as s:
        result = promote.promote_for_approval(s, rendered["topic"])
        promote.approve_and_schedule(
            s, result.approval_id,
            scheduled_at=utcnow() - timedelta(hours=5), window=timedelta(hours=2))
        task = s.get(ContentTask, rendered["task"])
        steps.enqueue_stage(s, task=task, run_id=rendered["run"],
                            stage=PipelineStatus.PUBLISHING)
        s.commit()

    Worker(session_factory, handlers, worker_id="w1").run_once()

    assert published == []
    with session_factory() as s:
        item = s.query(WorkItem).filter_by(step_type="video_publish").one()
        assert "错过允许发布窗口" in item.error_detail


def test_publishing_inside_the_window_goes_through(session_factory, rendered, monkeypatch):
    published = []
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage",
        lambda self, run_id, stage: published.append(stage) or {"status": "ok"},
    )
    handlers = steps.make_handlers(session_factory)

    with session_factory() as s:
        result = promote.promote_for_approval(s, rendered["topic"])
        promote.approve_and_schedule(
            s, result.approval_id, scheduled_at=utcnow() - timedelta(minutes=1))
        task = s.get(ContentTask, rendered["task"])
        steps.enqueue_stage(s, task=task, run_id=rendered["run"],
                            stage=PipelineStatus.PUBLISHING)
        s.commit()

    Worker(session_factory, handlers, worker_id="w1").run_once()

    assert published == [PipelineStatus.PUBLISHING]
    with session_factory() as s:
        item = s.query(WorkItem).filter_by(step_type="video_publish").one()
        assert item.error_detail is None


# ── 排期与幂等 ──────────────────────────────────────────────


def test_approving_twice_produces_one_plan(session_factory, rendered):
    """重复批准不能排出第二条发布。"""
    when = datetime(2026, 9, 17, 11, 30, tzinfo=timezone.utc)
    with session_factory() as s:
        result = promote.promote_for_approval(s, rendered["topic"])
        first = promote.approve_and_schedule(s, result.approval_id, scheduled_at=when)
        s.commit()
        first_id = first.id
    with session_factory() as s:
        approval = s.query(ApprovalRequest).one()
        second = promote.approve_and_schedule(s, approval.id, scheduled_at=when)
        s.commit()
        assert second.id == first_id

    with session_factory() as s:
        assert s.query(PublicationPlan).count() == 1


def test_approval_sets_the_allowed_window_and_idempotency_key(session_factory, rendered):
    when = datetime(2026, 9, 17, 11, 30, tzinfo=timezone.utc)
    with session_factory() as s:
        result = promote.promote_for_approval(s, rendered["topic"])
        plan = promote.approve_and_schedule(
            s, result.approval_id, scheduled_at=when, window=timedelta(hours=3))
        s.commit()
        assert plan.allowed_from == when
        assert plan.allowed_until == when + timedelta(hours=3)
        assert plan.idempotency_key == f"plan:{result.approval_id}"
        assert s.get(ApprovalRequest, result.approval_id).status == "approved"


def test_rejecting_blocks_publishing(session_factory, rendered):
    with session_factory() as s:
        result = promote.promote_for_approval(s, rendered["topic"])
        s.commit()
    with session_factory() as s:
        promote.reject(s, result.approval_id, comment="标题太标题党")
        s.commit()
    with session_factory() as s:
        assert s.get(ApprovalRequest, result.approval_id).status == "rejected"
        with pytest.raises(promote.PromoteError, match="不能再批准"):
            promote.approve_and_schedule(s, result.approval_id, scheduled_at=utcnow())


def test_pending_approvals_lists_only_video_ones(session_factory, rendered):
    with session_factory() as s:
        promote.promote_for_approval(s, rendered["topic"])
        # 一条图文审批，不该出现在视频清单里
        s.add(ApprovalRequest(
            approval_type="publish", resource_type="content_version",
            resource_id="some-article-version", resource_version="1",
            expires_at=utcnow() + timedelta(days=1),
        ))
        s.commit()

    with session_factory() as s:
        rows = promote.pending_approvals(s)

    assert len(rows) == 1
    assert rows[0]["title"] == "AI+ 是添头还是主菜"
    assert rows[0]["scenes"] == 2


def test_render_completion_submits_for_approval(session_factory, rendered, monkeypatch):
    """渲染阶段跑完要自动挂上审批，不用人再敲一次命令。"""
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage",
        lambda self, run_id, stage: {"status": "ok"},
    )
    handlers = steps.make_handlers(session_factory)
    with session_factory() as s:
        task = s.get(ContentTask, rendered["task"])
        steps.enqueue_stage(s, task=task, run_id=rendered["run"],
                            stage=PipelineStatus.RENDERING)
        s.commit()

    Worker(session_factory, handlers, worker_id="w1").run_once()

    with session_factory() as s:
        assert len(promote.pending_approvals(s)) == 1


def test_a_failed_promotion_does_not_fail_the_render(session_factory, rendered, monkeypatch):
    """成片已经出来了，别因为挂审批出错就把渲染判成失败。"""
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage",
        lambda self, run_id, stage: {"status": "ok"},
    )
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.promote.promote_for_approval",
        lambda *a, **k: (_ for _ in ()).throw(StageError("promote", "故意炸")),
    )
    handlers = steps.make_handlers(session_factory)
    with session_factory() as s:
        task = s.get(ContentTask, rendered["task"])
        steps.enqueue_stage(s, task=task, run_id=rendered["run"],
                            stage=PipelineStatus.RENDERING)
        s.commit()

    Worker(session_factory, handlers, worker_id="w1").run_once()

    with session_factory() as s:
        item = s.query(WorkItem).filter_by(step_type="video_render").one()
        assert item.status == "succeeded"
