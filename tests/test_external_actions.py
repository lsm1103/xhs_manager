import pytest

from xhs_manager.domain import ConflictError, ExternalActionStatus
from xhs_manager.external_actions import (
    mark_external_action_uncertain,
    prepare_external_action,
    resolve_external_action,
    start_external_action,
    succeed_external_action,
)


def test_external_action_is_idempotent(session):
    first = prepare_external_action(
        session,
        action_type="publish",
        resource_type="content_task",
        resource_id="task-1",
        idempotency_key="publish:task-1:v1",
        request={"title": "标题"},
    )
    second = prepare_external_action(
        session,
        action_type="publish",
        resource_type="content_task",
        resource_id="task-1",
        idempotency_key="publish:task-1:v1",
        request={"title": "标题"},
    )
    assert first.id == second.id

    with pytest.raises(ConflictError) as error:
        prepare_external_action(
            session,
            action_type="publish",
            resource_type="content_task",
            resource_id="task-1",
            idempotency_key="publish:task-1:v1",
            request={"title": "另一个标题"},
        )
    assert error.value.code == "IDEMPOTENCY_CONFLICT"


def test_uncertain_action_must_be_resolved_before_retry(session):
    action = prepare_external_action(
        session,
        action_type="publish",
        resource_type="content_task",
        resource_id="task-1",
        idempotency_key="publish:task-1:v2",
        request={"title": "标题"},
    )
    start_external_action(session, action_id=action.id)
    mark_external_action_uncertain(
        session,
        action_id=action.id,
        result={"reason": "响应丢失"},
    )

    with pytest.raises(ConflictError) as error:
        start_external_action(session, action_id=action.id)
    assert error.value.code == "EXTERNAL_RESULT_UNCERTAIN"

    resolve_external_action(
        session,
        action_id=action.id,
        succeeded=True,
        external_id="post-1",
        result={"verified": True},
    )
    assert action.status == ExternalActionStatus.SUCCEEDED.value


def test_succeeded_action_returns_existing_result(session):
    action = prepare_external_action(
        session,
        action_type="reply",
        resource_type="comment",
        resource_id="comment-1",
        idempotency_key="reply:comment-1:v1",
        request={"text": "收到"},
    )
    start_external_action(session, action_id=action.id)
    succeed_external_action(
        session,
        action_id=action.id,
        external_id="reply-1",
        result={"sent": True},
    )

    same = start_external_action(session, action_id=action.id)
    assert same.status == ExternalActionStatus.SUCCEEDED.value
