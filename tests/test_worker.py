import threading
import time

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from xhs_manager.domain import ConflictError, TaskState, WorkItemStatus
from xhs_manager.models import ContentTask, WorkItem
from xhs_manager.services import create_account, create_content_task
from xhs_manager.worker import RetryableStepError, Worker


def create_worker_task(session_factory):
    with session_factory() as session:
        account = create_account(
            session,
            command_id="worker-account",
            name="测试账号",
            timezone_name="Asia/Shanghai",
            strategy_config={"persona": "AI 工作流实验员"},
            actor_id="operator-1",
        )
        result = create_content_task(
            session,
            command_id="worker-task",
            account_id=account["account_id"],
            primary_goal="content_validation",
            actor_id="operator-1",
        )
        session.commit()
        return result


def test_worker_completes_supported_step(session_factory):
    result = create_worker_task(session_factory)

    def handler(_session, item):
        return f"result:{item.id}"

    worker = Worker(
        session_factory,
        {"collect_research": handler},
        worker_id="worker-test",
    )
    assert worker.run_once() is True

    with session_factory() as session:
        item = session.scalar(select(WorkItem).where(WorkItem.task_id == result["task_id"]))
        assert item.status == WorkItemStatus.SUCCEEDED.value
        assert item.output_ref == f"result:{item.id}"


def test_worker_requeues_retryable_error(session_factory):
    result = create_worker_task(session_factory)

    def handler(_session, _item):
        raise RetryableStepError("TEMPORARY", "临时失败")

    worker = Worker(
        session_factory,
        {"collect_research": handler},
        worker_id="worker-retry",
    )
    assert worker.run_once() is True

    with session_factory() as session:
        item = session.scalar(select(WorkItem).where(WorkItem.task_id == result["task_id"]))
        assert item.status == WorkItemStatus.PENDING.value
        assert item.error_code == "TEMPORARY"
        assert item.attempt == 1


def test_worker_moves_unhandled_error_to_human(session_factory):
    result = create_worker_task(session_factory)

    def handler(_session, _item):
        raise RuntimeError("不可恢复错误")

    worker = Worker(
        session_factory,
        {"collect_research": handler},
        worker_id="worker-fail",
    )
    assert worker.run_once() is True

    with session_factory() as session:
        item = session.scalar(select(WorkItem).where(WorkItem.task_id == result["task_id"]))
        task = session.get(ContentTask, result["task_id"])
        assert item.status == WorkItemStatus.FAILED.value
        assert task.state == TaskState.WAITING_HUMAN.value


# ── 心跳：什么时候该放弃续租 ──────────────────────────────────────


def test_heartbeat_keeps_retrying_after_transient_lock_errors(session_factory):
    """续租失败几乎都是瞬时的：SQLite 全库一把写锁，被别处的长事务占着。

    按固定失败次数放弃是最坏的选择——心跳一停，租约到期这个工作项就会被
    claim_next 当成「过期」重新领走，同一支片子渲两遍。
    """
    worker = Worker(session_factory, {}, worker_id="w", lease_seconds=3)
    calls: list[int] = []
    recovered = threading.Event()

    def flaky(work_item_id: str, lease_seconds: int) -> bool:
        calls.append(lease_seconds)
        if len(calls) <= 4:          # 连续 4 次，比旧代码放弃的阈值还多
            raise OperationalError(
                "UPDATE work_items SET lease_expires_at=?", {},
                Exception("database is locked"),
            )
        recovered.set()
        return True

    worker._renew = flaky
    stop = worker._start_heartbeat("item-1", 3)
    try:
        assert recovered.wait(timeout=30), "心跳被几次瞬时锁库错误弄停摆了"
    finally:
        stop.set()


def test_heartbeat_stops_once_the_lease_is_really_gone(session_factory):
    """对照组：租约真被别人接管了（ConflictError），才是该退出的时候。"""
    worker = Worker(session_factory, {}, worker_id="w", lease_seconds=3)
    calls: list[int] = []

    def taken_over(work_item_id: str, lease_seconds: int) -> bool:
        calls.append(lease_seconds)
        error = ConflictError("工作项租约已经丢失")
        error.code = "WORK_ITEM_LEASE_LOST"
        raise error

    worker._renew = taken_over
    stop = worker._start_heartbeat("item-1", 3)
    try:
        time.sleep(2.5)              # 间隔 1 秒，够跑两轮以上
        assert calls == [3], "租约已经被接管，心跳还在继续续"
    finally:
        stop.set()
