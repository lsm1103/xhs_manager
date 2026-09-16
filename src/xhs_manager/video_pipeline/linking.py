"""把视频选题认领进内容任务模型。

这是两条内容线打通的第一步：视频线拿到账号归属和任务状态，
之后才谈得上审批、排期和审计。

为什么不直接复用 services.create_content_task
-------------------------------------------
它会顺手入队一个 `collect_research` 工作项——对新任务是对的，
对一支**已经渲染完甚至已经发布**的片子就完全不对了：worker 会去给
一个早就做完的东西做调研。所以认领走独立路径，直接按视频的真实进度
落到对应的任务状态，不入队任何工作项。

认领是显式且可逆的：不自动跑、不猜账号，撤销只需把 task_id 置空。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from xhs_manager.domain import DomainError, TaskState
from xhs_manager.models import Account, AccountStrategyVersion, ContentTask
from xhs_manager.services import add_audit
from xhs_manager.video_pipeline.models import (
    VideoComposition,
    VideoPublication,
    VideoRender,
    VideoScript,
    VideoTopic,
)


class LinkError(DomainError):
    code = "VIDEO_LINK_ERROR"


# 视频的真实进度 → 任务状态。用主系统已有的状态词汇，不另造一套。
# 认领只是「承认它已经走到哪儿了」，不推进任何流程。
def task_state_for(session: Session, topic: VideoTopic) -> str:
    script = session.scalar(
        select(VideoScript).where(VideoScript.topic_id == topic.id)
        .order_by(VideoScript.version.desc())
    )
    pubs = session.scalars(
        select(VideoPublication).where(VideoPublication.topic_id == topic.id)
    ).all()

    if any(p.status == "published" for p in pubs):
        return TaskState.PUBLISHED.value
    if any(p.status == "failed" for p in pubs):
        return TaskState.PUBLICATION_FAILED.value

    render = None
    if script is not None:
        comp = session.scalar(
            select(VideoComposition).where(VideoComposition.script_id == script.id)
        )
        if comp is not None:
            render = session.scalar(
                select(VideoRender).where(VideoRender.composition_id == comp.id)
            )

    if render is not None:
        if render.status == "failed":
            return TaskState.WAITING_HUMAN.value
        if render.status == "completed":
            return TaskState.PENDING_PUBLISH_APPROVAL.value
    return TaskState.PRODUCING.value


@dataclass
class AdoptResult:
    topic_id: str
    task_id: str
    state: str
    created_task: bool


def ensure_default_account(session: Session, *, name: str, timezone_name: str = "Asia/Shanghai",
                           actor_id: str = "console") -> tuple[str, str]:
    """返回 (account_id, strategy_version_id)。已有账号就复用第一个，不新建。"""
    account = session.scalars(select(Account).order_by(Account.created_at)).first()
    if account is None:
        account = Account(name=name, timezone=timezone_name)
        session.add(account)
        session.flush()
        add_audit(
            session, actor_type="system", actor_id=actor_id,
            action="account.created", resource_type="account",
            resource_id=account.id, trace_id=f"adopt:{account.id}",
            after_state=account.status,
        )

    strategy = session.scalar(
        select(AccountStrategyVersion).where(
            AccountStrategyVersion.account_id == account.id,
            AccountStrategyVersion.status == "active",
        )
    )
    if strategy is None:
        strategy = AccountStrategyVersion(
            account_id=account.id, version=1, status="active",
            config={"source": "video_pipeline", "note": "由视频线认领时自动建立"},
            created_by=actor_id,
        )
        session.add(strategy)
        session.flush()
    return account.id, strategy.id


def adopt_topic(
    session: Session,
    topic_id: str,
    *,
    account_id: str | None = None,
    task_id: str | None = None,
    actor_id: str = "console",
) -> AdoptResult:
    """把一个视频选题挂到任务上。

    给 task_id 就挂到那个已有任务；否则按视频的真实进度新建一个任务。
    重复认领是幂等的——已经挂好的直接返回，不会重复建任务。
    """
    topic = session.get(VideoTopic, topic_id)
    if topic is None:
        raise LinkError(f"视频选题不存在: {topic_id}")

    if topic.task_id:
        existing = session.get(ContentTask, topic.task_id)
        return AdoptResult(topic.id, topic.task_id,
                           existing.state if existing else "", created_task=False)

    if task_id:
        task = session.get(ContentTask, task_id)
        if task is None:
            raise LinkError(f"内容任务不存在: {task_id}")
        topic.task_id = task.id
        _audit_link(session, topic, task, actor_id, created=False)
        return AdoptResult(topic.id, task.id, task.state, created_task=False)

    if account_id is None:
        raise LinkError("新建任务需要指定 account_id")
    account = session.get(Account, account_id)
    if account is None:
        raise LinkError(f"账号不存在: {account_id}")

    strategy = session.scalar(
        select(AccountStrategyVersion).where(
            AccountStrategyVersion.account_id == account_id,
            AccountStrategyVersion.status == "active",
        )
    )
    if strategy is None:
        raise LinkError("账号没有生效的策略版本")

    state = task_state_for(session, topic)
    task = ContentTask(
        account_id=account_id,
        strategy_version_id=strategy.id,
        primary_goal="video",
        content_pillar=topic.video_type,
        state=state,
    )
    session.add(task)
    session.flush()
    topic.task_id = task.id

    # 刻意不建 WorkflowInstance / WorkItem：
    # 认领的是已经做完（或做到一半）的片子，入队只会让 worker 重做一遍。
    _audit_link(session, topic, task, actor_id, created=True)
    return AdoptResult(topic.id, task.id, state, created_task=True)


def _audit_link(session: Session, topic: VideoTopic, task: ContentTask,
                actor_id: str, created: bool) -> None:
    add_audit(
        session, actor_type="human", actor_id=actor_id,
        action="video_topic.adopted", resource_type="video_topic",
        resource_id=topic.id, trace_id=f"adopt:{topic.id}",
        after_state=task.state,
        reason=f"task_id={task.id} created_task={created}",
    )


def unlink_topic(session: Session, topic_id: str, *, actor_id: str = "console") -> None:
    """撤销认领。任务本身留着——删任务是另一回事，不在这里顺手做。"""
    topic = session.get(VideoTopic, topic_id)
    if topic is None:
        raise LinkError(f"视频选题不存在: {topic_id}")
    if not topic.task_id:
        return
    previous = topic.task_id
    topic.task_id = None
    add_audit(
        session, actor_type="human", actor_id=actor_id,
        action="video_topic.unlinked", resource_type="video_topic",
        resource_id=topic.id, trace_id=f"unlink:{topic.id}",
        reason=f"task_id={previous}",
    )


def orphan_topics(session: Session) -> list[VideoTopic]:
    return list(session.scalars(
        select(VideoTopic).where(VideoTopic.task_id.is_(None))
        .order_by(VideoTopic.created_at)
    ).all())


def adopt_all_orphans(session: Session, *, account_id: str,
                      actor_id: str = "console") -> list[AdoptResult]:
    return [adopt_topic(session, t.id, account_id=account_id, actor_id=actor_id)
            for t in orphan_topics(session)]
