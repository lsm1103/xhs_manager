"""小红书草稿准备执行器。

这个模块只负责把已经审批和排期的内容版本保存到平台草稿箱。公开发布不在
这里自动执行：最终点击必须由账号持有人在小红书页面完成或另行显式确认。
"""

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from xhs_manager.domain import (
    ApprovalStatus,
    AutomationScope,
    ConflictError,
    NotFoundError,
    TaskState,
)
from xhs_manager.external_actions import (
    mark_external_action_uncertain,
    prepare_external_action,
    start_external_action,
    succeed_external_action,
)
from xhs_manager.models import (
    ApprovalRequest,
    ContentTask,
    ContentVersion,
    PublicationPlan,
    SystemPause,
)


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str = ""


CommandRunner = Callable[[list[str]], CommandResult]


def _run_command(args: list[str]) -> CommandResult:
    completed = subprocess.run(args, check=False, capture_output=True, text=True)
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


class OpenCliXhsClient:
    def __init__(self, runner: CommandRunner = _run_command) -> None:
        self._runner = runner

    def whoami(self) -> dict[str, Any]:
        result = self._runner(["opencli", "xiaohongshu", "whoami", "-f", "json"])
        if result.returncode != 0:
            error = ConflictError("小红书浏览器会话不可用", action="运行 opencli doctor 并重新登录")
            error.code = "BROWSER_UNAVAILABLE"
            raise error
        payload = _parse_json(result.stdout)
        if not payload.get("logged_in"):
            error = ConflictError("小红书登录已失效", action="请在 Chrome 中重新登录小红书")
            error.code = "LOGIN_REQUIRED"
            raise error
        return payload

    def save_draft(
        self,
        *,
        title: str,
        body: str,
        images: list[Path],
        topics: list[str],
    ) -> CommandResult:
        args = [
            "opencli",
            "xiaohongshu",
            "publish",
            body,
            "--title",
            title,
            "--images",
            ",".join(str(path) for path in images),
            "--draft",
            "-f",
            "json",
        ]
        if topics:
            args.extend(["--topics", ",".join(topics)])
        return self._runner(args)

    def list_drafts(self) -> list[dict[str, Any]]:
        result = self._runner(["opencli", "xiaohongshu", "drafts", "-f", "json"])
        if result.returncode != 0:
            return []
        payload = _parse_json(result.stdout)
        return payload if isinstance(payload, list) else []


def _parse_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _resolve_assets(asset_paths: list[str], root: Path) -> list[Path]:
    if not asset_paths:
        error = ConflictError("内容版本没有可上传图片")
        error.code = "CONTENT_INCOMPLETE"
        raise error
    root = root.resolve()
    result: list[Path] = []
    for value in asset_paths:
        candidate = (root / value).resolve()
        if root not in candidate.parents or not candidate.is_file():
            error = ConflictError("发布素材不存在或不在项目目录内")
            error.code = "CONTENT_INCOMPLETE"
            raise error
        result.append(candidate)
    return result


def prepare_xiaohongshu_draft(
    session: Session,
    *,
    publication_plan_id: str,
    actor_id: str,
    project_root: Path,
    client: Optional[OpenCliXhsClient] = None,
) -> dict[str, Any]:
    plan = session.get(PublicationPlan, publication_plan_id)
    if plan is None:
        raise NotFoundError("发布计划不存在")
    task = session.get(ContentTask, plan.task_id)
    content = session.get(ContentVersion, plan.content_version_id)
    approval = session.get(ApprovalRequest, plan.approval_id)
    pause = session.get(SystemPause, AutomationScope.PUBLISHING.value)
    if task is None or content is None or approval is None:
        raise NotFoundError("发布计划关联的内容不完整")
    if pause is not None and pause.active:
        error = ConflictError("发布自动化已暂停", action="由有权限的操作人恢复后再试")
        error.code = "AUTOMATION_PAUSED"
        raise error
    if (
        TaskState(task.state) != TaskState.SCHEDULED
        or plan.status != "scheduled"
        or approval.status != ApprovalStatus.APPROVED.value
        or approval.resource_version != content.id
        or task.current_content_version_id != content.id
    ):
        error = ConflictError("发布计划或审批已失效")
        error.code = "PUBLISH_APPROVAL_INVALID"
        raise error
    if len(content.selected_title) > 20 or not content.body.strip():
        error = ConflictError("标题或正文不完整")
        error.code = "CONTENT_INCOMPLETE"
        raise error
    assets = _resolve_assets(content.asset_paths, project_root)
    request = {
        "title": content.selected_title,
        "body": content.body,
        "topics": content.topics,
        "assets": [str(path.relative_to(project_root.resolve())) for path in assets],
        "mode": "draft",
    }
    action = prepare_external_action(
        session,
        action_type="prepare_xiaohongshu_draft",
        resource_type="publication_plan",
        resource_id=plan.id,
        idempotency_key=f"{plan.id}:draft",
        request=request,
    )
    if action.status == "succeeded":
        return {"external_action_id": action.id, "status": action.status, **action.result}
    start_external_action(session, action_id=action.id)
    publisher = client or OpenCliXhsClient()
    account = publisher.whoami()
    prior_draft_ids = {str(draft.get("id", "")) for draft in publisher.list_drafts()}
    result = publisher.save_draft(
        title=content.selected_title,
        body=content.body,
        images=assets,
        topics=content.topics,
    )
    matches = [
        draft
        for draft in publisher.list_drafts()
        if (
            str(draft.get("id", "")) not in prior_draft_ids
            and draft.get("title") == content.selected_title
            and draft.get("images") == len(assets)
        )
    ]
    if matches:
        draft = matches[0]
        action = succeed_external_action(
            session,
            action_id=action.id,
            external_id=str(draft.get("id", "")),
            result={
                "draft_id": draft.get("id"),
                "title": content.selected_title,
                "image_count": len(assets),
                "account": account.get("username"),
                "command_returncode": result.returncode,
                "warning": "平台话题绑定需在草稿页复核" if result.returncode else "",
            },
            evidence_ref="opencli:xiaohongshu:drafts",
        )
        return {"external_action_id": action.id, "status": action.status, **action.result}
    action = mark_external_action_uncertain(
        session,
        action_id=action.id,
        result={"returncode": result.returncode, "stderr": result.stderr[-500:]},
        evidence_ref="opencli:xiaohongshu:publish",
    )
    return {"external_action_id": action.id, "status": action.status, **action.result}
