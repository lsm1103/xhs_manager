"""控制台的 HTTP 层：读接口 + 少量写动作 + 静态页。

挂在主 API 进程下的 /console，不另起端口——业务 API 和控制台共用同一套
数据库会话与配置，分开进程只会多一份要维护的启动方式。

两处需要防守的地方
------------------
文件读取  /console/api/file 严格限制在 output_base_dir 之内（见 _safe_media_path）。
写动作    控制台只绑回环地址、没有登录态，所以写接口要挡住「浏览器被别的页面
          驱动着发请求」这条路：要求一个自定义请求头。跨源带自定义头会触发
          预检，而这里没开 CORS，预检必然失败——一行代码换掉整类 CSRF。
          另外设了 XHS_CONSOLE_TOKEN 就同时校验它。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from xhs_manager.console import probes, queries
from xhs_manager.services import add_audit
from xhs_manager.video_pipeline.config import get_video_settings

STATIC_DIR = Path(__file__).parent / "static"

# 允许通过 /console/api/file 读取的扩展名。控制台要能直接播放成片、看封面，
# 但不能变成一个任意文件读取器。
MEDIA_SUFFIXES = {".mp4", ".webm", ".mov", ".m4v", ".png", ".jpg", ".jpeg",
                  ".webp", ".mp3", ".m4a", ".aac", ".wav", ".srt", ".html"}

MEDIA_TYPES = {
    ".mp4": "video/mp4", ".webm": "video/webm", ".mov": "video/quicktime",
    ".m4v": "video/x-m4v", ".png": "image/png", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".webp": "image/webp", ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4", ".aac": "audio/aac", ".wav": "audio/wav",
    ".srt": "text/plain; charset=utf-8", ".html": "text/html; charset=utf-8",
}


def _media_root() -> Path:
    return Path(get_video_settings().output_base_dir).resolve()


def _safe_media_path(raw: str) -> Path:
    """把请求里的路径收敛到产物目录内。

    三道检查缺一不可：解析符号链接后仍在根目录下、扩展名在白名单里、文件存在。
    只做前缀字符串比较是不够的——`../` 和符号链接都能绕过。

    相对路径按两种基准各试一次：库里存的是相对工作目录的
    `data/video_pipeline/<run>/...`，而界面上手填时更自然的是相对产物目录。
    无论走哪条，最终都要落在产物目录里才放行。
    """
    root = _media_root()
    candidate = Path(raw)

    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        by_cwd = (Path.cwd() / candidate).resolve()
        resolved = by_cwd if by_cwd.is_relative_to(root) else (root / candidate).resolve()

    if not resolved.is_relative_to(root):
        raise HTTPException(status_code=403, detail="路径超出产物目录")
    if resolved.suffix.lower() not in MEDIA_SUFFIXES:
        raise HTTPException(status_code=403, detail=f"不支持的文件类型: {resolved.suffix}")
    if not resolved.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return resolved


#: 写接口必须带的请求头。值是什么不重要，重要的是「跨源发不出来」。
WRITE_HEADER = "X-Console-Action"


def _guard_write(
    x_console_action: str = Header(default="", alias=WRITE_HEADER),
    x_console_token: str = Header(default="", alias="X-Console-Token"),
) -> None:
    if not x_console_action:
        raise HTTPException(status_code=403, detail=f"写操作需要 {WRITE_HEADER} 请求头")
    expected = os.environ.get("XHS_CONSOLE_TOKEN", "").strip()
    if expected and x_console_token != expected:
        raise HTTPException(status_code=403, detail="控制台令牌无效")


def _drop_pending_work(session: Session, topic) -> int:
    """把这条选题在队列里还没跑的活撤掉。

    标记成「放弃」之后 worker 还在渲染，是这个界面上最容易让人
    误会的一种现象。已经在跑的那条不动——半路掐掉只会留下半个产物，
    它跑完自然就停了。
    """
    from xhs_manager.models import WorkItem

    if not topic.task_id:
        return 0
    items = session.query(WorkItem).filter(
        WorkItem.task_id == topic.task_id,
        WorkItem.status == "pending",
    ).all()
    for item in items:
        session.delete(item)
    return len(items)


def _parse_when(raw: str | None, delay_minutes: int) -> datetime:
    """把界面传来的时间解析成 UTC 时刻。不给就按「多少分钟后」。

    两件事必须一起做对，否则排期会错开一个时区：
    - 界面上的 datetime-local 没有时区，它是用户本机的墙上时间；
    - 落库必须是 UTC。时间列不带时区，SQLite 会把偏移量直接扔掉，
      而全库的读侧（_aware）一律按 UTC 解释这些裸时间。
    """
    if not raw:
        return datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
    try:
        when = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"时间格式看不懂: {raw}") from e
    if when.tzinfo is None:
        when = when.astimezone()          # 按本机时区理解
    return when.astimezone(timezone.utc)


def create_console_router(get_session: Callable[[], Iterator[Session]]) -> APIRouter:
    """构造控制台路由。get_session 由主 app 注入，共用它的会话生命周期。"""
    router = APIRouter(prefix="/console", tags=["console"])

    @router.get("", response_class=HTMLResponse, include_in_schema=False)
    @router.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index() -> HTMLResponse:
        page = STATIC_DIR / "index.html"
        if not page.exists():
            raise HTTPException(status_code=500, detail="控制台静态文件缺失")
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @router.get("/api/tasks")
    def api_tasks(session: Session = Depends(get_session)) -> dict:
        return queries.list_tasks(session)

    @router.get("/api/tasks/{task_id}")
    def api_task(task_id: str, session: Session = Depends(get_session)) -> dict:
        task = queries.get_task(session, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        return task

    @router.get("/api/runs")
    def api_runs(session: Session = Depends(get_session)) -> dict:
        return {"runs": queries.list_runs(session)}

    @router.get("/api/signals")
    def api_signals(
        run_id: str | None = Query(None),
        platform: str | None = Query(None),
        session: Session = Depends(get_session),
    ) -> dict:
        return queries.list_signals(session, run_id=run_id, platform=platform)

    @router.get("/api/assets")
    def api_assets(session: Session = Depends(get_session)) -> dict:
        return queries.list_assets(session)

    @router.get("/api/approvals")
    def api_approvals(session: Session = Depends(get_session)) -> dict:
        from xhs_manager.video_pipeline import promote

        rows = promote.pending_approvals(session)
        for r in rows:
            r["expires_at"] = r["expires_at"].isoformat() if r["expires_at"] else None
        return {"approvals": rows}

    @router.get("/api/tools")
    def api_tools(
        refresh: bool = Query(False, description="跳过缓存，强制重新探测"),
        session: Session = Depends(get_session),
    ) -> dict:
        return probes.run_all(session, get_video_settings(), force=refresh)

    @router.get("/api/file")
    def api_file(path: str = Query(..., description="产物目录内的相对或绝对路径")) -> FileResponse:
        resolved = _safe_media_path(path)
        return FileResponse(
            resolved,
            media_type=MEDIA_TYPES.get(resolved.suffix.lower()),
            filename=resolved.name,
        )

    # ── 写动作 ──────────────────────────────────────────────
    # 每个都要带 WRITE_HEADER；批准还会真的把片子排进发布队列，
    # 所以界面上另外做了二次确认。

    @router.post("/api/approvals/{approval_id}/approve",
                 dependencies=[Depends(_guard_write)])
    def api_approve(
        approval_id: str,
        body: dict | None = None,
        session: Session = Depends(get_session),
    ) -> dict:
        from xhs_manager.models import ContentTask
        from xhs_manager.video_pipeline import promote, steps

        body = body or {}
        when = _parse_when(body.get("scheduled_at"), int(body.get("delay_minutes", 5)))
        window = timedelta(hours=float(body.get("window_hours", 2)))
        try:
            plan = promote.approve_and_schedule(
                session, approval_id, scheduled_at=when, window=window,
                comment=body.get("comment", ""),
            )
        except promote.PromoteError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

        task = session.get(ContentTask, plan.task_id)
        topic = steps._topic_for_task(session, plan.task_id) if task else None
        if task is not None and topic is not None:
            steps.enqueue_publish(
                session, task=task, run_id=topic.pipeline_run_id,
                available_at=plan.scheduled_at,
            )
        return {
            "plan_id": plan.id,
            "scheduled_at": plan.scheduled_at.isoformat(),
            "allowed_until": plan.allowed_until.isoformat(),
            "idempotency_key": plan.idempotency_key,
        }

    @router.post("/api/approvals/{approval_id}/reject",
                 dependencies=[Depends(_guard_write)])
    def api_reject(
        approval_id: str,
        body: dict | None = None,
        session: Session = Depends(get_session),
    ) -> dict:
        from xhs_manager.video_pipeline import promote

        body = body or {}
        try:
            approval = promote.reject(session, approval_id, comment=body.get("comment", ""))
        except promote.PromoteError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        return {"approval_id": approval.id, "status": approval.status}

    @router.post("/api/tasks/{task_id}/state", dependencies=[Depends(_guard_write)])
    def api_set_state(
        task_id: str,
        body: dict | None = None,
        session: Session = Depends(get_session),
    ) -> dict:
        """人工标记任务状态：放弃 / 已过期 / 撤销标记。

        标记只写在选题上，因为任务台列的就是选题——有些还没认领进
        内容任务，不该因为「没有 ContentTask」就不能标记。
        认领过的，顺带把内容任务也收掉。
        """
        from xhs_manager.console.queries import MANUAL_STATES
        from xhs_manager.domain import ALLOWED_TRANSITIONS, TaskState
        from xhs_manager.models import ContentTask
        from xhs_manager.video_pipeline.models import VideoTopic

        body = body or {}
        state = (body.get("state") or "").strip()
        if state and state not in MANUAL_STATES:
            raise HTTPException(
                status_code=400,
                detail=f"只能标记为 {'、'.join(MANUAL_STATES)}，或传空值撤销标记",
            )

        topic = session.get(VideoTopic, task_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="任务不存在")

        if state:
            # 撤销标记时要能回到原来的样子，所以先把旧状态记下来
            previous = topic.status
            if previous not in MANUAL_STATES:
                topic.previous_status = previous
            topic.status = state
        else:
            topic.status = topic.previous_status or "selected"
            topic.previous_status = None

        # 队列里还没跑的活要撤掉：标记为放弃之后 worker 还在渲染，
        # 是这个界面上最容易让人误会的一种现象。
        killed = _drop_pending_work(session, topic)

        task_state = None
        if topic.task_id:
            task = session.get(ContentTask, topic.task_id)
            if task is not None and state:
                current = TaskState(task.state)
                if TaskState.CANCELLED in ALLOWED_TRANSITIONS.get(current, frozenset()):
                    task.state = TaskState.CANCELLED.value
                task_state = task.state

        add_audit(
            session, actor_type="human", actor_id="console",
            action="video_topic.marked" if state else "video_topic.unmarked",
            resource_type="video_topic", resource_id=topic.id,
            trace_id=f"topic:{topic.id}",
            reason=body.get("comment") or (f"标记为 {state}" if state else "撤销标记"),
        )
        return {"task_id": topic.id, "status": topic.status,
                "task_state": task_state, "cancelled_items": killed}

    @router.post("/api/publications/{pub_id}/retry", dependencies=[Depends(_guard_write)])
    def api_retry_publication(
        pub_id: str,
        body: dict | None = None,
        session: Session = Depends(get_session),
    ) -> dict:
        """把一条发布记录复位，好让清单重新可用。

        复位不等于「重新发出去」：人工模式下它只是把失败原因擦掉，
        没有任何东西会被自动推出去。

        已经记为「已发布」的要显式 force——那是在修改一条事实记录
        （比如笔记被删了、或者当初标错了），不该和「清掉失败」共用一个
        不假思索的按钮。
        """
        from xhs_manager.video_pipeline.models import VideoPublication

        body = body or {}
        pub = session.get(VideoPublication, pub_id)
        if pub is None:
            raise HTTPException(status_code=404, detail="发布记录不存在")
        was = pub.status
        if was == "published" and not body.get("force"):
            raise HTTPException(
                status_code=409,
                detail="这条已记为已发布。要改成未发布，用「撤销已发布」。",
            )

        pub.status = "awaiting_manual"
        pub.publish_method = "manual"
        pub.error_detail = None
        if was == "published":
            pub.external_url = None
            pub.published_at = None
            add_audit(
                session, actor_type="human", actor_id="console",
                action="video.publish_record_cleared",
                resource_type="video_publication", resource_id=pub.id,
                trace_id=f"publication:{pub.id}",
                reason=body.get("comment") or "撤销「已发布」标记",
            )
        return {"publication_id": pub.id, "status": pub.status, "was": was}

    def _mark_published(session: Session, pub, body: dict) -> dict:
        """把一条发布记录标成「已发布」。

        这是记录事实，不是执行发布——这里一行浏览器代码都不碰。
        """
        url = (body.get("url") or "").strip()
        if url and not url.startswith(("http://", "https://")):
            raise HTTPException(status_code=400, detail="链接要以 http:// 或 https:// 开头")

        pub.status = "published"
        pub.publish_method = "manual"
        pub.external_url = url or None
        pub.published_at = _parse_when(body.get("published_at"), 0)
        pub.error_detail = None

        add_audit(
            session, actor_type="human", actor_id="console",
            action="video.published_manually", resource_type="video_publication",
            resource_id=pub.id, trace_id=f"publication:{pub.id}",
            reason=url or "人工发布，未填链接",
        )
        return {"publication_id": pub.id, "status": pub.status,
                "url": pub.external_url,
                "published_at": pub.published_at.isoformat()}

    @router.post("/api/publications/{pub_id}/mark-published",
                 dependencies=[Depends(_guard_write)])
    def api_mark_published(
        pub_id: str,
        body: dict | None = None,
        session: Session = Depends(get_session),
    ) -> dict:
        """记下「我已经自己发过了」。"""
        from xhs_manager.video_pipeline.models import VideoPublication

        pub = session.get(VideoPublication, pub_id)
        if pub is None:
            raise HTTPException(status_code=404, detail="发布记录不存在")
        return _mark_published(session, pub, body or {})

    @router.post("/api/tasks/{task_id}/mark-published",
                 dependencies=[Depends(_guard_write)])
    def api_mark_task_published(
        task_id: str,
        body: dict | None = None,
        session: Session = Depends(get_session),
    ) -> dict:
        """人工模式下直接收尾：没有发布记录就现场补一条，再标成已发布。

        人工发布不经审批排期——闸门是给「自动驱动浏览器操作真实账号」设的，
        而这条路上没有任何东西会被推出去。让它也去等一个时间点，等来的
        只是同一份清单。
        """
        from xhs_manager.video_pipeline import manual_publish
        from xhs_manager.video_pipeline.models import VideoTopic

        topic = session.get(VideoTopic, task_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        try:
            pub = manual_publish.ensure_manual_publication(
                session, topic, get_video_settings(),
            )
        except manual_publish.ManualPublishError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return _mark_published(session, pub, body or {})

    @router.post("/api/plans/{plan_id}/cancel", dependencies=[Depends(_guard_write)])
    def api_cancel_plan(
        plan_id: str,
        body: dict | None = None,
        session: Session = Depends(get_session),
    ) -> dict:
        """撤销排期。批准能一键触发发布，就必须能一键收回。"""
        from xhs_manager.models import WorkItem
        from xhs_manager.video_pipeline import promote

        body = body or {}
        try:
            plan = promote.cancel_plan(session, plan_id, comment=body.get("comment", ""))
        except promote.PromoteError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e

        # 闸门已经会拦，但把队列里那条也撤掉：留着它只会到点跑一次、失败一次，
        # 在「出错」里留一条看着像事故的记录。
        killed = 0
        items = session.query(WorkItem).filter(
            WorkItem.task_id == plan.task_id,
            WorkItem.step_type == "video_publish",
            WorkItem.status.in_(["pending", "failed"]),
        ).all()
        for item in items:
            session.delete(item)
            killed += 1
        return {"plan_id": plan.id, "status": plan.status, "cancelled_items": killed}

    @router.post("/api/tasks/{task_id}/rerun", dependencies=[Depends(_guard_write)])
    def api_rerun(
        task_id: str,
        body: dict | None = None,
        session: Session = Depends(get_session),
    ) -> dict:
        """重跑某个阶段。task_id 是视频选题 ID，和详情页一致。"""
        from xhs_manager.models import ContentTask
        from xhs_manager.video_pipeline import steps
        from xhs_manager.video_pipeline.domain import PipelineStatus
        from xhs_manager.video_pipeline.models import VideoTopic

        body = body or {}
        stage_raw = body.get("stage", "")
        try:
            stage = PipelineStatus(stage_raw)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"未知阶段: {stage_raw}") from e
        # 发布不走这里：它必须经过审批和排期
        if stage is PipelineStatus.PUBLISHING:
            raise HTTPException(
                status_code=400, detail="发布不能直接重跑，要走审批与排期",
            )

        topic = session.get(VideoTopic, task_id)
        if topic is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if not topic.task_id:
            raise HTTPException(
                status_code=409, detail="这个选题还没认领进任务，先跑 cli adopt",
            )
        task = session.get(ContentTask, topic.task_id)

        # 重跑要能真的重跑：同一条运行的同一阶段已有工作项时，把它复位，
        # 否则幂等键会让这次点击变成一次静默的空操作。
        item = steps.enqueue_stage(
            session, task=task, run_id=topic.pipeline_run_id, stage=stage,
        )
        if item is not None and item.status in ("succeeded", "failed"):
            item.status = "pending"
            item.attempt = 0
            item.error_code = None
            item.error_detail = None
            item.available_at = datetime.now(timezone.utc)
        return {
            "work_item_id": item.id if item else None,
            "stage": stage.value,
            "status": item.status if item else None,
        }

    return router
