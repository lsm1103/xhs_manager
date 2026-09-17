from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from xhs_manager.domain import (
    ApprovalStatus,
    ExternalActionStatus,
    TaskState,
    WorkItemStatus,
    new_id,
    utcnow,
)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="xiaohongshu")
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="America/Los_Angeles")
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AccountStrategyVersion(Base):
    __tablename__ = "account_strategy_versions"
    __table_args__ = (
        UniqueConstraint("account_id", "version", name="uq_strategy_account_version"),
        Index(
            "uq_strategy_active_account",
            "account_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    config: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchRun(Base):
    __tablename__ = "research_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(
        ForeignKey("account_strategy_versions.id"), nullable=False
    )
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_scopes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    seed_queries: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    max_items_per_source: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="created")
    source_summary: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchSource(Base):
    __tablename__ = "research_sources"
    __table_args__ = (Index("ix_research_source_run", "research_run_id", "source_type"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    query: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    raw_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    valid_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rate_limit: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    error_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchSignal(Base):
    __tablename__ = "research_signals"
    __table_args__ = (
        UniqueConstraint("research_run_id", "canonical_url", name="uq_research_signal_canonical"),
        UniqueConstraint("research_run_id", "content_digest", name="uq_research_signal_digest"),
        Index("ix_research_signal_run_verification", "research_run_id", "verification_state"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    author_or_org: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    content_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    freshness: Mapped[str] = mapped_column(String(32), nullable=False)
    credibility: Mapped[str] = mapped_column(String(16), nullable=False)
    audience_relevance: Mapped[float] = mapped_column(nullable=False)
    copyright_risk: Mapped[str] = mapped_column(String(16), nullable=False)
    verification_state: Mapped[str] = mapped_column(String(40), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchSignalSource(Base):
    """Preserves every independent source execution when a signal is deduplicated."""

    __tablename__ = "research_signal_sources"
    __table_args__ = (
        UniqueConstraint("research_signal_id", "research_source_id", name="uq_signal_source_link"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_signal_id: Mapped[str] = mapped_column(
        ForeignKey("research_signals.id"), nullable=False
    )
    research_source_id: Mapped[str] = mapped_column(
        ForeignKey("research_sources.id"), nullable=False
    )
    original_source_url: Mapped[str] = mapped_column(Text, nullable=False)
    original_pointer: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidencePackage(Base):
    __tablename__ = "evidence_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    research_run_id: Mapped[str] = mapped_column(ForeignKey("research_runs.id"), nullable=False)
    supported_question: Mapped[str] = mapped_column(Text, nullable=False)
    supported_claim: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    key_evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    counterexamples_and_limits: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    source_independence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    conclusion_strength: Mapped[str] = mapped_column(String(32), nullable=False)
    unresolved_conflicts: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    gaps: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    applicable_from: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applicable_to: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    publishable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TopicProposal(Base):
    __tablename__ = "topic_proposals"
    __table_args__ = (Index("ix_topic_proposal_task_version", "task_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("content_tasks.id"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    user_problem: Mapped[str] = mapped_column(Text, nullable=False)
    working_title: Mapped[str] = mapped_column(Text, nullable=False)
    core_claim: Mapped[str] = mapped_column(Text, nullable=False)
    why_now: Mapped[str] = mapped_column(Text, nullable=False)
    content_pillar: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_goal: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_package_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    counterpoints: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    experiment_hypothesis: Mapped[str] = mapped_column(Text, nullable=False)
    primary_metric: Mapped[str] = mapped_column(String(80), nullable=False)
    recommended_format: Mapped[str] = mapped_column(String(32), nullable=False, default="carousel")
    scores: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    total_score: Mapped[int] = mapped_column(Integer, nullable=False)
    score_explanation: Mapped[str] = mapped_column(Text, nullable=False)
    risks: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending_approval")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContentVersion(Base):
    __tablename__ = "content_versions"
    __table_args__ = (Index("ix_content_version_task_version", "task_id", "version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("content_tasks.id"), nullable=False)
    topic_version_id: Mapped[str] = mapped_column(ForeignKey("topic_proposals.id"), nullable=False)
    strategy_version_id: Mapped[str] = mapped_column(
        ForeignKey("account_strategy_versions.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title_candidates: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    selected_title: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    interaction_question: Mapped[str] = mapped_column(Text, nullable=False)
    topics: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    cover_script: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    slide_scripts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    asset_paths: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    source_notes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False, default=list)
    risk_notes: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    generation_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PublicationPlan(Base):
    __tablename__ = "publication_plans"
    __table_args__ = (UniqueConstraint("idempotency_key", name="uq_publication_plan_idempotency"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("content_tasks.id"), nullable=False)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    content_version_id: Mapped[str] = mapped_column(
        ForeignKey("content_versions.id"), nullable=False
    )
    approval_id: Mapped[str] = mapped_column(ForeignKey("approval_requests.id"), nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    allowed_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    allowed_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ContentTask(Base):
    __tablename__ = "content_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), nullable=False)
    state: Mapped[str] = mapped_column(
        String(48), nullable=False, default=TaskState.PENDING_RESEARCH.value
    )
    strategy_version_id: Mapped[str] = mapped_column(
        ForeignKey("account_strategy_versions.id"), nullable=False
    )
    primary_goal: Mapped[str] = mapped_column(String(64), nullable=False)
    content_pillar: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    current_topic_version_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    current_content_version_id: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class WorkflowInstance(Base):
    __tablename__ = "workflow_instances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("content_tasks.id"), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    current_step: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    context: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkItem(Base):
    __tablename__ = "work_items"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_work_item_idempotency"),
        Index("ix_work_item_claim", "status", "available_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workflow_id: Mapped[str] = mapped_column(ForeignKey("workflow_instances.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(ForeignKey("content_tasks.id"), nullable=False)
    step_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=WorkItemStatus.PENDING.value
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    lease_owner: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False)
    input_ref: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    output_ref: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ExternalAction(Base):
    __tablename__ = "external_actions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(240), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ExternalActionStatus.PREPARED.value
    )
    request_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    evidence_ref: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_resource", "resource_type", "resource_id", "resource_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    approval_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_version: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ApprovalStatus.PENDING.value
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    card_message_id: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApprovalDecisionRecord(Base):
    __tablename__ = "approval_decisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    approval_request_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id"), nullable=False
    )
    operator_id: Mapped[str] = mapped_column(String(120), nullable=False)
    decision: Mapped[str] = mapped_column(String(40), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False, default="")
    event_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InboxEvent(Base):
    __tablename__ = "inbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    event_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    payload_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="received")
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    processed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class ProcessedCommand(Base):
    __tablename__ = "processed_commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    command_id: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    command_type: Mapped[str] = mapped_column(String(80), nullable=False)
    request_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    actor_type: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(80), nullable=False)
    before_state: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    after_state: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    trace_id: Mapped[str] = mapped_column(String(160), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SystemPause(Base):
    __tablename__ = "system_pauses"

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    requires_manual_resume: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    resumed_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    resumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TtsGeneration(Base):
    """一次配音生成记录。

    原来住在 data/tts_studio.db 里。搬进主库是「合并成一个系统」的一部分：
    配音是内容生产的一环，它的历史应该和任务、成片在同一个地方查。
    """

    __tablename__ = "tts_generations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(String(40), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    voice: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    ref_audio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    params: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    audio_path: Mapped[str] = mapped_column(Text, nullable=False)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elapsed: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rtf: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sample_rate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 波形峰值（下采样后的 JSON 数组）。存下来前端直接画，
    # 不用每次重新解码音频。
    waveform: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
