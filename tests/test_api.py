from xhs_manager.domain import TaskState
from xhs_manager.models import ContentTask
from xhs_manager.services import mark_topic_ready, transition_task


def create_account_and_task(client, auth_headers):
    account_response = client.post(
        "/v1/accounts",
        headers=auth_headers,
        json={
            "command_id": "api-account",
            "name": "测试账号",
            "timezone": "Asia/Shanghai",
            "strategy_config": {"persona": "AI 工作流实验员"},
            "actor_id": "operator-1",
        },
    )
    assert account_response.status_code == 200
    account = account_response.json()
    task_response = client.post(
        "/v1/tasks",
        headers=auth_headers,
        json={
            "command_id": "api-task",
            "account_id": account["account_id"],
            "primary_goal": "content_validation",
            "actor_id": "operator-1",
        },
    )
    assert task_response.status_code == 200
    return task_response.json()


def test_internal_api_requires_token(client):
    response = client.post(
        "/v1/accounts",
        json={
            "command_id": "unauthorized",
            "name": "测试账号",
            "strategy_config": {},
            "actor_id": "operator-1",
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


def test_create_and_get_task(client, auth_headers):
    task = create_account_and_task(client, auth_headers)
    response = client.get(f"/v1/tasks/{task['task_id']}", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["state"] == TaskState.PENDING_RESEARCH.value


def test_feishu_url_verification(client):
    response = client.post(
        "/webhooks/feishu/events",
        json={
            "type": "url_verification",
            "token": "verify-token",
            "challenge": "challenge-value",
        },
    )
    assert response.status_code == 200
    assert response.json() == {"challenge": "challenge-value"}


def test_feishu_card_approval_and_duplicate_event(
    client,
    auth_headers,
):
    task_result = create_account_and_task(client, auth_headers)
    app = client.app
    factory = app.state.session_factory
    with factory() as session:
        transition_task(
            session,
            task_id=task_result["task_id"],
            target=TaskState.RESEARCHING,
            actor_type="system",
            actor_id="worker-1",
            trace_id="api-research",
        )
        approval = mark_topic_ready(
            session,
            task_id=task_result["task_id"],
            topic_version_id="topic-api-v1",
        )
        approval_id = approval.id
        session.commit()

    payload = {
        "token": "verify-token",
        "header": {"event_id": "feishu-event-1"},
        "operator": {"open_id": "operator-1"},
        "action": {
            "value": {
                "approval_id": approval_id,
                "decision": "approve",
            }
        },
    }
    first = client.post("/webhooks/feishu/events", json=payload)
    second = client.post("/webhooks/feishu/events", json=payload)

    assert first.status_code == 200
    assert first.json()["result"]["task_state"] == TaskState.PRODUCING.value
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    with factory() as session:
        task = session.get(ContentTask, task_result["task_id"])
        assert task.state == TaskState.PRODUCING.value


def test_unauthorized_feishu_operator_is_rejected(client, auth_headers):
    task_result = create_account_and_task(client, auth_headers)
    factory = client.app.state.session_factory
    with factory() as session:
        transition_task(
            session,
            task_id=task_result["task_id"],
            target=TaskState.RESEARCHING,
            actor_type="system",
            actor_id="worker-1",
            trace_id="api-research-unauthorized",
        )
        approval = mark_topic_ready(
            session,
            task_id=task_result["task_id"],
            topic_version_id="topic-api-v2",
        )
        approval_id = approval.id
        session.commit()

    response = client.post(
        "/webhooks/feishu/events",
        json={
            "token": "verify-token",
            "header": {"event_id": "feishu-event-unauthorized"},
            "operator": {"open_id": "stranger"},
            "action": {
                "value": {
                    "approval_id": approval_id,
                    "decision": "approve",
                }
            },
        },
    )
    assert response.status_code == 403
