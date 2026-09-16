from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


def new_id() -> str:
    return str(uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DomainError(Exception):
    code = "DOMAIN_ERROR"
    retryable = False

    def __init__(self, message: str, *, action: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.action = action


class NotFoundError(DomainError):
    code = "NOT_FOUND"


class ConflictError(DomainError):
    code = "CONFLICT"


class UnauthorizedError(DomainError):
    code = "UNAUTHORIZED"


class TaskState(str, Enum):
    PENDING_RESEARCH = "pending_research"
    RESEARCHING = "researching"
    PENDING_TOPIC_APPROVAL = "pending_topic_approval"
    PRODUCING = "producing"
    QUALITY_CHECKING = "quality_checking"
    PENDING_PUBLISH_APPROVAL = "pending_publish_approval"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    PUBLICATION_UNCERTAIN = "publication_uncertain"
    PUBLICATION_FAILED = "publication_failed"
    ENGAGING = "engaging"
    PENDING_REVIEW = "pending_review"
    ARCHIVED = "archived"
    WAITING_HUMAN = "waiting_human"
    CANCELLED = "cancelled"


ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING_RESEARCH: frozenset(
        {
            TaskState.RESEARCHING,
            TaskState.WAITING_HUMAN,
            TaskState.CANCELLED,
        }
    ),
    TaskState.RESEARCHING: frozenset(
        {
            TaskState.PENDING_TOPIC_APPROVAL,
            TaskState.WAITING_HUMAN,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PENDING_TOPIC_APPROVAL: frozenset(
        {
            TaskState.PRODUCING,
            TaskState.RESEARCHING,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PRODUCING: frozenset(
        {
            TaskState.QUALITY_CHECKING,
            TaskState.WAITING_HUMAN,
            TaskState.CANCELLED,
        }
    ),
    TaskState.QUALITY_CHECKING: frozenset(
        {
            TaskState.PRODUCING,
            TaskState.PENDING_PUBLISH_APPROVAL,
            TaskState.WAITING_HUMAN,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PENDING_PUBLISH_APPROVAL: frozenset(
        {
            TaskState.PRODUCING,
            TaskState.SCHEDULED,
            TaskState.CANCELLED,
        }
    ),
    TaskState.SCHEDULED: frozenset(
        {
            TaskState.PUBLISHING,
            TaskState.PENDING_PUBLISH_APPROVAL,
            TaskState.WAITING_HUMAN,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PUBLISHING: frozenset(
        {
            TaskState.PUBLISHED,
            TaskState.PUBLICATION_UNCERTAIN,
            TaskState.PUBLICATION_FAILED,
            TaskState.WAITING_HUMAN,
        }
    ),
    TaskState.PUBLICATION_UNCERTAIN: frozenset(
        {
            TaskState.PUBLISHED,
            TaskState.PUBLICATION_FAILED,
            TaskState.WAITING_HUMAN,
        }
    ),
    TaskState.PUBLICATION_FAILED: frozenset(
        {
            TaskState.PUBLISHING,
            TaskState.WAITING_HUMAN,
            TaskState.CANCELLED,
        }
    ),
    TaskState.PUBLISHED: frozenset({TaskState.ENGAGING, TaskState.PENDING_REVIEW}),
    TaskState.ENGAGING: frozenset({TaskState.PENDING_REVIEW, TaskState.WAITING_HUMAN}),
    TaskState.PENDING_REVIEW: frozenset({TaskState.ARCHIVED, TaskState.WAITING_HUMAN}),
    TaskState.WAITING_HUMAN: frozenset(
        {
            TaskState.RESEARCHING,
            TaskState.PRODUCING,
            TaskState.PENDING_PUBLISH_APPROVAL,
            TaskState.PUBLISHING,
            TaskState.PENDING_REVIEW,
            TaskState.CANCELLED,
        }
    ),
    TaskState.ARCHIVED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


class ApprovalType(str, Enum):
    TOPIC = "topic_approval"
    PUBLISH = "publish_approval"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    DELAYED = "delayed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REQUEST_CHANGES = "request_changes"
    DELAY = "delay"


class WorkItemStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExternalActionStatus(str, Enum):
    PREPARED = "prepared"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNCERTAIN = "uncertain"


class AutomationScope(str, Enum):
    RESEARCH = "research"
    PRODUCTION = "production"
    PUBLISHING = "publishing"
    COMMENTS = "comments"
    ARCHIVING = "archiving"
    ALL = "all"


STEP_SCOPE = {
    "collect_research": AutomationScope.RESEARCH,
    "produce_content": AutomationScope.PRODUCTION,
    # 视频流水线的六个阶段。归到已有的作用域里，
    # 这样「暂停自动化」对视频线同样生效，不需要另造一套开关。
    "video_collect": AutomationScope.RESEARCH,
    "video_select": AutomationScope.PRODUCTION,
    "video_materialize": AutomationScope.PRODUCTION,
    "video_compose": AutomationScope.PRODUCTION,
    "video_render": AutomationScope.PRODUCTION,
    "video_publish": AutomationScope.PUBLISHING,
    "prepare_publication": AutomationScope.PUBLISHING,
    "publish_content": AutomationScope.PUBLISHING,
    "collect_comments": AutomationScope.COMMENTS,
    "reply_comment": AutomationScope.COMMENTS,
    "archive_content": AutomationScope.ARCHIVING,
}


def assert_transition(current: TaskState, target: TaskState) -> None:
    if target not in ALLOWED_TRANSITIONS[current]:
        error = ConflictError(f"任务不能从 {current.value} 转换到 {target.value}")
        error.code = "STATE_CONFLICT"
        raise error
