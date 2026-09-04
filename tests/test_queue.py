from datetime import timedelta

from sqlalchemy import select

from xhs_manager.domain import AutomationScope, WorkItemStatus, utcnow
from xhs_manager.models import WorkItem
from xhs_manager.queue import claim_next, complete_work_item
from xhs_manager.services import create_account, create_content_task, set_pause


def create_pending_work(session):
    account = create_account(
        session,
        command_id="queue-account",
        name="测试账号",
        timezone_name="Asia/Shanghai",
        strategy_config={"persona": "AI 工作流实验员"},
        actor_id="operator-1",
    )
    task = create_content_task(
        session,
        command_id="queue-task",
        account_id=account["account_id"],
        primary_goal="content_validation",
        actor_id="operator-1",
    )
    session.commit()
    return task


def test_only_one_worker_can_claim(session_factory):
    first_session = session_factory()
    second_session = session_factory()
    try:
        create_pending_work(first_session)
        first = claim_next(first_session, worker_id="worker-1")
        first_session.commit()
        second = claim_next(second_session, worker_id="worker-2")

        assert first is not None
        assert first.status == WorkItemStatus.RUNNING.value
        assert second is None
    finally:
        first_session.close()
        second_session.close()


def test_expired_lease_can_be_reclaimed(session):
    create_pending_work(session)
    first = claim_next(session, worker_id="worker-1", lease_seconds=1)
    session.commit()
    first.lease_expires_at = utcnow() - timedelta(seconds=1)
    session.commit()

    second = claim_next(session, worker_id="worker-2")
    assert second is not None
    assert second.id == first.id
    assert second.lease_owner == "worker-2"
    assert second.attempt == 2


def test_pause_blocks_matching_work(session):
    create_pending_work(session)
    set_pause(
        session,
        scope=AutomationScope.RESEARCH,
        reason="测试暂停",
        actor_id="operator-1",
    )
    session.commit()

    assert claim_next(session, worker_id="worker-1") is None
    item = session.scalar(select(WorkItem))
    assert item.status == WorkItemStatus.PENDING.value


def test_worker_can_limit_claims_to_supported_steps(session):
    create_pending_work(session)

    assert (
        claim_next(
            session,
            worker_id="worker-1",
            supported_steps={"produce_content"},
        )
        is None
    )
    item = session.scalar(select(WorkItem))
    assert item.status == WorkItemStatus.PENDING.value


def test_complete_requires_current_lease(session):
    create_pending_work(session)
    item = claim_next(session, worker_id="worker-1")
    complete_work_item(
        session,
        work_item_id=item.id,
        worker_id="worker-1",
        output_ref="result:1",
    )
    session.commit()

    assert item.status == WorkItemStatus.SUCCEEDED.value
    assert item.output_ref == "result:1"
