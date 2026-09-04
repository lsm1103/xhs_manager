from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from xhs_manager.domain import ApprovalDecision, AutomationScope


class AccountCreate(BaseModel):
    command_id: str
    name: str = Field(min_length=1, max_length=120)
    timezone: str = "America/Los_Angeles"
    strategy_config: dict[str, Any]
    actor_id: str


class AccountCreated(BaseModel):
    account_id: str
    strategy_version_id: str
    status: str


class ContentTaskCreate(BaseModel):
    command_id: str
    account_id: str
    primary_goal: str = "content_validation"
    strategy_version_id: Optional[str] = None
    actor_id: str


SourceScope = Literal["web_search", "github", "xiaohongshu"]
SourceExecutionStatus = Literal["running", "succeeded", "failed", "rate_limited", "login_required"]
ContentKind = Literal["fact", "opinion", "question", "experience", "promotion", "unknown"]
VerificationState = Literal[
    "primary_source_verified",
    "secondary_only",
    "conflicting",
    "unverified",
    "unavailable",
]


class ResearchRunCreate(BaseModel):
    command_id: str
    account_id: str
    strategy_version_id: Optional[str] = None
    window_start: datetime
    window_end: datetime
    source_scopes: list[SourceScope] = Field(min_length=1)
    seed_queries: list[str] = Field(min_length=1)
    max_items_per_source: int = Field(default=30, ge=1, le=100)
    actor_id: str


class ResearchSourceCreate(BaseModel):
    command_id: str
    source_type: SourceScope
    query: str = Field(min_length=1, max_length=2000)
    status: SourceExecutionStatus = "succeeded"
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    raw_result_count: int = Field(default=0, ge=0)
    valid_result_count: int = Field(default=0, ge=0)
    rate_limit: dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = Field(default=None, max_length=80)
    error_detail: Optional[str] = Field(default=None, max_length=4000)
    actor_id: str


class ResearchSignalCreate(BaseModel):
    command_id: str
    research_source_id: str
    source_url: str = Field(min_length=1, max_length=8000)
    canonical_url: Optional[str] = Field(default=None, max_length=8000)
    original_pointer: Optional[str] = Field(default=None, max_length=8000)
    author_or_org: Optional[str] = Field(default=None, max_length=240)
    published_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    title: str = Field(min_length=1, max_length=4000)
    summary: str = Field(min_length=1, max_length=16000)
    content_kind: ContentKind
    freshness: Literal["recent", "evergreen", "unknown"] = "unknown"
    credibility: Literal["high", "medium", "low"]
    audience_relevance: float = Field(ge=0, le=1)
    copyright_risk: Literal["low", "medium", "high", "unknown"] = "unknown"
    verification_state: VerificationState
    content_digest: Optional[str] = Field(default=None, min_length=64, max_length=64)
    actor_id: str


class EvidencePackageCreate(BaseModel):
    command_id: str
    supported_question: str = Field(min_length=1, max_length=4000)
    supported_claim: Optional[str] = Field(default=None, max_length=8000)
    key_signal_ids: list[str] = Field(min_length=1)
    counterexamples_and_limits: list[str] = Field(default_factory=list)
    conclusion_strength: Literal["strong", "moderate", "weak"] = "weak"
    unresolved_conflicts: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    applicable_from: Optional[datetime] = None
    applicable_to: Optional[datetime] = None
    actor_id: str


class TopicProposalCreate(BaseModel):
    command_id: str
    task_id: str
    user_problem: str = Field(min_length=1, max_length=4000)
    working_title: str = Field(min_length=1, max_length=4000)
    core_claim: str = Field(min_length=1, max_length=4000)
    why_now: str = Field(min_length=1, max_length=4000)
    content_pillar: Literal[
        "concept_explainer",
        "workflow_experiment",
        "solution_comparison",
        "trend_response",
        "replication_experiment",
    ]
    primary_goal: str = Field(min_length=1, max_length=64)
    evidence_package_ids: list[str] = Field(min_length=1)
    counterpoints: list[str] = Field(default_factory=list)
    experiment_hypothesis: str = Field(min_length=1, max_length=4000)
    primary_metric: str = Field(min_length=1, max_length=80)
    recommended_format: Literal["carousel", "video"] = "carousel"
    scores: dict[str, int]
    score_explanation: str = Field(min_length=1, max_length=4000)
    risks: list[str] = Field(default_factory=list)
    actor_id: str


class ContentVersionCreate(BaseModel):
    command_id: str
    task_id: str
    title_candidates: list[str] = Field(min_length=2, max_length=5)
    selected_title: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=10000)
    interaction_question: str = Field(min_length=1, max_length=1000)
    topics: list[str] = Field(default_factory=list, max_length=8)
    cover_script: dict[str, Any]
    slide_scripts: list[dict[str, Any]] = Field(min_length=1, max_length=9)
    asset_paths: list[str] = Field(default_factory=list)
    source_notes: list[dict[str, Any]] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    generation_manifest: dict[str, Any]
    actor_id: str


class PublicationPlanCreate(BaseModel):
    command_id: str
    task_id: str
    approval_id: str
    scheduled_at: datetime
    allowed_from: datetime
    allowed_until: datetime
    actor_id: str


class DraftPreparationInput(BaseModel):
    actor_id: str = Field(min_length=1, max_length=120)


class ResearchRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    strategy_version_id: str
    window_start: datetime
    window_end: datetime
    source_scopes: list[str]
    seed_queries: list[str]
    max_items_per_source: int
    status: str
    source_summary: dict[str, Any]
    completed_at: Optional[datetime]


class ResearchSourceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    research_run_id: str
    source_type: str
    query: str
    status: str
    raw_result_count: int
    valid_result_count: int
    rate_limit: dict[str, Any]
    error_code: Optional[str]


class ResearchSignalView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    research_run_id: str
    source_type: str
    source_url: str
    canonical_url: str
    title: str
    summary: str
    verification_state: str
    content_digest: str


class EvidencePackageView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    research_run_id: str
    supported_question: str
    supported_claim: Optional[str]
    key_evidence: list[dict[str, Any]]
    source_independence: dict[str, Any]
    conclusion_strength: str
    unresolved_conflicts: list[str]
    gaps: list[str]
    publishable: bool


class ContentTaskView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    account_id: str
    state: str
    strategy_version_id: str
    primary_goal: str
    content_pillar: Optional[str]
    current_topic_version_id: Optional[str]
    current_content_version_id: Optional[str]
    scheduled_at: Optional[datetime]
    row_version: int
    created_at: datetime
    updated_at: datetime


class TopicProposalView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    version: int
    user_problem: str
    working_title: str
    core_claim: str
    content_pillar: str
    evidence_package_ids: list[str]
    total_score: int
    risks: list[str]
    status: str


class ContentVersionView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    topic_version_id: str
    version: int
    title_candidates: list[str]
    selected_title: str
    body: str
    interaction_question: str
    topics: list[str]
    cover_script: dict[str, Any]
    slide_scripts: list[dict[str, Any]]
    asset_paths: list[str]
    status: str


class PublicationPlanView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    content_version_id: str
    approval_id: str
    scheduled_at: datetime
    allowed_from: datetime
    allowed_until: datetime
    status: str


class ApprovalView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    approval_type: str
    resource_type: str
    resource_id: str
    resource_version: str
    status: str
    expires_at: datetime
    card_message_id: Optional[str]


class ApprovalDecisionInput(BaseModel):
    decision: ApprovalDecision
    operator_id: str
    event_id: str
    comment: str = ""
    scheduled_at: Optional[datetime] = None


class PauseInput(BaseModel):
    scope: AutomationScope
    reason: str = Field(min_length=1, max_length=1000)
    actor_id: str
    requires_manual_resume: bool = False


class ResumeInput(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)
    actor_id: str


class PauseView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scope: str
    active: bool
    reason: str
    created_by: str
    created_at: datetime
    requires_manual_resume: bool
    resumed_by: Optional[str]
    resumed_at: Optional[datetime]


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool
    action: str = ""


class ErrorResponse(BaseModel):
    error: ErrorDetail
