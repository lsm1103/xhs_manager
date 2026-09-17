"""P3：视频流水线跑在工作队列上。"""

import threading
import time
from datetime import date, timedelta, timezone

import pytest

from xhs_manager.domain import TaskState, WorkItemStatus, utcnow
from xhs_manager.models import ContentTask, WorkflowInstance, WorkItem
from xhs_manager.queue import claim_next
from xhs_manager.video_pipeline import linking, steps
from xhs_manager.video_pipeline.domain import PipelineStatus, StageError
from xhs_manager.video_pipeline.models import VideoPipelineRun, VideoTopic
from xhs_manager.worker import Worker


@pytest.fixture
def task_with_video(session_factory):
    """一个已认领的视频任务，运行停在 collecting。"""
    from xhs_manager.domain import new_id

    with session_factory() as s:
        run = VideoPipelineRun(
            id=new_id(), run_date=date(2026, 9, 16),
            status=PipelineStatus.COLLECTING.value, trigger_type="manual",
        )
        s.add(run)
        s.flush()
        topic = VideoTopic(
            id=new_id(), pipeline_run_id=run.id, rank=1, title="测试选题",
            angle="", why_now="", target_audience="", video_type="explainer",
            estimated_duration=60, scores={}, total_score=8.0,
            source_signal_ids=[], status="selected",
        )
        s.add(topic)
        s.flush()
        account_id, _ = linking.ensure_default_account(s, name="测试账号")
        s.flush()
        result = linking.adopt_topic(s, topic.id, account_id=account_id)
        s.commit()
        return {"task": result.task_id, "topic": topic.id, "run": run.id}


def _handlers(session_factory, stage_result=None, fail_with=None):
    """把真正跑阶段的那一步换掉，只测队列编排。"""
    calls = []

    def fake_run_stage(self, run_id, stage):
        calls.append((run_id, stage))
        if fail_with:
            raise fail_with
        return stage_result or {"status": "ok"}

    return calls, fake_run_stage


# ── 链式推进 ────────────────────────────────────────────────


def test_a_finished_stage_enqueues_the_next_one(session_factory, task_with_video, monkeypatch):
    """每次只排下一个阶段，不一次把六个都排进去。"""
    calls, fake = _handlers(session_factory)
    monkeypatch.setattr("xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage", fake)

    handlers = steps.make_handlers(session_factory)
    with session_factory() as s:
        task = s.get(ContentTask, task_with_video["task"])
        steps.enqueue_stage(s, task=task, run_id=task_with_video["run"],
                            stage=PipelineStatus.COLLECTING)
        s.commit()

    worker = Worker(session_factory, handlers, worker_id="w1")
    assert worker.run_once() is True

    with session_factory() as s:
        items = {i.step_type: i for i in s.query(WorkItem).all()}
    assert items["video_collect"].status == WorkItemStatus.SUCCEEDED.value
    assert items["video_select"].status == WorkItemStatus.PENDING.value
    assert len(items) == 2                       # 只排了下一个，没排全部六个
    assert calls == [(task_with_video["run"], PipelineStatus.COLLECTING)]


def test_the_chain_runs_to_render_and_then_stops_for_approval(
    session_factory, task_with_video, monkeypatch
):
    """渲染完成是整条链上唯一的人工闸门。

    它**不会**自动入队发布——「渲染完就发」是视频线接进主系统之前的行为。
    """
    calls, fake = _handlers(session_factory)
    monkeypatch.setattr("xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage", fake)

    handlers = steps.make_handlers(session_factory)
    with session_factory() as s:
        task = s.get(ContentTask, task_with_video["task"])
        steps.enqueue_stage(s, task=task, run_id=task_with_video["run"],
                            stage=PipelineStatus.COLLECTING)
        s.commit()

    worker = Worker(session_factory, handlers, worker_id="w1")
    for _ in range(10):
        if not worker.run_once():
            break

    assert [stage for _, stage in calls] == [
        PipelineStatus.COLLECTING, PipelineStatus.SELECTING, PipelineStatus.MATERIALIZING,
        PipelineStatus.COMPOSING, PipelineStatus.RENDERING,
    ]
    with session_factory() as s:
        types = {i.step_type for i in s.query(WorkItem).all()}
        assert "video_publish" not in types          # 闸门生效
        task = s.get(ContentTask, task_with_video["task"])
        assert task.state == TaskState.PENDING_PUBLISH_APPROVAL.value


def test_the_chain_stops_at_the_failing_stage(session_factory, task_with_video, monkeypatch):
    """失败时队列里不该躺着后面几个注定失败的工作项。"""
    _, fake = _handlers(session_factory, fail_with=StageError("rendering", "ffmpeg 退出码 1"))
    monkeypatch.setattr("xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage", fake)

    handlers = steps.make_handlers(session_factory)
    with session_factory() as s:
        task = s.get(ContentTask, task_with_video["task"])
        steps.enqueue_stage(s, task=task, run_id=task_with_video["run"],
                            stage=PipelineStatus.RENDERING)
        s.commit()

    worker = Worker(session_factory, handlers, worker_id="w1")
    worker.run_once()

    with session_factory() as s:
        types = {i.step_type for i in s.query(WorkItem).all()}
    assert types == {"video_render"}            # 没有 video_publish


def test_failure_retries_the_same_item_not_a_new_one(session_factory, task_with_video, monkeypatch):
    _, fake = _handlers(session_factory, fail_with=StageError("rendering", "临时抽风"))
    monkeypatch.setattr("xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage", fake)

    handlers = steps.make_handlers(session_factory)
    with session_factory() as s:
        task = s.get(ContentTask, task_with_video["task"])
        steps.enqueue_stage(s, task=task, run_id=task_with_video["run"],
                            stage=PipelineStatus.RENDERING)
        s.commit()

    worker = Worker(session_factory, handlers, worker_id="w1")
    worker.run_once()

    with session_factory() as s:
        items = s.query(WorkItem).all()
        assert len(items) == 1                   # 同一个工作项
        assert items[0].attempt == 1
        assert items[0].error_detail


# ── 幂等 ────────────────────────────────────────────────────


def test_enqueueing_the_same_stage_twice_reuses_the_item(session_factory, task_with_video):
    with session_factory() as s:
        task = s.get(ContentTask, task_with_video["task"])
        first = steps.enqueue_stage(s, task=task, run_id=task_with_video["run"],
                                    stage=PipelineStatus.RENDERING)
        second = steps.enqueue_stage(s, task=task, run_id=task_with_video["run"],
                                     stage=PipelineStatus.RENDERING)
        s.commit()
        assert first.id == second.id
        assert s.query(WorkItem).count() == 1


def test_publish_gets_a_single_attempt(session_factory, task_with_video):
    """重复发布比不发布更糟——发布阶段不重试。"""
    with session_factory() as s:
        task = s.get(ContentTask, task_with_video["task"])
        item = steps.enqueue_stage(s, task=task, run_id=task_with_video["run"],
                                   stage=PipelineStatus.PUBLISHING)
        s.commit()
        assert item.max_attempts == 1


# ── produce_content 分派 ─────────────────────────────────────


def test_produce_content_routes_a_video_task_into_the_video_chain(
    session_factory, task_with_video, monkeypatch
):
    handlers = steps.make_handlers(session_factory)
    with session_factory() as s:
        task = s.get(ContentTask, task_with_video["task"])
        workflow = WorkflowInstance(task_id=task.id, workflow_type="content_lifecycle",
                                    current_step="produce_content")
        s.add(workflow)
        s.flush()
        item = WorkItem(workflow_id=workflow.id, task_id=task.id,
                        step_type="produce_content",
                        idempotency_key=f"task:{task.id}:produce_content:v1")
        s.add(item)
        s.commit()

    worker = Worker(session_factory, handlers, worker_id="w1")
    worker.run_once()

    with session_factory() as s:
        types = {i.step_type for i in s.query(WorkItem).all()}
    assert "video_collect" in types


def test_produce_content_says_so_when_the_task_has_no_video(session_factory, monkeypatch):
    """图文生产路径还没实现——要明确报出来，不能假装处理成功。"""

    handlers = steps.make_handlers(session_factory)
    with session_factory() as s:
        account_id, strategy_id = linking.ensure_default_account(s, name="测试账号")
        task = ContentTask(account_id=account_id, strategy_version_id=strategy_id,
                           primary_goal="article", state=TaskState.PRODUCING.value)
        s.add(task)
        s.flush()
        workflow = WorkflowInstance(task_id=task.id, workflow_type="content_lifecycle")
        s.add(workflow)
        s.flush()
        s.add(WorkItem(workflow_id=workflow.id, task_id=task.id,
                       step_type="produce_content",
                       idempotency_key=f"task:{task.id}:produce_content:v1"))
        s.commit()
        task_id = task.id

    Worker(session_factory, handlers, worker_id="w1").run_once()

    with session_factory() as s:
        item = s.query(WorkItem).filter_by(task_id=task_id).one()
        assert item.status == WorkItemStatus.FAILED.value
        assert "图文生产路径尚未实现" in item.error_detail


# ── 租约心跳：没有它，渲染会被跑两遍 ──────────────────────────


def test_a_long_step_keeps_its_lease(session_factory, task_with_video, monkeypatch):
    """渲染要 4 分钟，默认租约只有 60 秒。

    没有心跳的话，另一个 worker 会把「租约过期」的工作项重新领走，
    同一支片子渲染两遍。这里用 2 秒租约 + 3 秒处理器来复现这个窗口。
    """
    started = threading.Event()
    release = threading.Event()

    def slow_stage(self, run_id, stage):
        started.set()
        release.wait(timeout=5)
        return {"status": "ok"}

    monkeypatch.setattr("xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage", slow_stage)
    handlers = steps.make_handlers(session_factory)

    with session_factory() as s:
        task = s.get(ContentTask, task_with_video["task"])
        steps.enqueue_stage(s, task=task, run_id=task_with_video["run"],
                            stage=PipelineStatus.RENDERING)
        s.commit()

    worker = Worker(session_factory, handlers, worker_id="slow",
                    lease_seconds=2, step_lease_seconds={"video_render": 2})
    thread = threading.Thread(target=worker.run_once, daemon=True)
    thread.start()
    assert started.wait(timeout=5)

    # 租约到期之后，另一个 worker 试着抢：心跳应该已经把租约续上了
    time.sleep(2.5)
    with session_factory() as s:
        stolen = claim_next(s, worker_id="thief", supported_steps={"video_render"})
        s.commit()
    assert stolen is None, "租约没续上，工作项被另一个 worker 抢走了"

    release.set()
    thread.join(timeout=5)

    with session_factory() as s:
        item = s.query(WorkItem).filter_by(step_type="video_render").one()
        assert item.status == WorkItemStatus.SUCCEEDED.value
        assert item.attempt == 1              # 只跑了一遍


def test_without_a_heartbeat_an_expired_lease_is_stealable(session_factory, task_with_video):
    """对照组：证明上面那个测试测的是真问题，不是摆设。"""
    with session_factory() as s:
        task = s.get(ContentTask, task_with_video["task"])
        steps.enqueue_stage(s, task=task, run_id=task_with_video["run"],
                            stage=PipelineStatus.RENDERING)
        s.commit()

    with session_factory() as s:
        first = claim_next(s, worker_id="w1", lease_seconds=1,
                           supported_steps={"video_render"})
        s.commit()
        assert first is not None

    later = utcnow() + timedelta(seconds=5)
    with session_factory() as s:
        stolen = claim_next(s, worker_id="w2", now=later, supported_steps={"video_render"})
        s.commit()
    assert stolen is not None                 # 没续租就会被抢走


def test_a_long_step_upgrades_its_lease_immediately_after_claiming(
    session_factory, task_with_video, monkeypatch
):
    """认领时用的是通用租约，领到之后要立刻按步骤时长升上去。

    这是实跑一次真渲染才暴露的 bug：video_render 的步骤租约是 1800 秒，
    心跳间隔按它算成 600 秒，而认领时给的租约只有 60 秒——
    第一次心跳之前租约早就过期了，中间 540 秒谁都能把它抢走。
    """
    seen = {}

    def capture_stage(self, run_id, stage):
        with session_factory() as s:
            item = s.query(WorkItem).filter_by(step_type="video_render").one()
            seen["expires"] = item.lease_expires_at
            seen["claimed_at"] = utcnow()
        return {"status": "ok"}

    monkeypatch.setattr("xhs_manager.video_pipeline.pipeline.VideoPipeline.run_stage",
                        capture_stage)
    handlers = steps.make_handlers(session_factory)

    with session_factory() as s:
        task = s.get(ContentTask, task_with_video["task"])
        steps.enqueue_stage(s, task=task, run_id=task_with_video["run"],
                            stage=PipelineStatus.RENDERING)
        s.commit()

    Worker(session_factory, handlers, worker_id="w1",
           lease_seconds=60, step_lease_seconds={"video_render": 1800}).run_once()

    expires = seen["expires"]
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    held = (expires - seen["claimed_at"]).total_seconds()
    assert held > 600, f"处理器运行期间租约只剩 {held:.0f} 秒，不足以撑过第一次心跳"
