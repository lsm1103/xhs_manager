from datetime import timedelta

import pytest
from sqlalchemy import select

from xhs_manager.domain import (
    ApprovalDecision,
    ApprovalStatus,
    ConflictError,
    TaskState,
    utcnow,
)
from xhs_manager.models import ApprovalRequest, ContentTask, WorkItem
from xhs_manager.services import (
    create_account,
    create_content_task,
    decide_approval,
    mark_content_ready,
    mark_topic_ready,
    transition_task,
)


def create_researching_task(session):
    account = create_account(
        session,
        command_id="approval-account",
        name="测试账号",
        timezone_name="Asia/Shanghai",
        strategy_config={"persona": "AI 工作流实验员"},
        actor_id="operator-1",
    )
    result = create_content_task(
        session,
        command_id="approval-task",
        account_id=account["account_id"],
        primary_goal="content_validation",
        actor_id="operator-1",
    )
    transition_task(
        session,
        task_id=result["task_id"],
        target=TaskState.RESEARCHING,
        actor_type="system",
        actor_id="worker-1",
        trace_id="research-start",
    )
    session.commit()
    return result


def test_topic_approval_is_version_bound_and_idempotent(session):
    result = create_researching_task(session)
    approval = mark_topic_ready(
        session,
        task_id=result["task_id"],
        topic_version_id="topic-v1",
    )
    session.commit()

    decision = decide_approval(
        session,
        approval_id=approval.id,
        decision=ApprovalDecision.APPROVE,
        operator_id="operator-1",
        event_id="approval-event-1",
    )
    session.commit()
    duplicate = decide_approval(
        session,
        approval_id=approval.id,
        decision=ApprovalDecision.APPROVE,
        operator_id="operator-1",
        event_id="approval-event-1",
    )

    assert decision["task_state"] == TaskState.PRODUCING.value
    assert duplicate["duplicate"] is True
    work_items = list(
        session.scalars(select(WorkItem).where(WorkItem.task_id == result["task_id"]))
    )
    assert [item.step_type for item in work_items] == [
        "collect_research",
        "produce_content",
    ]


def test_stale_topic_approval_is_rejected(session):
    result = create_researching_task(session)
    approval = mark_topic_ready(
        session,
        task_id=result["task_id"],
        topic_version_id="topic-v1",
    )
    session.commit()
    task = session.get(ContentTask, result["task_id"])
    task.current_topic_version_id = "topic-v2"
    session.commit()

    with pytest.raises(ConflictError) as error:
        decide_approval(
            session,
            approval_id=approval.id,
            decision=ApprovalDecision.APPROVE,
            operator_id="operator-1",
            event_id="approval-event-stale",
        )
    assert error.value.code == "APPROVAL_VERSION_MISMATCH"
    session.rollback()
    assert session.get(ContentTask, result["task_id"]).state == (
        TaskState.PENDING_TOPIC_APPROVAL.value
    )


def test_publish_approval_requires_schedule(session):
    result = create_researching_task(session)
    topic_approval = mark_topic_ready(
        session,
        task_id=result["task_id"],
        topic_version_id="topic-v1",
    )
    session.commit()
    decide_approval(
        session,
        approval_id=topic_approval.id,
        decision=ApprovalDecision.APPROVE,
        operator_id="operator-1",
        event_id="topic-approved",
    )
    session.commit()
    publish_approval = mark_content_ready(
        session,
        task_id=result["task_id"],
        content_version_id="content-v1",
    )
    session.commit()

    with pytest.raises(ConflictError) as error:
        decide_approval(
            session,
            approval_id=publish_approval.id,
            decision=ApprovalDecision.APPROVE,
            operator_id="operator-1",
            event_id="publish-no-schedule",
        )
    assert error.value.code == "SCHEDULE_REQUIRED"
    session.rollback()

    decision = decide_approval(
        session,
        approval_id=publish_approval.id,
        decision=ApprovalDecision.APPROVE,
        operator_id="operator-1",
        event_id="publish-with-schedule",
        scheduled_at=utcnow() + timedelta(hours=1),
    )
    session.commit()
    assert decision["task_state"] == TaskState.SCHEDULED.value


def test_expired_approval_cannot_advance_task(session):
    result = create_researching_task(session)
    approval = mark_topic_ready(
        session,
        task_id=result["task_id"],
        topic_version_id="topic-v1",
    )
    approval.expires_at = utcnow() - timedelta(seconds=1)
    session.commit()

    with pytest.raises(ConflictError) as error:
        decide_approval(
            session,
            approval_id=approval.id,
            decision=ApprovalDecision.APPROVE,
            operator_id="operator-1",
            event_id="expired-event",
        )
    assert error.value.code == "APPROVAL_EXPIRED"
    session.rollback()
    stored = session.get(ApprovalRequest, approval.id)
    assert stored.status == ApprovalStatus.PENDING.value
