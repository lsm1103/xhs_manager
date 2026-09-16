import hashlib
import json
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from xhs_manager.config import Settings, get_settings
from xhs_manager.console.app import STATIC_DIR as CONSOLE_STATIC
from xhs_manager.console.app import create_console_router
from xhs_manager.db import create_db_engine, create_session_factory
from xhs_manager.domain import (
    ApprovalDecision,
    AutomationScope,
    DomainError,
    UnauthorizedError,
    utcnow,
)
from xhs_manager.models import (
    ApprovalRequest,
    ContentTask,
    ContentVersion,
    EvidencePackage,
    InboxEvent,
    PublicationPlan,
    ResearchRun,
    ResearchSignal,
    ResearchSource,
    SystemPause,
    TopicProposal,
)
from xhs_manager.publishing import prepare_xiaohongshu_draft
from xhs_manager.schemas import (
    AccountCreate,
    AccountCreated,
    ApprovalDecisionInput,
    ApprovalView,
    ContentTaskCreate,
    ContentTaskView,
    ContentVersionCreate,
    ContentVersionView,
    DraftPreparationInput,
    EvidencePackageCreate,
    EvidencePackageView,
    PauseInput,
    PauseView,
    PublicationPlanCreate,
    PublicationPlanView,
    ResearchRunCreate,
    ResearchRunView,
    ResearchSignalCreate,
    ResearchSignalView,
    ResearchSourceCreate,
    ResearchSourceView,
    ResumeInput,
    TopicProposalCreate,
    TopicProposalView,
)
from xhs_manager.services import (
    canonical_digest,
    create_account,
    create_content_task,
    create_content_version,
    create_evidence_package,
    create_publication_plan,
    create_research_run,
    create_topic_proposal,
    decide_approval,
    record_research_source,
    require_authorized_operator,
    resume_pause,
    set_pause,
    write_research_signal,
)


def create_app(
    *,
    settings: Optional[Settings] = None,
    engine: Optional[Engine] = None,
) -> FastAPI:
    app_settings = settings or get_settings()
    app_engine = engine or create_db_engine(app_settings.database_url)
    session_factory = create_session_factory(app_engine)

    app = FastAPI(title="小红书自动运营系统", version="0.1.0")
    app.state.settings = app_settings
    app.state.engine = app_engine
    app.state.session_factory = session_factory

    @app.exception_handler(DomainError)
    async def domain_error_handler(_request: Request, exc: DomainError) -> JSONResponse:
        status = 403 if isinstance(exc, UnauthorizedError) else 409
        if exc.code == "NOT_FOUND":
            status = 404
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "action": exc.action,
                }
            },
        )

    def get_session() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def require_internal_token(
        x_internal_token: str = Header(default="", alias="X-Internal-Token"),
    ) -> None:
        expected = app_settings.internal_api_token
        if expected and x_internal_token != expected:
            raise UnauthorizedError("内部接口令牌无效")

    # 控制台：本机运维视图，挂在同一个进程下。
    # 它只读，且不走 require_internal_token——鉴权靠「只绑回环地址」。
    app.include_router(create_console_router(get_session))
    if CONSOLE_STATIC.is_dir():
        app.mount(
            "/console/static",
            StaticFiles(directory=str(CONSOLE_STATIC)),
            name="console-static",
        )

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    def ready(session: Session = Depends(get_session)) -> dict[str, str]:
        session.execute(select(1))
        return {"status": "ready"}

    @app.post(
        "/v1/accounts",
        response_model=AccountCreated,
        dependencies=[Depends(require_internal_token)],
    )
    def create_account_endpoint(
        payload: AccountCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return create_account(
            session,
            command_id=payload.command_id,
            name=payload.name,
            timezone_name=payload.timezone,
            strategy_config=payload.strategy_config,
            actor_id=payload.actor_id,
        )

    @app.post(
        "/v1/tasks",
        dependencies=[Depends(require_internal_token)],
    )
    def create_task_endpoint(
        payload: ContentTaskCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return create_content_task(
            session,
            command_id=payload.command_id,
            account_id=payload.account_id,
            primary_goal=payload.primary_goal,
            strategy_version_id=payload.strategy_version_id,
            actor_id=payload.actor_id,
        )

    @app.post(
        "/v1/research-runs",
        dependencies=[Depends(require_internal_token)],
    )
    def create_research_run_endpoint(
        payload: ResearchRunCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return create_research_run(
            session,
            command_id=payload.command_id,
            account_id=payload.account_id,
            strategy_version_id=payload.strategy_version_id,
            window_start=payload.window_start,
            window_end=payload.window_end,
            source_scopes=payload.source_scopes,
            seed_queries=payload.seed_queries,
            max_items_per_source=payload.max_items_per_source,
            actor_id=payload.actor_id,
        )

    @app.get(
        "/v1/research-runs/{research_run_id}",
        response_model=ResearchRunView,
        dependencies=[Depends(require_internal_token)],
    )
    def get_research_run_endpoint(
        research_run_id: str,
        session: Session = Depends(get_session),
    ) -> ResearchRun:
        run = session.get(ResearchRun, research_run_id)
        if run is None:
            from xhs_manager.domain import NotFoundError

            raise NotFoundError("研究运行不存在")
        return run

    @app.post(
        "/v1/research-runs/{research_run_id}/sources",
        dependencies=[Depends(require_internal_token)],
    )
    def record_research_source_endpoint(
        research_run_id: str,
        payload: ResearchSourceCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return record_research_source(
            session,
            command_id=payload.command_id,
            research_run_id=research_run_id,
            source_type=payload.source_type,
            query=payload.query,
            status=payload.status,
            started_at=payload.started_at,
            completed_at=payload.completed_at,
            raw_result_count=payload.raw_result_count,
            valid_result_count=payload.valid_result_count,
            rate_limit=payload.rate_limit,
            error_code=payload.error_code,
            error_detail=payload.error_detail,
            actor_id=payload.actor_id,
        )

    @app.get(
        "/v1/research-sources/{research_source_id}",
        response_model=ResearchSourceView,
        dependencies=[Depends(require_internal_token)],
    )
    def get_research_source_endpoint(
        research_source_id: str,
        session: Session = Depends(get_session),
    ) -> ResearchSource:
        source = session.get(ResearchSource, research_source_id)
        if source is None:
            from xhs_manager.domain import NotFoundError

            raise NotFoundError("研究来源执行记录不存在")
        return source

    @app.post(
        "/v1/research-sources/{research_source_id}/signals",
        dependencies=[Depends(require_internal_token)],
    )
    def write_research_signal_endpoint(
        research_source_id: str,
        payload: ResearchSignalCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        if research_source_id != payload.research_source_id:
            raise UnauthorizedError("路径中的来源执行记录与请求体不一致")
        return write_research_signal(
            session,
            command_id=payload.command_id,
            research_source_id=payload.research_source_id,
            source_url=payload.source_url,
            canonical_url=payload.canonical_url,
            original_pointer=payload.original_pointer,
            author_or_org=payload.author_or_org,
            published_at=payload.published_at,
            collected_at=payload.collected_at,
            title=payload.title,
            summary=payload.summary,
            content_kind=payload.content_kind,
            freshness=payload.freshness,
            credibility=payload.credibility,
            audience_relevance=payload.audience_relevance,
            copyright_risk=payload.copyright_risk,
            verification_state=payload.verification_state,
            content_digest=payload.content_digest,
            actor_id=payload.actor_id,
        )

    @app.get(
        "/v1/research-signals/{research_signal_id}",
        response_model=ResearchSignalView,
        dependencies=[Depends(require_internal_token)],
    )
    def get_research_signal_endpoint(
        research_signal_id: str,
        session: Session = Depends(get_session),
    ) -> ResearchSignal:
        signal = session.get(ResearchSignal, research_signal_id)
        if signal is None:
            from xhs_manager.domain import NotFoundError

            raise NotFoundError("研究信号不存在")
        return signal

    @app.post(
        "/v1/research-runs/{research_run_id}/evidence-packages",
        dependencies=[Depends(require_internal_token)],
    )
    def create_evidence_package_endpoint(
        research_run_id: str,
        payload: EvidencePackageCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return create_evidence_package(
            session,
            command_id=payload.command_id,
            research_run_id=research_run_id,
            supported_question=payload.supported_question,
            supported_claim=payload.supported_claim,
            key_signal_ids=payload.key_signal_ids,
            counterexamples_and_limits=payload.counterexamples_and_limits,
            conclusion_strength=payload.conclusion_strength,
            unresolved_conflicts=payload.unresolved_conflicts,
            gaps=payload.gaps,
            applicable_from=payload.applicable_from,
            applicable_to=payload.applicable_to,
            actor_id=payload.actor_id,
        )

    @app.get(
        "/v1/evidence-packages/{evidence_package_id}",
        response_model=EvidencePackageView,
        dependencies=[Depends(require_internal_token)],
    )
    def get_evidence_package_endpoint(
        evidence_package_id: str,
        session: Session = Depends(get_session),
    ) -> EvidencePackage:
        package = session.get(EvidencePackage, evidence_package_id)
        if package is None:
            from xhs_manager.domain import NotFoundError

            raise NotFoundError("证据包不存在")
        return package

    @app.post(
        "/v1/topic-proposals",
        dependencies=[Depends(require_internal_token)],
    )
    def create_topic_proposal_endpoint(
        payload: TopicProposalCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return create_topic_proposal(
            session,
            command_id=payload.command_id,
            task_id=payload.task_id,
            user_problem=payload.user_problem,
            working_title=payload.working_title,
            core_claim=payload.core_claim,
            why_now=payload.why_now,
            content_pillar=payload.content_pillar,
            primary_goal=payload.primary_goal,
            evidence_package_ids=payload.evidence_package_ids,
            counterpoints=payload.counterpoints,
            experiment_hypothesis=payload.experiment_hypothesis,
            primary_metric=payload.primary_metric,
            recommended_format=payload.recommended_format,
            scores=payload.scores,
            score_explanation=payload.score_explanation,
            risks=payload.risks,
            actor_id=payload.actor_id,
        )

    @app.get(
        "/v1/topic-proposals/{topic_proposal_id}",
        response_model=TopicProposalView,
        dependencies=[Depends(require_internal_token)],
    )
    def get_topic_proposal_endpoint(
        topic_proposal_id: str,
        session: Session = Depends(get_session),
    ) -> TopicProposal:
        proposal = session.get(TopicProposal, topic_proposal_id)
        if proposal is None:
            from xhs_manager.domain import NotFoundError

            raise NotFoundError("选题不存在")
        return proposal

    @app.post(
        "/v1/content-versions",
        dependencies=[Depends(require_internal_token)],
    )
    def create_content_version_endpoint(
        payload: ContentVersionCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return create_content_version(
            session,
            command_id=payload.command_id,
            task_id=payload.task_id,
            title_candidates=payload.title_candidates,
            selected_title=payload.selected_title,
            body=payload.body,
            interaction_question=payload.interaction_question,
            topics=payload.topics,
            cover_script=payload.cover_script,
            slide_scripts=payload.slide_scripts,
            asset_paths=payload.asset_paths,
            source_notes=payload.source_notes,
            risk_notes=payload.risk_notes,
            generation_manifest=payload.generation_manifest,
            actor_id=payload.actor_id,
        )

    @app.get(
        "/v1/content-versions/{content_version_id}",
        response_model=ContentVersionView,
        dependencies=[Depends(require_internal_token)],
    )
    def get_content_version_endpoint(
        content_version_id: str,
        session: Session = Depends(get_session),
    ) -> ContentVersion:
        content = session.get(ContentVersion, content_version_id)
        if content is None:
            from xhs_manager.domain import NotFoundError

            raise NotFoundError("内容版本不存在")
        return content

    @app.post(
        "/v1/publication-plans",
        dependencies=[Depends(require_internal_token)],
    )
    def create_publication_plan_endpoint(
        payload: PublicationPlanCreate,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return create_publication_plan(
            session,
            command_id=payload.command_id,
            task_id=payload.task_id,
            approval_id=payload.approval_id,
            scheduled_at=payload.scheduled_at,
            allowed_from=payload.allowed_from,
            allowed_until=payload.allowed_until,
            actor_id=payload.actor_id,
        )

    @app.get(
        "/v1/publication-plans/{publication_plan_id}",
        response_model=PublicationPlanView,
        dependencies=[Depends(require_internal_token)],
    )
    def get_publication_plan_endpoint(
        publication_plan_id: str,
        session: Session = Depends(get_session),
    ) -> PublicationPlan:
        plan = session.get(PublicationPlan, publication_plan_id)
        if plan is None:
            from xhs_manager.domain import NotFoundError

            raise NotFoundError("发布计划不存在")
        return plan

    @app.post(
        "/v1/publication-plans/{publication_plan_id}/draft",
        dependencies=[Depends(require_internal_token)],
    )
    def prepare_publication_draft_endpoint(
        publication_plan_id: str,
        payload: DraftPreparationInput,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return prepare_xiaohongshu_draft(
            session,
            publication_plan_id=publication_plan_id,
            actor_id=payload.actor_id,
            project_root=Path.cwd(),
        )

    @app.get(
        "/v1/tasks/{task_id}",
        response_model=ContentTaskView,
        dependencies=[Depends(require_internal_token)],
    )
    def get_task_endpoint(
        task_id: str,
        session: Session = Depends(get_session),
    ) -> ContentTask:
        task = session.get(ContentTask, task_id)
        if task is None:
            from xhs_manager.domain import NotFoundError

            raise NotFoundError("内容任务不存在")
        return task

    @app.get(
        "/v1/approvals/{approval_id}",
        response_model=ApprovalView,
        dependencies=[Depends(require_internal_token)],
    )
    def get_approval_endpoint(
        approval_id: str,
        session: Session = Depends(get_session),
    ) -> ApprovalRequest:
        approval = session.get(ApprovalRequest, approval_id)
        if approval is None:
            from xhs_manager.domain import NotFoundError

            raise NotFoundError("审批请求不存在")
        return approval

    @app.post(
        "/v1/approvals/{approval_id}/decisions",
        dependencies=[Depends(require_internal_token)],
    )
    def decide_approval_endpoint(
        approval_id: str,
        payload: ApprovalDecisionInput,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        return decide_approval(
            session,
            approval_id=approval_id,
            decision=payload.decision,
            operator_id=payload.operator_id,
            event_id=payload.event_id,
            comment=payload.comment,
            scheduled_at=payload.scheduled_at,
        )

    @app.post(
        "/v1/automation/pauses",
        response_model=PauseView,
        dependencies=[Depends(require_internal_token)],
    )
    def pause_endpoint(
        payload: PauseInput,
        session: Session = Depends(get_session),
    ) -> SystemPause:
        return set_pause(
            session,
            scope=payload.scope,
            reason=payload.reason,
            actor_id=payload.actor_id,
            requires_manual_resume=payload.requires_manual_resume,
        )

    @app.delete(
        "/v1/automation/pauses/{scope}",
        response_model=PauseView,
        dependencies=[Depends(require_internal_token)],
    )
    def resume_endpoint(
        scope: AutomationScope,
        payload: ResumeInput,
        session: Session = Depends(get_session),
    ) -> SystemPause:
        return resume_pause(
            session,
            scope=scope,
            reason=payload.reason,
            actor_id=payload.actor_id,
        )

    @app.get(
        "/v1/automation/pauses",
        response_model=list[PauseView],
        dependencies=[Depends(require_internal_token)],
    )
    def list_pauses(session: Session = Depends(get_session)) -> list[SystemPause]:
        return list(session.scalars(select(SystemPause).order_by(SystemPause.scope)))

    @app.post("/webhooks/feishu/events")
    async def feishu_events(
        request: Request,
        session: Session = Depends(get_session),
    ) -> dict[str, Any]:
        payload = await request.json()
        token = payload.get("token") or payload.get("header", {}).get("token")
        expected_token = app_settings.feishu_verification_token
        if expected_token and token != expected_token:
            raise UnauthorizedError("飞书回调校验令牌无效")

        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        header = payload.get("header") or {}
        event_id = header.get("event_id") or payload.get("event_id")
        if not event_id:
            event_id = hashlib.sha256(
                json.dumps(payload, sort_keys=True).encode("utf-8")
            ).hexdigest()
        digest = canonical_digest(payload)
        existing = session.scalar(select(InboxEvent).where(InboxEvent.event_id == event_id))
        if existing is not None:
            return {"status": "ok", "duplicate": True, "result": existing.result}

        inbox = InboxEvent(
            source="feishu",
            event_id=event_id,
            payload_digest=digest,
        )
        session.add(inbox)
        session.flush()

        action = payload.get("action") or payload.get("event", {}).get("action") or {}
        value = action.get("value") or {}
        operator = payload.get("operator") or payload.get("event", {}).get("operator") or {}
        operator_id = (
            operator.get("open_id")
            or operator.get("operator_id", {}).get("open_id")
            or value.get("operator_id")
        )
        approval_id = value.get("approval_id")
        decision_value = value.get("decision")

        if approval_id and decision_value and operator_id:
            require_authorized_operator(
                operator_id,
                app_settings.feishu_allowed_user_ids,
            )
            scheduled_at = value.get("scheduled_at")
            if isinstance(scheduled_at, str):
                scheduled_at = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
            result = decide_approval(
                session,
                approval_id=approval_id,
                decision=ApprovalDecision(decision_value),
                operator_id=operator_id,
                event_id=event_id,
                comment=value.get("comment", ""),
                scheduled_at=scheduled_at,
            )
        else:
            result = {"ignored": True, "reason": "暂不支持该飞书事件"}

        inbox.status = "processed"
        inbox.result = result
        inbox.processed_at = utcnow()
        return {"status": "ok", "duplicate": False, "result": result}

    return app


app = create_app()
