from sqlalchemy import select

from xhs_manager.domain import TaskState, WorkItemStatus
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
