from datetime import timedelta
from pathlib import Path

from xhs_manager.domain import ApprovalDecision, TaskState, utcnow
from xhs_manager.models import EvidencePackage
from xhs_manager.publishing import CommandResult, OpenCliXhsClient, prepare_xiaohongshu_draft
from xhs_manager.services import (
    create_account,
    create_content_task,
    create_content_version,
    create_publication_plan,
    create_research_run,
    create_topic_proposal,
    decide_approval,
    transition_task,
)


def test_prepare_draft_is_idempotent_and_verifies_platform_draft(session, tmp_path: Path):
    account = create_account(
        session,
        command_id="account",
        name="账号",
        timezone_name="Asia/Shanghai",
        strategy_config={},
        actor_id="operator",
    )
    task = create_content_task(
        session,
        command_id="task",
        account_id=account["account_id"],
        primary_goal="content_validation",
        actor_id="operator",
    )
    transition_task(
        session,
        task_id=task["task_id"],
        target=TaskState.RESEARCHING,
        actor_type="system",
        actor_id="operator",
        trace_id="research-transition",
    )
    now = utcnow()
    run = create_research_run(
        session,
        command_id="research",
        account_id=account["account_id"],
        strategy_version_id=account["strategy_version_id"],
        window_start=now,
        window_end=now + timedelta(days=1),
        source_scopes=["github"],
        seed_queries=["AI 工作流"],
        max_items_per_source=1,
        actor_id="operator",
    )
    evidence = EvidencePackage(
        research_run_id=run["research_run_id"],
        supported_question="如何复现工作流？",
        key_evidence=[],
        source_independence={},
        conclusion_strength="moderate",
        publishable=True,
    )
    session.add(evidence)
    session.flush()
    topic = create_topic_proposal(
        session,
        command_id="topic",
        task_id=task["task_id"],
        user_problem="问题",
        working_title="标题",
        core_claim="主张",
        why_now="现在",
        content_pillar="workflow_experiment",
        primary_goal="content_validation",
        evidence_package_ids=[evidence.id],
        counterpoints=[],
        experiment_hypothesis="假设",
        primary_metric="metric",
        recommended_format="carousel",
        scores={
            "audience_relevance": 80,
            "problem_discovery_value": 80,
            "evidence_strength": 80,
            "spread_potential": 80,
            "distinctiveness": 80,
            "freshness": 80,
            "production_cost": 80,
            "risk": 80,
        },
        score_explanation="说明",
        risks=[],
        actor_id="operator",
    )
    decide_approval(
        session,
        approval_id=topic["approval_id"],
        decision=ApprovalDecision.APPROVE,
        operator_id="operator",
        event_id="topic-approved",
    )
    asset = tmp_path / "card.png"
    asset.write_bytes(b"png")
    content = create_content_version(
        session,
        command_id="content",
        task_id=task["task_id"],
        title_candidates=["标题", "备选"],
        selected_title="标题",
        body="正文",
        interaction_question="问题？",
        topics=["AI工作流"],
        cover_script={"headline": "标题"},
        slide_scripts=[{"page": 1}],
        asset_paths=["card.png"],
        source_notes=[],
        risk_notes=[],
        generation_manifest={},
        actor_id="operator",
    )
    scheduled = now + timedelta(hours=1)
    decide_approval(
        session,
        approval_id=content["approval_id"],
        decision=ApprovalDecision.APPROVE,
        operator_id="operator",
        event_id="content-approved",
        scheduled_at=scheduled,
    )
    plan = create_publication_plan(
        session,
        command_id="plan",
        task_id=task["task_id"],
        approval_id=content["approval_id"],
        scheduled_at=scheduled,
        allowed_from=scheduled,
        allowed_until=scheduled + timedelta(hours=1),
        actor_id="operator",
    )
    calls: list[list[str]] = []

    def runner(args: list[str]) -> CommandResult:
        calls.append(args)
        if "whoami" in args:
            return CommandResult(0, '{"logged_in":true,"username":"测试号"}')
        if "drafts" in args:
            drafts_calls = sum("drafts" in item for item in calls)
            if drafts_calls == 1:
                return CommandResult(0, "[]")
            return CommandResult(0, '[{"id":"draft-1","title":"标题","images":1}]')
        return CommandResult(1, "", "topic unavailable")

    client = OpenCliXhsClient(runner)
    first = prepare_xiaohongshu_draft(
        session,
        publication_plan_id=plan["publication_plan_id"],
        actor_id="operator",
        project_root=tmp_path,
        client=client,
    )
    second = prepare_xiaohongshu_draft(
        session,
        publication_plan_id=plan["publication_plan_id"],
        actor_id="operator",
        project_root=tmp_path,
        client=client,
    )
    assert first["status"] == "succeeded"
    assert first["draft_id"] == "draft-1"
    assert second == first
    assert len(calls) == 4
