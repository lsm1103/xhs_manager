"""控制台的 HTTP 层：只读 API + 静态页。

挂在主 API 进程下的 /console，不另起端口——业务 API 和控制台共用同一套
数据库会话与配置，分开进程只会多一份要维护的启动方式。

P0 只暴露读接口。唯一会碰文件系统的是 /console/api/file，
它严格限制在 output_base_dir 之内（见 _safe_media_path）。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from xhs_manager.console import probes, queries
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

    return router
