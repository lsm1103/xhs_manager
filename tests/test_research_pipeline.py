from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from xhs_manager.models import (
    EvidencePackage,
    ResearchRun,
    ResearchSignal,
    ResearchSignalSource,
    ResearchSource,
)


def _create_account(client, auth_headers):
    response = client.post(
        "/v1/accounts",
        headers=auth_headers,
        json={
            "command_id": "research-account",
            "name": "研究测试账号",
            "strategy_config": {"persona": "AI 工作流实验员"},
            "actor_id": "operator-1",
        },
    )
    assert response.status_code == 200
    return response.json()


def _create_run(client, auth_headers):
    account = _create_account(client, auth_headers)
    now = datetime.now(timezone.utc)
    response = client.post(
        "/v1/research-runs",
        headers=auth_headers,
        json={
            "command_id": "research-run",
            "account_id": account["account_id"],
            "window_start": now.isoformat(),
            "window_end": (now + timedelta(days=1)).isoformat(),
            "source_scopes": ["web_search", "github", "xiaohongshu"],
            "seed_queries": ["AI 工作流"],
            "max_items_per_source": 30,
            "actor_id": "research-worker",
        },
    )
    assert response.status_code == 200
    return response.json()["research_run_id"]


def _record_source(client, auth_headers, run_id, command_id, source_type, status="succeeded"):
    response = client.post(
        f"/v1/research-runs/{run_id}/sources",
        headers=auth_headers,
        json={
            "command_id": command_id,
            "source_type": source_type,
            "query": "AI 工作流",
            "status": status,
            "raw_result_count": 2 if status == "succeeded" else 0,
            "valid_result_count": 1 if status == "succeeded" else 0,
            "error_code": "SOURCE_UNAVAILABLE" if status == "failed" else None,
            "actor_id": "research-worker",
        },
    )
    assert response.status_code == 200
    return response.json()["research_source_id"]


def _write_signal(
    client,
    auth_headers,
    source_id,
    command_id,
    *,
    source_url,
    verification_state="primary_source_verified",
):
    response = client.post(
        f"/v1/research-sources/{source_id}/signals",
        headers=auth_headers,
        json={
            "command_id": command_id,
            "research_source_id": source_id,
            "source_url": source_url,
            "title": "官方工作流文档",
            "summary": "该官方页面描述了可复现的工作流配置。",
            "content_kind": "fact",
            "freshness": "recent",
            "credibility": "high",
            "audience_relevance": 0.9,
            "copyright_risk": "low",
            "verification_state": verification_state,
            "actor_id": "research-worker",
        },
    )
    return response


def test_research_api_deduplicates_content_but_preserves_independent_sources(client, auth_headers):
    run_id = _create_run(client, auth_headers)
    github_source_id = _record_source(
        client, auth_headers, run_id, "research-source-github", "github"
    )
    web_source_id = _record_source(
        client, auth_headers, run_id, "research-source-web", "web_search"
    )
    _record_source(client, auth_headers, run_id, "research-source-xhs", "xiaohongshu", "failed")

    first = _write_signal(
        client,
        auth_headers,
        github_source_id,
        "research-signal-github",
        source_url="HTTPS://Example.com/docs/workflow#section",
    )
    second = _write_signal(
        client,
        auth_headers,
        web_source_id,
        "research-signal-web",
        source_url="https://other.example.org/mirror/workflow",
        verification_state="secondary_only",
    )
    assert first.status_code == 200
    assert first.json()["deduplicated"] is False
    assert second.status_code == 200
    assert second.json()["deduplicated"] is True
    assert first.json()["research_signal_id"] == second.json()["research_signal_id"]

    factory = client.app.state.session_factory
    with factory() as session:
        signals = list(session.scalars(select(ResearchSignal)))
        links = list(session.scalars(select(ResearchSignalSource)))
        run = session.get(ResearchRun, run_id)
        assert len(signals) == 1
        assert signals[0].canonical_url == "https://example.com/docs/workflow"
        assert len(links) == 2
        assert {link.research_source_id for link in links} == {github_source_id, web_source_id}
        assert run.status == "completed_with_errors"


def test_evidence_package_rejects_unverified_key_signal(client, auth_headers):
    run_id = _create_run(client, auth_headers)
    source_id = _record_source(client, auth_headers, run_id, "research-source-unverified", "github")
    signal = _write_signal(
        client,
        auth_headers,
        source_id,
        "research-signal-unverified",
        source_url="https://example.com/unverified",
        verification_state="secondary_only",
    )
    signal_id = signal.json()["research_signal_id"]

    response = client.post(
        f"/v1/research-runs/{run_id}/evidence-packages",
        headers=auth_headers,
        json={
            "command_id": "evidence-unverified",
            "supported_question": "这个工作流能解决什么问题？",
            "supported_claim": "它可复现。",
            "key_signal_ids": [signal_id],
            "actor_id": "research-worker",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EVIDENCE_UNVERIFIED"


def test_search_summary_cannot_be_recorded_as_primary_verified(client, auth_headers):
    run_id = _create_run(client, auth_headers)
    source_id = _record_source(client, auth_headers, run_id, "research-source-search", "web_search")
    response = _write_signal(
        client,
        auth_headers,
        source_id,
        "research-signal-search",
        source_url="https://example.com/search-result",
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RESEARCH_OUTPUT_INVALID"


def test_evidence_package_uses_verified_signal_and_records_independence(client, auth_headers):
    run_id = _create_run(client, auth_headers)
    source_id = _record_source(client, auth_headers, run_id, "research-source-verified", "github")
    signal = _write_signal(
        client,
        auth_headers,
        source_id,
        "research-signal-verified",
        source_url="https://example.com/verified",
    )
    signal_id = signal.json()["research_signal_id"]

    response = client.post(
        f"/v1/research-runs/{run_id}/evidence-packages",
        headers=auth_headers,
        json={
            "command_id": "evidence-verified",
            "supported_question": "这个工作流能解决什么问题？",
            "supported_claim": "官方文档说明它可复现。",
            "key_signal_ids": [signal_id],
            "counterexamples_and_limits": ["仅依据当前版本文档。"],
            "conclusion_strength": "moderate",
            "actor_id": "research-worker",
        },
    )
    assert response.status_code == 200
    package_id = response.json()["evidence_package_id"]
    package_response = client.get(f"/v1/evidence-packages/{package_id}", headers=auth_headers)
    assert package_response.status_code == 200
    assert package_response.json()["publishable"] is True
    assert package_response.json()["source_independence"]["independent_source_execution_count"] == 1

    factory = client.app.state.session_factory
    with factory() as session:
        assert session.get(EvidencePackage, package_id) is not None


def test_research_source_redacts_sensitive_error_detail(client, auth_headers):
    run_id = _create_run(client, auth_headers)
    response = client.post(
        f"/v1/research-runs/{run_id}/sources",
        headers=auth_headers,
        json={
            "command_id": "research-source-redaction",
            "source_type": "xiaohongshu",
            "query": "AI 工作流",
            "status": "login_required",
            "error_code": "LOGIN_REQUIRED",
            "error_detail": "Authorization: Bearer top-secret-token",
            "actor_id": "research-worker",
        },
    )
    assert response.status_code == 200

    factory = client.app.state.session_factory
    with factory() as session:
        source = session.get(ResearchSource, response.json()["research_source_id"])
        assert source.error_detail == "[已脱敏的连接器错误详情]"
