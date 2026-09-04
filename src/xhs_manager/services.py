import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from xhs_manager.domain import (
    ApprovalDecision,
    ApprovalStatus,
    ApprovalType,
    AutomationScope,
    ConflictError,
    NotFoundError,
    TaskState,
    UnauthorizedError,
    assert_transition,
    new_id,
    utcnow,
)
from xhs_manager.models import (
    Account,
    AccountStrategyVersion,
    ApprovalDecisionRecord,
    ApprovalRequest,
    AuditLog,
    ContentTask,
    ContentVersion,
    EvidencePackage,
    ProcessedCommand,
    PublicationPlan,
    ResearchRun,
    ResearchSignal,
    ResearchSignalSource,
    ResearchSource,
    SystemPause,
    TopicProposal,
    WorkflowInstance,
    WorkItem,
)


def canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def normalize_canonical_url(value: str) -> str:
    """Normalize only deterministic URL components; never fetch or expand a URL."""
    parsed = urlsplit(value.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        error = ConflictError("来源地址必须是完整的 HTTP(S) 地址")
        error.code = "RESEARCH_OUTPUT_INVALID"
        raise error
    host = parsed.hostname.lower() if parsed.hostname else ""
    if parsed.port and not (
        (parsed.scheme == "http" and parsed.port == 80)
        or (parsed.scheme == "https" and parsed.port == 443)
    ):
        host = f"{host}:{parsed.port}"
    path = parsed.path or "/"
    return urlunsplit((parsed.scheme.lower(), host, path, parsed.query, ""))


def research_content_digest(*, title: str, summary: str, content_kind: str) -> str:
    return canonical_digest(
        {
            "title": " ".join(title.split()).casefold(),
            "summary": " ".join(summary.split()).casefold(),
            "content_kind": content_kind,
        }
    )


_SENSITIVE_CONNECTOR_DETAIL = re.compile(
    r"(?:authorization|bearer\s+|api[_ -]?key|access[_ -]?token|"
    r"refresh[_ -]?token|cookie|set-cookie|session(?:id)?\s*=)",
    re.IGNORECASE,
)


def sanitize_research_error_detail(value: Optional[str]) -> Optional[str]:
    """Keep diagnosable connector failures without persisting credentials or page bodies."""
    if not value:
        return None
    if _SENSITIVE_CONNECTOR_DETAIL.search(value):
        return "[已脱敏的连接器错误详情]"
    return value.strip()[:500]


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def add_audit(
    session: Session,
    *,
    actor_type: str,
    actor_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    trace_id: str,
    reason: str = "",
    before_state: Optional[str] = None,
    after_state: Optional[str] = None,
) -> None:
    session.add(
        AuditLog(
            actor_type=actor_type,
            actor_id=actor_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            before_state=before_state,
            after_state=after_state,
            reason=reason,
            trace_id=trace_id,
        )
    )


def _processed_result(
    session: Session,
    *,
    command_id: str,
    request_digest: str,
) -> Optional[dict[str, Any]]:
    processed = session.scalar(
        select(ProcessedCommand).where(ProcessedCommand.command_id == command_id)
    )
    if processed is None:
        return None
    if processed.request_digest != request_digest:
        error = ConflictError("相同命令标识对应了不同请求")
        error.code = "IDEMPOTENCY_CONFLICT"
        raise error
    return dict(processed.result)


def _save_processed(
    session: Session,
    *,
    command_id: str,
    command_type: str,
    request_digest: str,
    resource_type: str,
    resource_id: str,
    result: dict[str, Any],
) -> None:
    session.add(
        ProcessedCommand(
            command_id=command_id,
            command_type=command_type,
            request_digest=request_digest,
            resource_type=resource_type,
            resource_id=resource_id,
            result=result,
        )
    )


def create_account(
    session: Session,
    *,
    command_id: str,
    name: str,
    timezone_name: str,
    strategy_config: dict[str, Any],
    actor_id: str,
) -> dict[str, Any]:
    request = {
        "name": name,
        "timezone": timezone_name,
        "strategy_config": strategy_config,
        "actor_id": actor_id,
    }
    digest = canonical_digest(request)
    previous = _processed_result(session, command_id=command_id, request_digest=digest)
    if previous is not None:
        return previous

    account = Account(name=name, timezone=timezone_name)
    session.add(account)
    session.flush()
    strategy = AccountStrategyVersion(
        account_id=account.id,
        version=1,
        status="active",
        config=strategy_config,
        created_by=actor_id,
    )
    session.add(strategy)
    session.flush()

    result = {
        "account_id": account.id,
        "strategy_version_id": strategy.id,
        "status": account.status,
    }
    _save_processed(
        session,
        command_id=command_id,
        command_type="CreateAccount",
        request_digest=digest,
        resource_type="account",
        resource_id=account.id,
        result=result,
    )
    add_audit(
        session,
        actor_type="human",
        actor_id=actor_id,
        action="account.created",
        resource_type="account",
        resource_id=account.id,
        trace_id=command_id,
        after_state=account.status,
    )
    return result


def create_content_task(
    session: Session,
    *,
    command_id: str,
    account_id: str,
    primary_goal: str,
    actor_id: str,
    strategy_version_id: Optional[str] = None,
) -> dict[str, Any]:
    request = {
        "account_id": account_id,
        "primary_goal": primary_goal,
        "strategy_version_id": strategy_version_id,
        "actor_id": actor_id,
    }
    digest = canonical_digest(request)
    previous = _processed_result(session, command_id=command_id, request_digest=digest)
    if previous is not None:
        return previous

    account = session.get(Account, account_id)
    if account is None:
        raise NotFoundError("账号不存在")
    if strategy_version_id:
        strategy = session.get(AccountStrategyVersion, strategy_version_id)
        if strategy is None or strategy.account_id != account_id:
            raise NotFoundError("账号策略版本不存在")
    else:
        strategy = session.scalar(
            select(AccountStrategyVersion).where(
                AccountStrategyVersion.account_id == account_id,
                AccountStrategyVersion.status == "active",
            )
        )
        if strategy is None:
            raise NotFoundError("账号没有生效策略")

    task = ContentTask(
        account_id=account_id,
        strategy_version_id=strategy.id,
        primary_goal=primary_goal,
        state=TaskState.PENDING_RESEARCH.value,
    )
    session.add(task)
    session.flush()

    workflow = WorkflowInstance(
        task_id=task.id,
        workflow_type="content_lifecycle",
        current_step="collect_research",
    )
    session.add(workflow)
    session.flush()
    session.add(
        WorkItem(
            workflow_id=workflow.id,
            task_id=task.id,
            step_type="collect_research",
            idempotency_key=f"task:{task.id}:collect_research:v1",
        )
    )

    result = {
        "task_id": task.id,
        "workflow_id": workflow.id,
        "state": task.state,
        "strategy_version_id": strategy.id,
    }
    _save_processed(
        session,
        command_id=command_id,
        command_type="CreateContentTask",
        request_digest=digest,
        resource_type="content_task",
        resource_id=task.id,
        result=result,
    )
    add_audit(
        session,
        actor_type="human",
        actor_id=actor_id,
        action="content_task.created",
        resource_type="content_task",
        resource_id=task.id,
        trace_id=command_id,
        after_state=task.state,
    )
    return result


def create_research_run(
    session: Session,
    *,
    command_id: str,
    account_id: str,
    strategy_version_id: Optional[str],
    window_start: datetime,
    window_end: datetime,
    source_scopes: list[str],
    seed_queries: list[str],
    max_items_per_source: int,
    actor_id: str,
) -> dict[str, Any]:
    if _aware(window_start) >= _aware(window_end):
        error = ConflictError("研究时间窗口必须有明确的开始和结束顺序")
        error.code = "RESEARCH_OUTPUT_INVALID"
        raise error
    request = {
        "account_id": account_id,
        "strategy_version_id": strategy_version_id,
        "window_start": _aware(window_start).isoformat(),
        "window_end": _aware(window_end).isoformat(),
        "source_scopes": source_scopes,
        "seed_queries": seed_queries,
        "max_items_per_source": max_items_per_source,
        "actor_id": actor_id,
    }
    digest = canonical_digest(request)
    previous = _processed_result(session, command_id=command_id, request_digest=digest)
    if previous is not None:
        return previous

    account = session.get(Account, account_id)
    if account is None:
        raise NotFoundError("账号不存在")
    if strategy_version_id:
        strategy = session.get(AccountStrategyVersion, strategy_version_id)
        if strategy is None or strategy.account_id != account_id:
            raise NotFoundError("账号策略版本不存在")
    else:
        strategy = session.scalar(
            select(AccountStrategyVersion).where(
                AccountStrategyVersion.account_id == account_id,
                AccountStrategyVersion.status == "active",
            )
        )
        if strategy is None:
            raise NotFoundError("账号没有生效策略")

    run = ResearchRun(
        account_id=account_id,
        strategy_version_id=strategy.id,
        window_start=_aware(window_start),
        window_end=_aware(window_end),
        source_scopes=sorted(set(source_scopes)),
        seed_queries=list(seed_queries),
        max_items_per_source=max_items_per_source,
        source_summary={},
    )
    session.add(run)
    session.flush()
    result = {"research_run_id": run.id, "status": run.status, "strategy_version_id": strategy.id}
    _save_processed(
        session,
        command_id=command_id,
        command_type="CreateResearchRun",
        request_digest=digest,
        resource_type="research_run",
        resource_id=run.id,
        result=result,
    )
    add_audit(
        session,
        actor_type="system",
        actor_id=actor_id,
        action="research_run.created",
        resource_type="research_run",
        resource_id=run.id,
        trace_id=command_id,
        after_state=run.status,
    )
    return result


def _refresh_research_run_status(session: Session, run: ResearchRun) -> None:
    sources = list(
        session.scalars(select(ResearchSource).where(ResearchSource.research_run_id == run.id))
    )
    represented_scopes = {source.source_type for source in sources}
    statuses = {source.status for source in sources}
    summary: dict[str, dict[str, int]] = {}
    for source in sources:
        item = summary.setdefault(
            source.source_type,
            {"executions": 0, "succeeded": 0, "failed": 0},
        )
        item["executions"] += 1
        if source.status == "succeeded":
            item["succeeded"] += 1
        elif source.status != "running":
            item["failed"] += 1
    run.source_summary = summary
    if not sources:
        run.status = "created"
        run.completed_at = None
    elif "running" in statuses or not set(run.source_scopes).issubset(represented_scopes):
        run.status = "running"
        run.completed_at = None
    else:
        run.status = (
            "completed_with_errors"
            if any(source.status != "succeeded" for source in sources)
            else "completed"
        )
        run.completed_at = utcnow()


def record_research_source(
    session: Session,
    *,
    command_id: str,
    research_run_id: str,
    source_type: str,
    query: str,
    status: str,
    started_at: Optional[datetime],
    completed_at: Optional[datetime],
    raw_result_count: int,
    valid_result_count: int,
    rate_limit: dict[str, Any],
    error_code: Optional[str],
    error_detail: Optional[str],
    actor_id: str,
) -> dict[str, Any]:
    if valid_result_count > raw_result_count:
        error = ConflictError("有效结果数量不能大于原始结果数量")
        error.code = "RESEARCH_OUTPUT_INVALID"
        raise error
    request = {
        "research_run_id": research_run_id,
        "source_type": source_type,
        "query": query,
        "status": status,
        "started_at": _aware(started_at).isoformat() if started_at else None,
        "completed_at": _aware(completed_at).isoformat() if completed_at else None,
        "raw_result_count": raw_result_count,
        "valid_result_count": valid_result_count,
        "rate_limit": rate_limit,
        "error_code": error_code,
        "error_detail": error_detail,
        "actor_id": actor_id,
    }
    digest = canonical_digest(request)
    previous = _processed_result(session, command_id=command_id, request_digest=digest)
    if previous is not None:
        return previous
    run = session.get(ResearchRun, research_run_id)
    if run is None:
        raise NotFoundError("研究运行不存在")
    if source_type not in run.source_scopes:
        error = ConflictError("来源类型不在研究运行的允许范围内")
        error.code = "RESEARCH_OUTPUT_INVALID"
        raise error
    source = ResearchSource(
        research_run_id=run.id,
        source_type=source_type,
        query=query,
        status=status,
        started_at=_aware(started_at) if started_at else utcnow(),
        completed_at=(
            _aware(completed_at) if completed_at else (utcnow() if status != "running" else None)
        ),
        raw_result_count=raw_result_count,
        valid_result_count=valid_result_count,
        rate_limit=rate_limit,
        error_code=error_code,
        error_detail=sanitize_research_error_detail(error_detail),
    )
    session.add(source)
    session.flush()
    _refresh_research_run_status(session, run)
    result = {"research_source_id": source.id, "research_run_id": run.id, "status": source.status}
    _save_processed(
        session,
        command_id=command_id,
        command_type="RecordResearchSource",
        request_digest=digest,
        resource_type="research_source",
        resource_id=source.id,
        result=result,
    )
    add_audit(
        session,
        actor_type="system",
        actor_id=actor_id,
        action="research_source.recorded",
        resource_type="research_source",
        resource_id=source.id,
        trace_id=command_id,
        after_state=source.status,
    )
    return result


def write_research_signal(
    session: Session,
    *,
    command_id: str,
    research_source_id: str,
    source_url: str,
    canonical_url: Optional[str],
    original_pointer: Optional[str],
    author_or_org: Optional[str],
    published_at: Optional[datetime],
    collected_at: Optional[datetime],
    title: str,
    summary: str,
    content_kind: str,
    freshness: str,
    credibility: str,
    audience_relevance: float,
    copyright_risk: str,
    verification_state: str,
    content_digest: Optional[str],
    actor_id: str,
) -> dict[str, Any]:
    normalized_url = normalize_canonical_url(canonical_url or source_url)
    computed_digest = research_content_digest(
        title=title, summary=summary, content_kind=content_kind
    )
    if content_digest and content_digest != computed_digest:
        error = ConflictError("内容摘要值必须由标准化内容确定性计算")
        error.code = "RESEARCH_OUTPUT_INVALID"
        raise error
    digest_value = computed_digest
    request = {
        "research_source_id": research_source_id,
        "source_url": source_url,
        "canonical_url": normalized_url,
        "original_pointer": original_pointer,
        "author_or_org": author_or_org,
        "published_at": _aware(published_at).isoformat() if published_at else None,
        "collected_at": _aware(collected_at).isoformat() if collected_at else None,
        "title": title,
        "summary": summary,
        "content_kind": content_kind,
        "freshness": freshness,
        "credibility": credibility,
        "audience_relevance": audience_relevance,
        "copyright_risk": copyright_risk,
        "verification_state": verification_state,
        "content_digest": digest_value,
        "actor_id": actor_id,
    }
    request_digest = canonical_digest(request)
    previous = _processed_result(session, command_id=command_id, request_digest=request_digest)
    if previous is not None:
        return previous
    source = session.get(ResearchSource, research_source_id)
    if source is None:
        raise NotFoundError("研究来源执行记录不存在")
    if source.source_type == "web_search" and verification_state == "primary_source_verified":
        error = ConflictError("搜索摘要不能标记为已核验的原始来源")
        error.code = "RESEARCH_OUTPUT_INVALID"
        raise error
    signal = session.scalar(
        select(ResearchSignal).where(
            ResearchSignal.research_run_id == source.research_run_id,
            or_(
                ResearchSignal.canonical_url == normalized_url,
                ResearchSignal.content_digest == digest_value,
            ),
        )
    )
    deduplicated = signal is not None
    if signal is None:
        signal = ResearchSignal(
            research_run_id=source.research_run_id,
            source_type=source.source_type,
            source_url=source_url,
            canonical_url=normalized_url,
            author_or_org=author_or_org,
            published_at=_aware(published_at) if published_at else None,
            collected_at=_aware(collected_at) if collected_at else utcnow(),
            title=title,
            summary=summary,
            content_kind=content_kind,
            freshness=freshness,
            credibility=credibility,
            audience_relevance=audience_relevance,
            copyright_risk=copyright_risk,
            verification_state=verification_state,
            content_digest=digest_value,
        )
        session.add(signal)
        session.flush()
    link = session.scalar(
        select(ResearchSignalSource).where(
            ResearchSignalSource.research_signal_id == signal.id,
            ResearchSignalSource.research_source_id == source.id,
        )
    )
    if link is None:
        session.add(
            ResearchSignalSource(
                research_signal_id=signal.id,
                research_source_id=source.id,
                original_source_url=source_url,
                original_pointer=original_pointer,
            )
        )
    result = {"research_signal_id": signal.id, "deduplicated": deduplicated}
    _save_processed(
        session,
        command_id=command_id,
        command_type="WriteResearchSignal",
        request_digest=request_digest,
        resource_type="research_signal",
        resource_id=signal.id,
        result=result,
    )
    add_audit(
        session,
        actor_type="system",
        actor_id=actor_id,
        action="research_signal.deduplicated" if deduplicated else "research_signal.recorded",
        resource_type="research_signal",
        resource_id=signal.id,
        trace_id=command_id,
    )
    return result


def create_evidence_package(
    session: Session,
    *,
    command_id: str,
    research_run_id: str,
    supported_question: str,
    supported_claim: Optional[str],
    key_signal_ids: list[str],
    counterexamples_and_limits: list[str],
    conclusion_strength: str,
    unresolved_conflicts: list[str],
    gaps: list[str],
    applicable_from: Optional[datetime],
    applicable_to: Optional[datetime],
    actor_id: str,
) -> dict[str, Any]:
    request = {
        "research_run_id": research_run_id,
        "supported_question": supported_question,
        "supported_claim": supported_claim,
        "key_signal_ids": sorted(set(key_signal_ids)),
        "counterexamples_and_limits": counterexamples_and_limits,
        "conclusion_strength": conclusion_strength,
        "unresolved_conflicts": unresolved_conflicts,
        "gaps": gaps,
        "applicable_from": _aware(applicable_from).isoformat() if applicable_from else None,
        "applicable_to": _aware(applicable_to).isoformat() if applicable_to else None,
        "actor_id": actor_id,
    }
    request_digest = canonical_digest(request)
    previous = _processed_result(session, command_id=command_id, request_digest=request_digest)
    if previous is not None:
        return previous
    run = session.get(ResearchRun, research_run_id)
    if run is None:
        raise NotFoundError("研究运行不存在")
    if applicable_from and applicable_to and _aware(applicable_from) > _aware(applicable_to):
        error = ConflictError("证据包适用时间范围无效")
        error.code = "RESEARCH_OUTPUT_INVALID"
        raise error
    unique_signal_ids = list(dict.fromkeys(key_signal_ids))
    signals = list(
        session.scalars(
            select(ResearchSignal).where(
                ResearchSignal.research_run_id == run.id,
                ResearchSignal.id.in_(unique_signal_ids),
            )
        )
    )
    if len(signals) != len(unique_signal_ids):
        raise NotFoundError("证据包包含不存在或不属于本研究运行的信号")
    unverified = [
        signal.id for signal in signals if signal.verification_state != "primary_source_verified"
    ]
    if unverified:
        error = ConflictError("关键证据必须来自已核验的原始来源")
        error.code = "EVIDENCE_UNVERIFIED"
        raise error
    links = list(
        session.scalars(
            select(ResearchSignalSource).where(
                ResearchSignalSource.research_signal_id.in_(unique_signal_ids)
            )
        )
    )
    links_by_signal: dict[str, list[ResearchSignalSource]] = {signal.id: [] for signal in signals}
    for link in links:
        links_by_signal[link.research_signal_id].append(link)
    key_evidence = [
        {
            "signal_id": signal.id,
            "title": signal.title,
            "canonical_url": signal.canonical_url,
            "verification_state": signal.verification_state,
            "source_execution_count": len(links_by_signal[signal.id]),
        }
        for signal in signals
    ]
    independent_source_ids = {link.research_source_id for link in links}
    source_independence = {
        "independent_source_execution_count": len(independent_source_ids),
        "per_signal_source_execution_counts": {
            signal.id: len(links_by_signal[signal.id]) for signal in signals
        },
    }
    package = EvidencePackage(
        research_run_id=run.id,
        supported_question=supported_question,
        supported_claim=supported_claim,
        key_evidence=key_evidence,
        counterexamples_and_limits=counterexamples_and_limits,
        source_independence=source_independence,
        conclusion_strength=conclusion_strength,
        unresolved_conflicts=unresolved_conflicts,
        gaps=gaps,
        applicable_from=_aware(applicable_from) if applicable_from else run.window_start,
        applicable_to=_aware(applicable_to) if applicable_to else run.window_end,
        publishable=not unresolved_conflicts,
    )
    session.add(package)
    session.flush()
    result = {"evidence_package_id": package.id, "publishable": package.publishable}
    _save_processed(
        session,
        command_id=command_id,
        command_type="CreateEvidencePackage",
        request_digest=request_digest,
        resource_type="evidence_package",
        resource_id=package.id,
        result=result,
    )
    add_audit(
        session,
        actor_type="system",
        actor_id=actor_id,
        action="evidence_package.created",
        resource_type="evidence_package",
        resource_id=package.id,
        trace_id=command_id,
        after_state="publishable" if package.publishable else "not_publishable",
    )
    return result


_TOPIC_SCORE_WEIGHTS = {
    "audience_relevance": 20,
    "problem_discovery_value": 15,
    "evidence_strength": 15,
    "spread_potential": 15,
    "distinctiveness": 10,
    "freshness": 10,
    "production_cost": 5,
    "risk": 10,
}


def _topic_total_score(scores: dict[str, int]) -> int:
    missing = set(_TOPIC_SCORE_WEIGHTS).difference(scores)
    if missing:
        error = ConflictError("选题评分缺少必要维度")
        error.code = "TOPIC_SCHEMA_INVALID"
        raise error
    if any(not isinstance(value, int) or not 0 <= value <= 100 for value in scores.values()):
        error = ConflictError("选题评分必须是零到一百的整数")
        error.code = "TOPIC_SCHEMA_INVALID"
        raise error
    return round(
        sum(scores[name] * weight for name, weight in _TOPIC_SCORE_WEIGHTS.items())
        / sum(_TOPIC_SCORE_WEIGHTS.values())
    )


def create_topic_proposal(
    session: Session,
    *,
    command_id: str,
    task_id: str,
    user_problem: str,
    working_title: str,
    core_claim: str,
    why_now: str,
    content_pillar: str,
    primary_goal: str,
    evidence_package_ids: list[str],
    counterpoints: list[str],
    experiment_hypothesis: str,
    primary_metric: str,
    recommended_format: str,
    scores: dict[str, int],
    score_explanation: str,
    risks: list[str],
    actor_id: str,
) -> dict[str, Any]:
    request = {
        "task_id": task_id,
        "user_problem": user_problem,
        "working_title": working_title,
        "core_claim": core_claim,
        "why_now": why_now,
        "content_pillar": content_pillar,
        "primary_goal": primary_goal,
        "evidence_package_ids": sorted(set(evidence_package_ids)),
        "counterpoints": counterpoints,
        "experiment_hypothesis": experiment_hypothesis,
        "primary_metric": primary_metric,
        "recommended_format": recommended_format,
        "scores": scores,
        "score_explanation": score_explanation,
        "risks": risks,
        "actor_id": actor_id,
    }
    digest = canonical_digest(request)
    previous = _processed_result(session, command_id=command_id, request_digest=digest)
    if previous is not None:
        return previous
    task = session.get(ContentTask, task_id)
    if task is None:
        raise NotFoundError("内容任务不存在")
    if TaskState(task.state) != TaskState.RESEARCHING:
        error = ConflictError("只有研究中的任务可以创建选题")
        error.code = "STATE_CONFLICT"
        raise error
    package_ids = list(dict.fromkeys(evidence_package_ids))
    packages = list(
        session.scalars(select(EvidencePackage).where(EvidencePackage.id.in_(package_ids)))
    )
    if len(packages) != len(package_ids) or any(not package.publishable for package in packages):
        error = ConflictError("选题必须引用可用证据包")
        error.code = "NO_USABLE_EVIDENCE"
        raise error
    total_score = _topic_total_score(scores)
    if scores["evidence_strength"] < 50:
        error = ConflictError("证据充分度低于五十分，不能创建主选题")
        error.code = "NO_USABLE_EVIDENCE"
        raise error
    version = (
        session.scalar(
            select(TopicProposal.version)
            .where(TopicProposal.task_id == task.id)
            .order_by(TopicProposal.version.desc())
            .limit(1)
        )
        or 0
    ) + 1
    proposal = TopicProposal(
        task_id=task.id,
        version=version,
        user_problem=user_problem,
        working_title=working_title,
        core_claim=core_claim,
        why_now=why_now,
        content_pillar=content_pillar,
        primary_goal=primary_goal,
        evidence_package_ids=package_ids,
        counterpoints=counterpoints,
        experiment_hypothesis=experiment_hypothesis,
        primary_metric=primary_metric,
        recommended_format=recommended_format,
        scores=scores,
        total_score=total_score,
        score_explanation=score_explanation,
        risks=risks,
    )
    session.add(proposal)
    session.flush()
    approval = mark_topic_ready(
        session,
        task_id=task.id,
        topic_version_id=proposal.id,
        actor_id=actor_id,
        trace_id=command_id,
    )
    result = {
        "topic_proposal_id": proposal.id,
        "approval_id": approval.id,
        "total_score": proposal.total_score,
        "status": proposal.status,
    }
    _save_processed(
        session,
        command_id=command_id,
        command_type="CreateTopicProposal",
        request_digest=digest,
        resource_type="topic_proposal",
        resource_id=proposal.id,
        result=result,
    )
    add_audit(
        session,
        actor_type="human",
        actor_id=actor_id,
        action="topic_proposal.created",
        resource_type="topic_proposal",
        resource_id=proposal.id,
        trace_id=command_id,
        after_state=proposal.status,
    )
    return result


def _validate_asset_paths(paths: list[str]) -> None:
    if any(path.startswith("/") or ".." in path.split("/") for path in paths):
        error = ConflictError("素材路径必须是 data/assets 下的相对路径")
        error.code = "ASSET_WRITE_FAILED"
        raise error


def create_content_version(
    session: Session,
    *,
    command_id: str,
    task_id: str,
    title_candidates: list[str],
    selected_title: str,
    body: str,
    interaction_question: str,
    topics: list[str],
    cover_script: dict[str, Any],
    slide_scripts: list[dict[str, Any]],
    asset_paths: list[str],
    source_notes: list[dict[str, Any]],
    risk_notes: list[str],
    generation_manifest: dict[str, Any],
    actor_id: str,
) -> dict[str, Any]:
    request = {
        "task_id": task_id,
        "title_candidates": title_candidates,
        "selected_title": selected_title,
        "body": body,
        "interaction_question": interaction_question,
        "topics": topics,
        "cover_script": cover_script,
        "slide_scripts": slide_scripts,
        "asset_paths": asset_paths,
        "source_notes": source_notes,
        "risk_notes": risk_notes,
        "generation_manifest": generation_manifest,
        "actor_id": actor_id,
    }
    digest = canonical_digest(request)
    previous = _processed_result(session, command_id=command_id, request_digest=digest)
    if previous is not None:
        return previous
    task = session.get(ContentTask, task_id)
    if task is None:
        raise NotFoundError("内容任务不存在")
    if TaskState(task.state) != TaskState.PRODUCING or not task.current_topic_version_id:
        error = ConflictError("只有选题已批准的制作中任务可以创建内容版本")
        error.code = "APPROVED_TOPIC_MISSING"
        raise error
    if selected_title not in title_candidates or len(selected_title) > 20:
        error = ConflictError("选中标题必须来自候选且不超过二十个字符")
        error.code = "CONTENT_SCHEMA_INVALID"
        raise error
    if "headline" not in cover_script or not slide_scripts:
        error = ConflictError("内容版本必须先提供封面和分页视觉脚本")
        error.code = "CONTENT_SCHEMA_INVALID"
        raise error
    _validate_asset_paths(asset_paths)
    version = (
        session.scalar(
            select(ContentVersion.version)
            .where(ContentVersion.task_id == task.id)
            .order_by(ContentVersion.version.desc())
            .limit(1)
        )
        or 0
    ) + 1
    content = ContentVersion(
        task_id=task.id,
        topic_version_id=task.current_topic_version_id,
        strategy_version_id=task.strategy_version_id,
        version=version,
        title_candidates=title_candidates,
        selected_title=selected_title,
        body=body,
        interaction_question=interaction_question,
        topics=topics,
        cover_script=cover_script,
        slide_scripts=slide_scripts,
        asset_paths=asset_paths,
        source_notes=source_notes,
        risk_notes=risk_notes,
        generation_manifest=generation_manifest,
    )
    session.add(content)
    session.flush()
    approval = mark_content_ready(
        session,
        task_id=task.id,
        content_version_id=content.id,
        actor_id=actor_id,
        trace_id=command_id,
    )
    result = {
        "content_version_id": content.id,
        "approval_id": approval.id,
        "status": content.status,
    }
    _save_processed(
        session,
        command_id=command_id,
        command_type="CreateContentVersion",
        request_digest=digest,
        resource_type="content_version",
        resource_id=content.id,
        result=result,
    )
    add_audit(
        session,
        actor_type="human",
        actor_id=actor_id,
        action="content_version.created",
        resource_type="content_version",
        resource_id=content.id,
        trace_id=command_id,
        after_state=content.status,
    )
    return result


def create_publication_plan(
    session: Session,
    *,
    command_id: str,
    task_id: str,
    approval_id: str,
    scheduled_at: datetime,
    allowed_from: datetime,
    allowed_until: datetime,
    actor_id: str,
) -> dict[str, Any]:
    if _aware(allowed_from) > _aware(scheduled_at) or _aware(scheduled_at) > _aware(allowed_until):
        error = ConflictError("排期必须位于允许发布窗口内")
        error.code = "PUBLISH_WINDOW_MISSED"
        raise error
    request = {
        "task_id": task_id,
        "approval_id": approval_id,
        "scheduled_at": _aware(scheduled_at).isoformat(),
        "allowed_from": _aware(allowed_from).isoformat(),
        "allowed_until": _aware(allowed_until).isoformat(),
        "actor_id": actor_id,
    }
    digest = canonical_digest(request)
    previous = _processed_result(session, command_id=command_id, request_digest=digest)
    if previous is not None:
        return previous
    task = session.get(ContentTask, task_id)
    approval = session.get(ApprovalRequest, approval_id)
    if task is None or approval is None:
        raise NotFoundError("内容任务或发布审批不存在")
    if (
        TaskState(task.state) != TaskState.SCHEDULED
        or approval.approval_type != ApprovalType.PUBLISH.value
        or approval.status != ApprovalStatus.APPROVED.value
        or approval.resource_id != task.id
        or approval.resource_version != task.current_content_version_id
    ):
        error = ConflictError("发布审批或内容版本无效")
        error.code = "PUBLISH_APPROVAL_INVALID"
        raise error
    idempotency_key = (
        f"xiaohongshu:{task.account_id}:{task.id}:{task.current_content_version_id}:{approval.id}"
    )
    plan = PublicationPlan(
        task_id=task.id,
        account_id=task.account_id,
        content_version_id=task.current_content_version_id,
        approval_id=approval.id,
        scheduled_at=_aware(scheduled_at),
        allowed_from=_aware(allowed_from),
        allowed_until=_aware(allowed_until),
        idempotency_key=idempotency_key,
    )
    session.add(plan)
    session.flush()
    result = {"publication_plan_id": plan.id, "status": plan.status}
    _save_processed(
        session,
        command_id=command_id,
        command_type="CreatePublicationPlan",
        request_digest=digest,
        resource_type="publication_plan",
        resource_id=plan.id,
        result=result,
    )
    add_audit(
        session,
        actor_type="human",
        actor_id=actor_id,
        action="publication_plan.created",
        resource_type="publication_plan",
        resource_id=plan.id,
        trace_id=command_id,
        after_state=plan.status,
    )
    return result


def transition_task(
    session: Session,
    *,
    task_id: str,
    target: TaskState,
    actor_type: str,
    actor_id: str,
    trace_id: str,
    reason: str = "",
    expected_row_version: Optional[int] = None,
) -> ContentTask:
    task = session.get(ContentTask, task_id)
    if task is None:
        raise NotFoundError("内容任务不存在")
    current = TaskState(task.state)
    assert_transition(current, target)
    row_version = task.row_version
    if expected_row_version is not None and expected_row_version != row_version:
        error = ConflictError("内容任务已被其他操作更新")
        error.code = "ROW_VERSION_CONFLICT"
        raise error

    result = session.execute(
        update(ContentTask)
        .where(ContentTask.id == task_id, ContentTask.row_version == row_version)
        .values(
            state=target.value,
            row_version=row_version + 1,
            updated_at=utcnow(),
        )
    )
    if result.rowcount != 1:
        error = ConflictError("内容任务已被其他操作更新")
        error.code = "ROW_VERSION_CONFLICT"
        raise error
    session.flush()
    session.refresh(task)
    add_audit(
        session,
        actor_type=actor_type,
        actor_id=actor_id,
        action="content_task.transitioned",
        resource_type="content_task",
        resource_id=task.id,
        trace_id=trace_id,
        reason=reason,
        before_state=current.value,
        after_state=target.value,
    )
    return task


def _create_approval(
    session: Session,
    *,
    approval_type: ApprovalType,
    task: ContentTask,
    resource_version: str,
    expires_in_hours: int = 24,
) -> ApprovalRequest:
    existing = session.scalar(
        select(ApprovalRequest).where(
            ApprovalRequest.approval_type == approval_type.value,
            ApprovalRequest.resource_type == "content_task",
            ApprovalRequest.resource_id == task.id,
            ApprovalRequest.resource_version == resource_version,
            ApprovalRequest.status == ApprovalStatus.PENDING.value,
        )
    )
    if existing is not None:
        return existing
    approval = ApprovalRequest(
        approval_type=approval_type.value,
        resource_type="content_task",
        resource_id=task.id,
        resource_version=resource_version,
        expires_at=utcnow() + timedelta(hours=expires_in_hours),
    )
    session.add(approval)
    session.flush()
    return approval


def mark_topic_ready(
    session: Session,
    *,
    task_id: str,
    topic_version_id: str,
    actor_id: str = "workflow",
    trace_id: Optional[str] = None,
) -> ApprovalRequest:
    task = session.get(ContentTask, task_id)
    if task is None:
        raise NotFoundError("内容任务不存在")
    task.current_topic_version_id = topic_version_id
    transition_task(
        session,
        task_id=task_id,
        target=TaskState.PENDING_TOPIC_APPROVAL,
        actor_type="system",
        actor_id=actor_id,
        trace_id=trace_id or new_id(),
        reason="研究和选题已准备完成",
    )
    return _create_approval(
        session,
        approval_type=ApprovalType.TOPIC,
        task=task,
        resource_version=topic_version_id,
    )


def mark_content_ready(
    session: Session,
    *,
    task_id: str,
    content_version_id: str,
    actor_id: str = "workflow",
    trace_id: Optional[str] = None,
) -> ApprovalRequest:
    task = session.get(ContentTask, task_id)
    if task is None:
        raise NotFoundError("内容任务不存在")
    task.current_content_version_id = content_version_id
    target = TaskState(task.state)
    if target == TaskState.PRODUCING:
        transition_task(
            session,
            task_id=task_id,
            target=TaskState.QUALITY_CHECKING,
            actor_type="system",
            actor_id=actor_id,
            trace_id=trace_id or new_id(),
            reason="内容已生成，进入自动检查",
        )
    transition_task(
        session,
        task_id=task_id,
        target=TaskState.PENDING_PUBLISH_APPROVAL,
        actor_type="system",
        actor_id=actor_id,
        trace_id=trace_id or new_id(),
        reason="自动检查通过",
    )
    return _create_approval(
        session,
        approval_type=ApprovalType.PUBLISH,
        task=task,
        resource_version=content_version_id,
    )


def invalidate_publish_approvals(
    session: Session,
    *,
    task_id: str,
    actor_id: str,
    reason: str,
    trace_id: str,
) -> int:
    approvals = list(
        session.scalars(
            select(ApprovalRequest).where(
                ApprovalRequest.approval_type == ApprovalType.PUBLISH.value,
                ApprovalRequest.resource_id == task_id,
                ApprovalRequest.status == ApprovalStatus.PENDING.value,
            )
        )
    )
    for approval in approvals:
        approval.status = ApprovalStatus.INVALIDATED.value
        add_audit(
            session,
            actor_type="system",
            actor_id=actor_id,
            action="approval.invalidated",
            resource_type="approval_request",
            resource_id=approval.id,
            trace_id=trace_id,
            reason=reason,
            before_state=ApprovalStatus.PENDING.value,
            after_state=ApprovalStatus.INVALIDATED.value,
        )
    return len(approvals)


def decide_approval(
    session: Session,
    *,
    approval_id: str,
    decision: ApprovalDecision,
    operator_id: str,
    event_id: str,
    comment: str = "",
    scheduled_at: Optional[datetime] = None,
) -> dict[str, Any]:
    duplicate = session.scalar(
        select(ApprovalDecisionRecord).where(ApprovalDecisionRecord.event_id == event_id)
    )
    if duplicate is not None:
        approval = session.get(ApprovalRequest, approval_id)
        return {
            "approval_id": approval_id,
            "status": approval.status if approval else "unknown",
            "duplicate": True,
        }

    approval = session.get(ApprovalRequest, approval_id)
    if approval is None:
        raise NotFoundError("审批请求不存在")
    if approval.status != ApprovalStatus.PENDING.value:
        error = ConflictError("审批已经处理或失效")
        error.code = "APPROVAL_ALREADY_DECIDED"
        raise error
    if _aware(approval.expires_at) <= utcnow():
        approval.status = ApprovalStatus.EXPIRED.value
        error = ConflictError("审批已经过期", action="请创建新的审批")
        error.code = "APPROVAL_EXPIRED"
        raise error
    if approval.resource_type != "content_task":
        raise ConflictError("暂不支持该审批资源")

    task = session.get(ContentTask, approval.resource_id)
    if task is None:
        raise NotFoundError("审批关联的内容任务不存在")
    approval_type = ApprovalType(approval.approval_type)
    current_version = (
        task.current_topic_version_id
        if approval_type == ApprovalType.TOPIC
        else task.current_content_version_id
    )
    if current_version != approval.resource_version:
        approval.status = ApprovalStatus.INVALIDATED.value
        error = ConflictError("审批绑定的版本已经失效", action="请打开最新审批卡片")
        error.code = "APPROVAL_VERSION_MISMATCH"
        raise error

    status_map = {
        ApprovalDecision.APPROVE: ApprovalStatus.APPROVED,
        ApprovalDecision.REJECT: ApprovalStatus.REJECTED,
        ApprovalDecision.REQUEST_CHANGES: ApprovalStatus.CHANGES_REQUESTED,
        ApprovalDecision.DELAY: ApprovalStatus.DELAYED,
    }
    approval.status = status_map[decision].value
    record = ApprovalDecisionRecord(
        approval_request_id=approval.id,
        operator_id=operator_id,
        decision=decision.value,
        comment=comment,
        event_id=event_id,
    )
    session.add(record)

    if decision == ApprovalDecision.APPROVE:
        if approval_type == ApprovalType.TOPIC:
            task = transition_task(
                session,
                task_id=task.id,
                target=TaskState.PRODUCING,
                actor_type="human",
                actor_id=operator_id,
                trace_id=event_id,
                reason=comment or "选题已批准",
            )
            workflow = session.scalar(
                select(WorkflowInstance).where(
                    WorkflowInstance.task_id == task.id,
                    WorkflowInstance.status == "running",
                )
            )
            if workflow is None:
                raise NotFoundError("任务工作流不存在")
            session.add(
                WorkItem(
                    workflow_id=workflow.id,
                    task_id=task.id,
                    step_type="produce_content",
                    idempotency_key=(
                        f"task:{task.id}:produce_content:{task.current_topic_version_id}"
                    ),
                )
            )
        else:
            effective_schedule = scheduled_at or task.scheduled_at
            if effective_schedule is None:
                error = ConflictError("发布审批缺少排期时间")
                error.code = "SCHEDULE_REQUIRED"
                raise error
            task.scheduled_at = effective_schedule
            task = transition_task(
                session,
                task_id=task.id,
                target=TaskState.SCHEDULED,
                actor_type="human",
                actor_id=operator_id,
                trace_id=event_id,
                reason=comment or "发布内容已批准",
            )
            workflow = session.scalar(
                select(WorkflowInstance).where(
                    WorkflowInstance.task_id == task.id,
                    WorkflowInstance.status == "running",
                )
            )
            if workflow is None:
                raise NotFoundError("任务工作流不存在")
            session.add(
                WorkItem(
                    workflow_id=workflow.id,
                    task_id=task.id,
                    step_type="prepare_publication",
                    available_at=effective_schedule,
                    idempotency_key=(
                        f"task:{task.id}:prepare_publication:{task.current_content_version_id}"
                    ),
                )
            )
    elif decision == ApprovalDecision.REJECT:
        transition_task(
            session,
            task_id=task.id,
            target=TaskState.CANCELLED,
            actor_type="human",
            actor_id=operator_id,
            trace_id=event_id,
            reason=comment or "审批已驳回",
        )
    elif decision == ApprovalDecision.REQUEST_CHANGES:
        target = (
            TaskState.RESEARCHING if approval_type == ApprovalType.TOPIC else TaskState.PRODUCING
        )
        transition_task(
            session,
            task_id=task.id,
            target=target,
            actor_type="human",
            actor_id=operator_id,
            trace_id=event_id,
            reason=comment or "审批要求修改",
        )

    add_audit(
        session,
        actor_type="human",
        actor_id=operator_id,
        action="approval.decided",
        resource_type="approval_request",
        resource_id=approval.id,
        trace_id=event_id,
        reason=comment,
        before_state=ApprovalStatus.PENDING.value,
        after_state=approval.status,
    )
    return {
        "approval_id": approval.id,
        "status": approval.status,
        "task_id": task.id,
        "task_state": task.state,
        "duplicate": False,
    }


def set_pause(
    session: Session,
    *,
    scope: AutomationScope,
    reason: str,
    actor_id: str,
    requires_manual_resume: bool = False,
    trace_id: Optional[str] = None,
) -> SystemPause:
    pause = session.get(SystemPause, scope.value)
    before = "inactive"
    if pause is None:
        pause = SystemPause(
            scope=scope.value,
            active=True,
            reason=reason,
            created_by=actor_id,
            requires_manual_resume=requires_manual_resume,
        )
        session.add(pause)
    else:
        before = "active" if pause.active else "inactive"
        pause.active = True
        pause.reason = reason
        pause.created_by = actor_id
        pause.created_at = utcnow()
        pause.requires_manual_resume = requires_manual_resume
        pause.resumed_by = None
        pause.resumed_at = None
    add_audit(
        session,
        actor_type="human",
        actor_id=actor_id,
        action="automation.paused",
        resource_type="automation_scope",
        resource_id=scope.value,
        trace_id=trace_id or new_id(),
        reason=reason,
        before_state=before,
        after_state="active",
    )
    return pause


def resume_pause(
    session: Session,
    *,
    scope: AutomationScope,
    reason: str,
    actor_id: str,
    trace_id: Optional[str] = None,
) -> SystemPause:
    pause = session.get(SystemPause, scope.value)
    if pause is None or not pause.active:
        error = ConflictError("该自动化范围当前没有暂停")
        error.code = "AUTOMATION_NOT_PAUSED"
        raise error
    pause.active = False
    pause.resumed_by = actor_id
    pause.resumed_at = utcnow()
    add_audit(
        session,
        actor_type="human",
        actor_id=actor_id,
        action="automation.resumed",
        resource_type="automation_scope",
        resource_id=scope.value,
        trace_id=trace_id or new_id(),
        reason=reason,
        before_state="active",
        after_state="inactive",
    )
    return pause


def require_authorized_operator(operator_id: str, allowed_user_ids: list[str]) -> None:
    if not allowed_user_ids or operator_id not in allowed_user_ids:
        raise UnauthorizedError("当前飞书用户没有审批权限")
