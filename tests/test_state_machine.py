import pytest

from xhs_manager.domain import ConflictError, TaskState
from xhs_manager.models import AuditLog, ContentTask
from xhs_manager.services import (
    create_account,
    create_content_task,
    transition_task,
)


def bootstrap_task(session):
    account = create_account(
        session,
        command_id="account-command",
        name="测试账号",
        timezone_name="Asia/Shanghai",
        strategy_config={"persona": "AI 工作流实验员"},
        actor_id="operator-1",
    )
    task = create_content_task(
        session,
        command_id="task-command",
        account_id=account["account_id"],
        primary_goal="content_validation",
        actor_id="operator-1",
    )
    session.commit()
    return task


def test_valid_transition_and_audit(session):
    result = bootstrap_task(session)

    task = transition_task(
        session,
        task_id=result["task_id"],
        target=TaskState.RESEARCHING,
        actor_type="system",
        actor_id="worker-1",
        trace_id="trace-1",
    )
    session.commit()

    assert task.state == TaskState.RESEARCHING.value
    assert task.row_version == 2
    audits = session.query(AuditLog).filter_by(resource_id=task.id).all()
    assert [audit.action for audit in audits] == [
        "content_task.created",
        "content_task.transitioned",
    ]


def test_invalid_transition_is_rejected(session):
    result = bootstrap_task(session)

    with pytest.raises(ConflictError) as error:
        transition_task(
            session,
            task_id=result["task_id"],
            target=TaskState.SCHEDULED,
            actor_type="system",
            actor_id="worker-1",
            trace_id="trace-2",
        )

    assert error.value.code == "STATE_CONFLICT"
    session.rollback()
    task = session.get(ContentTask, result["task_id"])
    assert task.state == TaskState.PENDING_RESEARCH.value


def test_command_is_idempotent_and_conflict_is_detected(session):
    account = create_account(
        session,
        command_id="same-account-command",
        name="测试账号",
        timezone_name="Asia/Shanghai",
        strategy_config={"persona": "AI 工作流实验员"},
        actor_id="operator-1",
    )
    session.commit()

    first = create_content_task(
        session,
        command_id="same-task-command",
        account_id=account["account_id"],
        primary_goal="content_validation",
        actor_id="operator-1",
    )
    session.commit()
    second = create_content_task(
        session,
        command_id="same-task-command",
        account_id=account["account_id"],
        primary_goal="content_validation",
        actor_id="operator-1",
    )
    assert first == second

    with pytest.raises(ConflictError) as error:
        create_content_task(
            session,
            command_id="same-task-command",
            account_id=account["account_id"],
            primary_goal="different_goal",
            actor_id="operator-1",
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"
