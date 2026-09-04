from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from xhs_manager.domain import ConflictError, ExternalActionStatus, NotFoundError, utcnow
from xhs_manager.models import ExternalAction
from xhs_manager.services import canonical_digest


def prepare_external_action(
    session: Session,
    *,
    action_type: str,
    resource_type: str,
    resource_id: str,
    idempotency_key: str,
    request: dict[str, Any],
) -> ExternalAction:
    digest = canonical_digest(request)
    existing = session.scalar(
        select(ExternalAction).where(ExternalAction.idempotency_key == idempotency_key)
    )
    if existing is not None:
        if existing.request_digest != digest:
            error = ConflictError("相同外部动作幂等键对应了不同请求")
            error.code = "IDEMPOTENCY_CONFLICT"
            raise error
        return existing
    action = ExternalAction(
        action_type=action_type,
        resource_type=resource_type,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        request_digest=digest,
    )
    session.add(action)
    session.flush()
    return action


def start_external_action(
    session: Session,
    *,
    action_id: str,
) -> ExternalAction:
    action = session.get(ExternalAction, action_id)
    if action is None:
        raise NotFoundError("外部动作不存在")
    if action.status == ExternalActionStatus.SUCCEEDED.value:
        return action
    if action.status == ExternalActionStatus.UNCERTAIN.value:
        error = ConflictError("外部动作结果未知，必须先核验")
        error.code = "EXTERNAL_RESULT_UNCERTAIN"
        raise error
    if action.status == ExternalActionStatus.RUNNING.value:
        error = ConflictError("外部动作正在执行")
        error.code = "EXTERNAL_ACTION_RUNNING"
        raise error
    action.status = ExternalActionStatus.RUNNING.value
    action.attempt += 1
    return action


def succeed_external_action(
    session: Session,
    *,
    action_id: str,
    external_id: str,
    result: dict[str, Any],
    evidence_ref: Optional[str] = None,
) -> ExternalAction:
    action = session.get(ExternalAction, action_id)
    if action is None:
        raise NotFoundError("外部动作不存在")
    if action.status == ExternalActionStatus.SUCCEEDED.value:
        return action
    if action.status != ExternalActionStatus.RUNNING.value:
        error = ConflictError("外部动作不在执行状态")
        error.code = "EXTERNAL_ACTION_STATE_CONFLICT"
        raise error
    action.status = ExternalActionStatus.SUCCEEDED.value
    action.external_id = external_id
    action.result = result
    action.evidence_ref = evidence_ref
    action.completed_at = utcnow()
    return action


def mark_external_action_uncertain(
    session: Session,
    *,
    action_id: str,
    result: dict[str, Any],
    evidence_ref: Optional[str] = None,
) -> ExternalAction:
    action = session.get(ExternalAction, action_id)
    if action is None:
        raise NotFoundError("外部动作不存在")
    if action.status != ExternalActionStatus.RUNNING.value:
        error = ConflictError("外部动作不在执行状态")
        error.code = "EXTERNAL_ACTION_STATE_CONFLICT"
        raise error
    action.status = ExternalActionStatus.UNCERTAIN.value
    action.result = result
    action.evidence_ref = evidence_ref
    return action


def resolve_external_action(
    session: Session,
    *,
    action_id: str,
    succeeded: bool,
    result: dict[str, Any],
    external_id: Optional[str] = None,
    evidence_ref: Optional[str] = None,
) -> ExternalAction:
    action = session.get(ExternalAction, action_id)
    if action is None:
        raise NotFoundError("外部动作不存在")
    if action.status != ExternalActionStatus.UNCERTAIN.value:
        error = ConflictError("只有结果未知的外部动作可以被核验")
        error.code = "EXTERNAL_ACTION_STATE_CONFLICT"
        raise error
    action.status = (
        ExternalActionStatus.SUCCEEDED.value if succeeded else ExternalActionStatus.FAILED.value
    )
    action.result = result
    action.external_id = external_id
    action.evidence_ref = evidence_ref
    action.completed_at = utcnow()
    return action
