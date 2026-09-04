from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from xhs_manager.domain import (
    STEP_SCOPE,
    AutomationScope,
    ConflictError,
    TaskState,
    WorkItemStatus,
    utcnow,
)
from xhs_manager.models import ContentTask, SystemPause, WorkItem
from xhs_manager.services import add_audit, transition_task


def _paused_scopes(session: Session) -> set[str]:
    return set(session.scalars(select(SystemPause.scope).where(SystemPause.active.is_(True))).all())


def claim_next(
    session: Session,
    *,
    worker_id: str,
    lease_seconds: int = 60,
    now: Optional[datetime] = None,
    supported_steps: Optional[set[str]] = None,
) -> Optional[WorkItem]:
    current_time = now or utcnow()
    if supported_steps is not None and not supported_steps:
        return None
    paused = _paused_scopes(session)
    if AutomationScope.ALL.value in paused:
        return None

    query = select(WorkItem).where(
        WorkItem.attempt < WorkItem.max_attempts,
        or_(
            and_(
                WorkItem.status == WorkItemStatus.PENDING.value,
                WorkItem.available_at <= current_time,
            ),
            and_(
                WorkItem.status == WorkItemStatus.RUNNING.value,
                WorkItem.lease_expires_at.is_not(None),
                WorkItem.lease_expires_at < current_time,
            ),
        ),
    )
    if supported_steps is not None:
        query = query.where(WorkItem.step_type.in_(supported_steps))
    candidates = list(
        session.scalars(query.order_by(WorkItem.available_at, WorkItem.created_at).limit(20))
    )
    for candidate in candidates:
        scope = STEP_SCOPE.get(candidate.step_type)
        if scope and scope.value in paused:
            continue
        previous_status = candidate.status
        previous_owner = candidate.lease_owner
        previous_expiry = candidate.lease_expires_at
        conditions = [
            WorkItem.id == candidate.id,
            WorkItem.attempt == candidate.attempt,
        ]
        if previous_status == WorkItemStatus.PENDING.value:
            conditions.append(WorkItem.status == WorkItemStatus.PENDING.value)
        else:
            conditions.extend(
                [
                    WorkItem.status == WorkItemStatus.RUNNING.value,
                    WorkItem.lease_owner == previous_owner,
                    WorkItem.lease_expires_at == previous_expiry,
                ]
            )
        result = session.execute(
            update(WorkItem)
            .where(*conditions)
            .values(
                status=WorkItemStatus.RUNNING.value,
                attempt=candidate.attempt + 1,
                lease_owner=worker_id,
                lease_expires_at=current_time + timedelta(seconds=lease_seconds),
                updated_at=current_time,
            )
        )
        if result.rowcount == 1:
            session.flush()
            session.refresh(candidate)
            return candidate
    return None


def renew_lease(
    session: Session,
    *,
    work_item_id: str,
    worker_id: str,
    lease_seconds: int = 60,
) -> WorkItem:
    item = session.get(WorkItem, work_item_id)
    if item is None or item.status != WorkItemStatus.RUNNING.value or item.lease_owner != worker_id:
        error = ConflictError("工作项租约已经丢失")
        error.code = "WORK_ITEM_LEASE_LOST"
        raise error
    item.lease_expires_at = utcnow() + timedelta(seconds=lease_seconds)
    return item


def complete_work_item(
    session: Session,
    *,
    work_item_id: str,
    worker_id: str,
    output_ref: Optional[str] = None,
) -> WorkItem:
    item = session.get(WorkItem, work_item_id)
    if item is None or item.status != WorkItemStatus.RUNNING.value or item.lease_owner != worker_id:
        error = ConflictError("工作项租约已经丢失")
        error.code = "WORK_ITEM_LEASE_LOST"
        raise error
    item.status = WorkItemStatus.SUCCEEDED.value
    item.output_ref = output_ref
    item.lease_owner = None
    item.lease_expires_at = None
    return item


def fail_work_item(
    session: Session,
    *,
    work_item_id: str,
    worker_id: str,
    error_code: str,
    error_detail: str,
    retryable: bool,
    retry_delay_seconds: int = 30,
) -> WorkItem:
    item = session.get(WorkItem, work_item_id)
    if item is None or item.status != WorkItemStatus.RUNNING.value or item.lease_owner != worker_id:
        error = ConflictError("工作项租约已经丢失")
        error.code = "WORK_ITEM_LEASE_LOST"
        raise error
    item.error_code = error_code
    item.error_detail = error_detail[:2000]
    item.lease_owner = None
    item.lease_expires_at = None
    if retryable and item.attempt < item.max_attempts:
        item.status = WorkItemStatus.PENDING.value
        item.available_at = utcnow() + timedelta(seconds=retry_delay_seconds)
    else:
        item.status = WorkItemStatus.FAILED.value
        task = session.get(ContentTask, item.task_id)
        if task is not None:
            try:
                transition_task(
                    session,
                    task_id=task.id,
                    target=TaskState.WAITING_HUMAN,
                    actor_type="system",
                    actor_id=worker_id,
                    trace_id=item.id,
                    reason=f"{error_code}: {error_detail[:500]}",
                )
            except ConflictError:
                add_audit(
                    session,
                    actor_type="system",
                    actor_id=worker_id,
                    action="work_item.failed",
                    resource_type="work_item",
                    resource_id=item.id,
                    trace_id=item.id,
                    reason=error_code,
                )
    return item
