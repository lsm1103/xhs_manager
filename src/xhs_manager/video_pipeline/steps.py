"""把视频流水线的六个阶段接进工作队列。

在这之前，视频线是 CLI 同步跑的：进程一挂就断在半路，失败没有重试，
同一个阶段跑两遍也没人拦。接进 work_items 之后这三件事都白来了。

阶段函数本身一行没改——`pipeline.run_stage(run_id, stage) -> dict`
的签名正好就是一个工作项处理器该有的样子。

链式推进
--------
每个阶段成功后入队下一个阶段，而不是一次把六个都排进去。
理由：下一阶段的入参（run_id）要等上一阶段跑完才确定，而且中途失败时
队列里不该躺着五个注定要失败的工作项。

幂等
----
idempotency_key 带上 run_id 和阶段名，同一条运行的同一个阶段只会有一个工作项。
重试是同一个工作项的 attempt 加一，不是新排一个。
"""

from __future__ import annotations

import logging
from datetime import timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from xhs_manager.domain import TaskState, WorkItemStatus, utcnow
from xhs_manager.models import ContentTask, WorkflowInstance, WorkItem
from xhs_manager.services import transition_task
from xhs_manager.video_pipeline.config import VideoPipelineSettings, get_video_settings
from xhs_manager.video_pipeline.domain import (
    PIPELINE_STAGE_ORDER,
    PipelineStatus,
    StageError,
)
from xhs_manager.video_pipeline.models import VideoTopic
from xhs_manager.video_pipeline.pipeline import VideoPipeline

logger = logging.getLogger(__name__)

# 工作项步骤名 ↔ 流水线阶段
STEP_PREFIX = "video_"
STAGE_STEPS: dict[PipelineStatus, str] = {
    PipelineStatus.COLLECTING: "video_collect",
    PipelineStatus.SELECTING: "video_select",
    PipelineStatus.MATERIALIZING: "video_materialize",
    PipelineStatus.COMPOSING: "video_compose",
    PipelineStatus.RENDERING: "video_render",
    PipelineStatus.PUBLISHING: "video_publish",
}
STEP_STAGES: dict[str, PipelineStatus] = {v: k for k, v in STAGE_STEPS.items()}

# 各阶段的重试次数。渲染和素材是外部依赖密集型（Pexels / ffmpeg / 浏览器），
# 值得多试一次；发布只试一次——重复发布比不发布更糟。
MAX_ATTEMPTS = {
    "video_collect": 3,
    "video_select": 2,
    "video_materialize": 3,
    "video_compose": 2,
    "video_render": 2,
    "video_publish": 1,
}

# 单个阶段的预计耗时上限，用于租约续期（见 worker 的心跳）。
# 渲染一支 2 分半的片子约 4 分钟，远超默认 60 秒租约。
STEP_LEASE_SECONDS = {
    "video_collect": 300,
    "video_select": 300,
    "video_materialize": 1800,
    "video_compose": 120,
    "video_render": 1800,
    "video_publish": 600,
}


def _workflow_for(session: Session, task: ContentTask) -> WorkflowInstance:
    """取任务的运行中工作流，没有就建一个。

    认领进来的任务刻意没有工作流（认领不推进流程），
    真正要跑生产时才建——这样「认领」和「开跑」仍然是两件事。
    """
    workflow = session.scalar(
        select(WorkflowInstance).where(
            WorkflowInstance.task_id == task.id,
            WorkflowInstance.status == "running",
        )
    )
    if workflow is None:
        workflow = WorkflowInstance(
            task_id=task.id, workflow_type="video_pipeline", current_step="video_collect",
        )
        session.add(workflow)
        session.flush()
    return workflow


def enqueue_stage(
    session: Session,
    *,
    task: ContentTask,
    run_id: str,
    stage: PipelineStatus,
    available_at=None,
) -> WorkItem | None:
    """为某个阶段排一个工作项。同一条运行的同一个阶段只排一次。"""
    step = STAGE_STEPS.get(stage)
    if step is None:
        return None

    key = f"run:{run_id}:{step}"
    existing = session.scalar(select(WorkItem).where(WorkItem.idempotency_key == key))
    if existing is not None:
        return existing

    workflow = _workflow_for(session, task)
    workflow.current_step = step
    item = WorkItem(
        workflow_id=workflow.id,
        task_id=task.id,
        step_type=step,
        idempotency_key=key,
        input_ref=run_id,
        max_attempts=MAX_ATTEMPTS.get(step, 2),
    )
    if available_at is not None:
        item.available_at = available_at
    session.add(item)
    session.flush()
    logger.info("入队 %s（run %s）", step, run_id[:8])
    return item


def _topic_for_task(session: Session, task_id: str) -> VideoTopic | None:
    return session.scalar(select(VideoTopic).where(VideoTopic.task_id == task_id))


def _next_stage(current: PipelineStatus) -> PipelineStatus | None:
    idx = PIPELINE_STAGE_ORDER.index(current)
    nxt = PIPELINE_STAGE_ORDER[idx + 1]
    return None if nxt == PipelineStatus.COMPLETED else nxt


def make_handlers(session_factory, settings: VideoPipelineSettings | None = None) -> dict:
    """构造 worker 用的处理器表。

    session_factory 只用于流水线自己的会话——阶段内部要长时间持有会话，
    和 worker 用来记账的那个会话分开，避免一个长事务锁住队列表。
    """
    conf = settings or get_video_settings()

    def run_stage_step(session: Session, item: WorkItem) -> str:
        stage = STEP_STAGES[item.step_type]
        run_id = item.input_ref
        if not run_id:
            raise StageError(item.step_type, "工作项没有带上 run_id")

        if stage is PipelineStatus.PUBLISHING:
            _assert_publishable(session, item)

        pipeline = VideoPipeline(session_factory, conf)
        result = pipeline.run_stage(run_id, stage)      # 阶段函数一行没改

        # 阶段成功了才推进
        task = session.get(ContentTask, item.task_id)
        if task is not None:
            _advance(session, task, run_id, stage)

        summary = result.get("status") or stage.value
        return f"run:{run_id}:{stage.value}:{summary}"[:240]

    def produce_content(session: Session, item: WorkItem) -> str:
        """主系统派发的生产步骤。视频任务在这里转入视频链。"""
        task = session.get(ContentTask, item.task_id)
        if task is None:
            raise StageError("produce_content", f"任务不存在: {item.task_id}")

        topic = _topic_for_task(session, task.id)
        if topic is None:
            # 图文任务的生产路径不在本模块——不假装处理，明确报出来
            raise StageError(
                "produce_content",
                "这个任务没有关联的视频选题；图文生产路径尚未实现",
            )

        run_id = topic.pipeline_run_id
        stage = _resume_stage(session, run_id)
        if stage is None:
            _finish(session, task, run_id)
            return f"run:{run_id}:already_complete"

        enqueue_stage(session, task=task, run_id=run_id, stage=stage)
        return f"run:{run_id}:enqueued:{stage.value}"

    handlers = {step: run_stage_step for step in STEP_STAGES}
    handlers["produce_content"] = produce_content
    return handlers


def _advance(session: Session, task: ContentTask, run_id: str,
             done: PipelineStatus) -> None:
    """一个阶段跑完之后该干什么。

    渲染完成是整条链上唯一的**人工闸门**：不再自动入队发布，
    而是提交发布审批。要发出去必须有人批准并排期——
    「渲染完就发」是视频线接进主系统之前的行为，不该保留。
    """
    if done is PipelineStatus.RENDERING:
        topic = _topic_for_task(session, task.id)
        if topic is not None:
            try:
                from xhs_manager.video_pipeline import promote
                promote.promote_for_approval(session, topic.id)
            except Exception as e:
                logger.warning("提交发布审批失败（成片本身已就绪）: %s", e)
        _finish(session, task, run_id)
        return

    nxt = _next_stage(done)
    if nxt is None:
        _finish(session, task, run_id)
        return
    enqueue_stage(session, task=task, run_id=run_id, stage=nxt)


def enqueue_publish(session: Session, *, task: ContentTask, run_id: str,
                    available_at=None) -> WorkItem | None:
    """排期到点之后，由发布计划把发布阶段放进队列。"""
    return enqueue_stage(
        session, task=task, run_id=run_id,
        stage=PipelineStatus.PUBLISHING, available_at=available_at,
    )


def _resume_stage(session: Session, run_id: str) -> PipelineStatus | None:
    """这条运行该从哪个阶段继续。已经完成的返回 None。"""
    from xhs_manager.video_pipeline.models import VideoPipelineRun

    run = session.get(VideoPipelineRun, run_id)
    if run is None:
        raise StageError("produce_content", f"流水线运行不存在: {run_id}")
    if run.status == PipelineStatus.COMPLETED.value:
        return None
    if run.status == PipelineStatus.FAILED.value:
        # 失败的运行从失败的那个阶段重来，而不是从头
        return PipelineStatus.COLLECTING
    try:
        return PipelineStatus(run.status)
    except ValueError:
        return PipelineStatus.COLLECTING


# 状态机不允许从 producing 直接跳到 pending_publish_approval，
# 中间必须经过 quality_checking。按合法路径一步步走，不绕过状态机。
_FINISH_PATH = (TaskState.QUALITY_CHECKING, TaskState.PENDING_PUBLISH_APPROVAL)


def _finish(session: Session, task: ContentTask, run_id: str) -> None:
    """整条链跑完。推进到「等人看」，不自动发布。"""
    for target in _FINISH_PATH:
        if task.state == target.value:
            continue
        try:
            transition_task(
                session, task_id=task.id, target=target,
                actor_type="system", actor_id="video_pipeline",
                trace_id=f"run:{run_id}",
                reason="视频流水线跑完" if target is TaskState.QUALITY_CHECKING
                else "成片就绪，等待发布审批",
            )
        except Exception as e:
            # 走不通就停在当前状态，别让一个状态跳转把整个阶段判失败——
            # 片子本身已经渲染好了。
            logger.warning(
                "任务 %s 推进到 %s 失败，停在 %s: %s",
                task.id[:8], target.value, task.state, e,
            )
            return


def _assert_publishable(session: Session, item: WorkItem) -> None:
    """发布前的闸门：必须有已批准的发布计划，且此刻在允许窗口内。

    没有这一关，队列里任何一个 video_publish 都会直接把片子推出去——
    包括手动排进去的、重试残留的。
    """
    from xhs_manager.video_pipeline import promote

    topic = _topic_for_task(session, item.task_id)
    if topic is None:
        raise StageError("publishing", "任务没有关联的视频选题")

    plan = promote.plan_for_topic(session, topic)
    if plan is None:
        raise StageError("publishing", "没有已批准的发布计划——发布需要先经过审批")

    now = utcnow()
    allowed_from = _aware(plan.allowed_from)
    allowed_until = _aware(plan.allowed_until)
    if allowed_from and now < allowed_from:
        raise StageError("publishing", f"还没到排期时间（{allowed_from.isoformat()}）")
    if allowed_until and now > allowed_until:
        # 过了窗口宁可不发：半夜把片子推出去比晚一天更糟
        raise StageError(
            "publishing",
            f"已错过允许发布窗口（{allowed_until.isoformat()}），需要重新排期",
        )


def _aware(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pending_items(session: Session, task_id: str) -> list[WorkItem]:
    return list(session.scalars(
        select(WorkItem).where(
            WorkItem.task_id == task_id,
            WorkItem.status.in_([WorkItemStatus.PENDING.value, WorkItemStatus.RUNNING.value]),
        ).order_by(WorkItem.created_at)
    ).all())
