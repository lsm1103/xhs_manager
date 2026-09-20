"""把视频产物提升成主系统的审批对象。

视频线原本渲染完就直接发——零审批、零排期。主系统那套（审批请求、
发布计划、允许窗口、幂等键）早就写好了，只是不认识视频。
这里把视频产物映射成它认识的东西，而不是给视频另造一套审批。

映射关系（设计文档里定的）：
    VideoTopic   → TopicProposal    选题提案，可审批、带版本
    VideoScript  → ContentVersion   scenes 映射成 slide_scripts
    审批通过后    → PublicationPlan  排期 + 允许窗口 + 幂等键

提升是幂等的：已经有对应行的直接复用，重复调用不会造出第二份。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from xhs_manager.domain import DomainError, utcnow
from xhs_manager.models import (
    ApprovalRequest,
    ContentTask,
    ContentVersion,
    PublicationPlan,
    TopicProposal,
)
from xhs_manager.services import add_audit
from xhs_manager.video_pipeline.models import (
    VideoComposition,
    VideoRender,
    VideoScript,
    VideoTopic,
)

logger = logging.getLogger(__name__)

APPROVAL_TTL = timedelta(days=7)
# 排期的默认允许窗口。超出窗口不发——宁可不发，也不要半夜把片子推出去。
DEFAULT_WINDOW = timedelta(hours=2)


class PromoteError(DomainError):
    code = "VIDEO_PROMOTE_ERROR"


@dataclass
class Promotion:
    topic_id: str
    proposal_id: str
    content_version_id: str
    approval_id: str
    created: bool


def latest_script(session: Session, topic_id: str) -> VideoScript | None:
    return session.scalar(
        select(VideoScript).where(VideoScript.topic_id == topic_id)
        .order_by(VideoScript.version.desc())
    )


def render_for(session: Session, script: VideoScript) -> VideoRender | None:
    comp = session.scalar(
        select(VideoComposition).where(VideoComposition.script_id == script.id)
    )
    if comp is None:
        return None
    return session.scalar(
        select(VideoRender).where(VideoRender.composition_id == comp.id)
        .order_by(VideoRender.created_at.desc())
    )


def _ensure_proposal(session: Session, topic: VideoTopic, task: ContentTask) -> TopicProposal:
    if topic.topic_proposal_id:
        existing = session.get(TopicProposal, topic.topic_proposal_id)
        if existing is not None:
            return existing

    proposal = TopicProposal(
        task_id=task.id,
        version=1,
        user_problem=topic.target_audience or "（未填写目标读者）",
        working_title=topic.title,
        core_claim=topic.angle,
        why_now=topic.why_now,
        content_pillar=topic.video_type,
        primary_goal=task.primary_goal or "video",
        # 视频线用 source_signal_ids 追溯来源，没有走 evidence_packages 那套
        evidence_package_ids=[],
        counterpoints=[],
        experiment_hypothesis="",
        primary_metric="completion_rate",
        recommended_format="video",
        scores=topic.scores or {},
        total_score=int(round(topic.total_score or 0)),
        score_explanation=f"来自视频流水线选题评分 {topic.total_score}",
        risks=[],
        status="approved",        # 选题在视频线里已经定了，这里只是补一条可追溯的记录
    )
    session.add(proposal)
    session.flush()
    topic.topic_proposal_id = proposal.id
    return proposal


def _ensure_content_version(
    session: Session, script: VideoScript, topic: VideoTopic,
    task: ContentTask, proposal: TopicProposal,
) -> ContentVersion:
    if script.content_version_id:
        existing = session.get(ContentVersion, script.content_version_id)
        if existing is not None:
            return existing

    meta = script.platform_metadata or {}
    xhs = meta.get("xiaohongshu") or {}
    titles = [m.get("title") for m in meta.values()
              if isinstance(m, dict) and m.get("title")]
    render = render_for(session, script)

    assets = []
    if render is not None:
        if render.output_path:
            assets.append(render.output_path)
        assets.extend((render.covers or {}).values())

    version = ContentVersion(
        task_id=task.id,
        topic_version_id=proposal.id,
        strategy_version_id=task.strategy_version_id,
        version=script.version,
        title_candidates=titles or [topic.title],
        selected_title=xhs.get("title") or topic.title,
        body=xhs.get("desc", ""),
        interaction_question="",
        topics=xhs.get("tags", []),
        cover_script={"covers": (render.covers if render else {}) or {}},
        # 设计里定的映射：视频的分镜就是这条内容的「屏」
        slide_scripts=list(script.scenes or []),
        asset_paths=assets,
        source_notes=[{"type": "video_signal", "id": sid}
                      for sid in (topic.source_signal_ids or [])],
        risk_notes=[],
        generation_manifest={
            "source": "video_pipeline",
            "script_id": script.id,
            "run_id": topic.pipeline_run_id,
            "model": script.generation_model,
            "duration": script.total_duration,
            "scenes": len(script.scenes or []),
            "render_id": render.id if render else None,
        },
        status="ready",
    )
    session.add(version)
    session.flush()
    script.content_version_id = version.id
    return version


def promote_for_approval(session: Session, topic_id: str, *,
                         actor_id: str = "video_pipeline") -> Promotion:
    """渲染完成之后，把这支片子提交发布审批。

    幂等：已经提交过的直接返回原来的审批请求，不会重复挂一条。
    """
    topic = session.get(VideoTopic, topic_id)
    if topic is None:
        raise PromoteError(f"视频选题不存在: {topic_id}")
    if not topic.task_id:
        raise PromoteError("这个选题还没认领进任务，先跑 cli adopt")

    task = session.get(ContentTask, topic.task_id)
    if task is None:
        raise PromoteError(f"内容任务不存在: {topic.task_id}")

    script = latest_script(session, topic.id)
    if script is None:
        raise PromoteError("这个选题还没有脚本")

    render = render_for(session, script)
    if render is None or render.status != "completed":
        raise PromoteError("成片还没渲染完，不能提交发布审批")

    proposal = _ensure_proposal(session, topic, task)
    version = _ensure_content_version(session, script, topic, task, proposal)

    existing = session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.resource_type == "content_version",
            ApprovalRequest.resource_id == version.id,
            ApprovalRequest.status == "pending",
        )
    )
    if existing is not None:
        return Promotion(topic.id, proposal.id, version.id, existing.id, created=False)

    approval = ApprovalRequest(
        approval_type="publish",
        resource_type="content_version",
        resource_id=version.id,
        resource_version=str(version.version),
        expires_at=utcnow() + APPROVAL_TTL,
    )
    session.add(approval)
    session.flush()
    task.current_topic_version_id = proposal.id
    task.current_content_version_id = version.id

    add_audit(
        session, actor_type="system", actor_id=actor_id,
        action="video.submitted_for_approval", resource_type="content_version",
        resource_id=version.id, trace_id=f"topic:{topic.id}",
        reason=f"成片 {render.duration:.0f}s 就绪，等待发布审批",
    )
    logger.info("提交发布审批: %s（内容版本 %s）", topic.title[:24], version.id[:8])
    return Promotion(topic.id, proposal.id, version.id, approval.id, created=True)


def approve_and_schedule(
    session: Session, approval_id: str, *, scheduled_at,
    window: timedelta = DEFAULT_WINDOW, actor_id: str = "console",
    comment: str = "",
) -> PublicationPlan:
    """批准发布并排期。返回发布计划。

    幂等键落在计划上：同一个审批只会产生一个计划，
    重复批准不会排出第二条发布。
    """
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise PromoteError(f"审批请求不存在: {approval_id}")
    if approval.status not in ("pending", "approved"):
        raise PromoteError(f"审批已是 {approval.status}，不能再批准")

    version = session.get(ContentVersion, approval.resource_id)
    if version is None:
        raise PromoteError("审批指向的内容版本不存在")
    task = session.get(ContentTask, version.task_id)
    if task is None:
        raise PromoteError("内容版本没有对应任务")

    key = f"plan:{approval.id}"
    existing = session.scalar(
        select(PublicationPlan).where(PublicationPlan.idempotency_key == key)
    )
    if existing is not None:
        if existing.status != "cancelled":
            return existing        # 连点两下只排一次
        # 撤销过的计划要能重新排期，否则撤销一次就等于永久废掉这支片子。
        existing.status = "scheduled"
        existing.scheduled_at = scheduled_at
        existing.allowed_from = scheduled_at
        existing.allowed_until = scheduled_at + window
        approval.status = "approved"
        task.scheduled_at = scheduled_at
        add_audit(
            session, actor_type="human", actor_id=actor_id,
            action="video.publish_approved", resource_type="content_version",
            resource_id=version.id, trace_id=f"approval:{approval.id}",
            reason=comment or f"重新排期 {scheduled_at.isoformat()}",
        )
        return existing

    approval.status = "approved"
    plan = PublicationPlan(
        task_id=task.id,
        account_id=task.account_id,
        content_version_id=version.id,
        approval_id=approval.id,
        scheduled_at=scheduled_at,
        allowed_from=scheduled_at,
        allowed_until=scheduled_at + window,
        idempotency_key=key,
    )
    session.add(plan)
    session.flush()
    task.scheduled_at = scheduled_at

    add_audit(
        session, actor_type="human", actor_id=actor_id,
        action="video.publish_approved", resource_type="content_version",
        resource_id=version.id, trace_id=f"approval:{approval.id}",
        reason=comment or f"排期 {scheduled_at.isoformat()}",
    )
    return plan


def reject(session: Session, approval_id: str, *, actor_id: str = "console",
           comment: str = "") -> ApprovalRequest:
    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise PromoteError(f"审批请求不存在: {approval_id}")
    approval.status = "rejected"
    add_audit(
        session, actor_type="human", actor_id=actor_id,
        action="video.publish_rejected", resource_type="content_version",
        resource_id=approval.resource_id, trace_id=f"approval:{approval.id}",
        reason=comment or "发布被打回",
    )
    return approval


def cancel_plan(session: Session, plan_id: str, *, actor_id: str = "console",
                comment: str = "") -> PublicationPlan:
    """撤销一条已排期的发布。

    批准是个会真的把片子推出去的动作，所以它必须可撤销。
    计划置为 cancelled 之后，发布闸门（steps._assert_publishable）会拦下来；
    队列里那条还没跑的 video_publish 由调用方一并清掉。
    """
    plan = session.get(PublicationPlan, plan_id)
    if plan is None:
        raise PromoteError(f"发布计划不存在: {plan_id}")
    if plan.status not in ("scheduled", "cancelled"):
        raise PromoteError(f"计划已是 {plan.status}，不能撤销")
    plan.status = "cancelled"
    task = session.get(ContentTask, plan.task_id)
    if task is not None:
        task.scheduled_at = None
    add_audit(
        session, actor_type="human", actor_id=actor_id,
        action="video.publish_cancelled", resource_type="content_version",
        resource_id=plan.content_version_id, trace_id=f"plan:{plan.id}",
        reason=comment or "排期被撤销",
    )
    return plan


def plan_for_topic(session: Session, topic: VideoTopic) -> PublicationPlan | None:
    """这支片子有没有一条已批准的发布计划。"""
    script = latest_script(session, topic.id)
    if script is None or not script.content_version_id:
        return None
    return session.scalar(
        select(PublicationPlan)
        .where(PublicationPlan.content_version_id == script.content_version_id)
        .order_by(PublicationPlan.created_at.desc())
    )


def pending_approvals(session: Session) -> list[dict]:
    """所有等着人看的视频发布审批。"""
    rows = session.scalars(
        select(ApprovalRequest).where(
            ApprovalRequest.approval_type == "publish",
            ApprovalRequest.status == "pending",
        ).order_by(ApprovalRequest.created_at)
    ).all()

    out = []
    for a in rows:
        version = session.get(ContentVersion, a.resource_id)
        if version is None:
            continue
        script = session.scalar(
            select(VideoScript).where(VideoScript.content_version_id == version.id)
        )
        if script is None:
            continue          # 图文的审批不在这里列
        out.append({
            "approval_id": a.id,
            "content_version_id": version.id,
            "title": version.selected_title,
            "task_id": version.task_id,
            "expires_at": a.expires_at,
            "scenes": len(script.scenes or []),
            "duration": script.total_duration,
        })
    return out
