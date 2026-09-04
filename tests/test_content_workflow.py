from datetime import timedelta

from xhs_manager.domain import ApprovalDecision, TaskState, utcnow
from xhs_manager.models import EvidencePackage
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


def test_evidence_to_content_to_publication_plan(session):
    account = create_account(
        session,
        command_id="workflow-account",
        name="测试账号",
        timezone_name="Asia/Shanghai",
        strategy_config={"persona": "AI 工作流实验员"},
        actor_id="operator-1",
    )
    task = create_content_task(
        session,
        command_id="workflow-task",
        account_id=account["account_id"],
        primary_goal="content_validation",
        actor_id="operator-1",
    )
    transition_task(
        session,
        task_id=task["task_id"],
        target=TaskState.RESEARCHING,
        actor_type="system",
        actor_id="worker-1",
        trace_id="workflow-research",
    )
    now = utcnow()
    run = create_research_run(
        session,
        command_id="workflow-run",
        account_id=account["account_id"],
        strategy_version_id=account["strategy_version_id"],
        window_start=now,
        window_end=now + timedelta(days=1),
        source_scopes=["github"],
        seed_queries=["AI 工作流"],
        max_items_per_source=10,
        actor_id="worker-1",
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
        command_id="workflow-topic",
        task_id=task["task_id"],
        user_problem="不会把对话变成可重复流程",
        working_title="把聊天变成流程",
        core_claim="先固定输入和检查点，再谈自动化。",
        why_now="团队开始使用智能体。",
        content_pillar="workflow_experiment",
        primary_goal="content_validation",
        evidence_package_ids=[evidence.id],
        counterpoints=["复杂任务仍需人工复核。"],
        experiment_hypothesis="清单能减少返工。",
        primary_metric="effective_questions",
        recommended_format="carousel",
        scores={
            "audience_relevance": 80,
            "problem_discovery_value": 80,
            "evidence_strength": 80,
            "spread_potential": 70,
            "distinctiveness": 70,
            "freshness": 70,
            "production_cost": 80,
            "risk": 80,
        },
        score_explanation="证据与用户问题匹配。",
        risks=[],
        actor_id="operator-1",
    )
    decide_approval(
        session,
        approval_id=topic["approval_id"],
        decision=ApprovalDecision.APPROVE,
        operator_id="operator-1",
        event_id="workflow-topic-approved",
    )
    content = create_content_version(
        session,
        command_id="workflow-content",
        task_id=task["task_id"],
        title_candidates=["把聊天变成流程", "AI 工作流别急着自动化"],
        selected_title="把聊天变成流程",
        body="先固定输入和检查点，再谈自动化。复杂任务仍需人工复核。",
        interaction_question="你最想先固定哪一步？",
        topics=["AI工作流"],
        cover_script={"headline": "把聊天变成流程"},
        slide_scripts=[{"page": 1, "goal": "结论"}],
        asset_paths=[],
        source_notes=[],
        risk_notes=["复杂任务需人工复核。"],
        generation_manifest={"skill": "xhs-writer", "version": "local"},
        actor_id="operator-1",
    )
    scheduled = utcnow() + timedelta(hours=1)
    decide_approval(
        session,
        approval_id=content["approval_id"],
        decision=ApprovalDecision.APPROVE,
        operator_id="operator-1",
        event_id="workflow-content-approved",
        scheduled_at=scheduled,
    )
    plan = create_publication_plan(
        session,
        command_id="workflow-plan",
        task_id=task["task_id"],
        approval_id=content["approval_id"],
        scheduled_at=scheduled,
        allowed_from=scheduled - timedelta(minutes=15),
        allowed_until=scheduled + timedelta(minutes=30),
        actor_id="operator-1",
    )
    assert plan["status"] == "scheduled"
