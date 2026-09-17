"""配音（TTS）的 HTTP 层，挂在主服务的 /tts 下。

原来这是一个独立进程（:8420，自带 FastAPI app、自带 SQLite）。
合并之后：同一个进程、同一个库、控制台里的一页。

模型进程的归属
--------------
重模型不在这个进程里。indextts2 / omnivoice 各自跑在独立子进程中，
本进程只握着管道（见 registry.Registry）。所以「合并」并没有把
十几 GB 塞进 Web 服务——但它确实带来一个后果：主服务退出，
那些子进程就成了没人认领的孤儿。因此 shutdown 时必须显式卸载。

视频流水线仍然通过 HTTP 调这里（video_pipeline/tts/studio.py），
只是基址从 :8420 换成了 :8000/tts。路径形状保持不变：
/api/models、/api/generate、/audio/<name> 都还在原来的相对位置上。
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Callable

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from xhs_manager.console.app import _guard_write
from xhs_manager.domain import new_id
from xhs_manager.models import TtsGeneration
from xhs_manager.tts_studio import engine
from xhs_manager.tts_studio.config import get_settings
from xhs_manager.tts_studio.registry import Registry

logger = logging.getLogger(__name__)

# 音频扩展名白名单。和控制台的 /console/api/file 一个道理：
# 这两个路由要能放音频，但不能变成任意文件读取器。
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg"}


def _settings():
    return get_settings()


def _dirs() -> tuple[Path, Path]:
    """音频与参考音频目录，绝对路径。

    必须是绝对的：IndexTTS worker 的 cwd 在 mlx-indextts 仓库里，
    相对路径传过去会解析到别的地方（LibsndfileError: System error）。
    """
    s = _settings()
    audio = Path(s.audio_dir).resolve()
    refs = Path(s.upload_dir).resolve()
    audio.mkdir(parents=True, exist_ok=True)
    refs.mkdir(parents=True, exist_ok=True)
    return audio, refs


def _safe_audio(root: Path, name: str) -> Path:
    """只允许取 root 下的音频文件，名字里不许带路径。"""
    candidate = (root / Path(name).name).resolve()
    if not candidate.is_relative_to(root):
        raise HTTPException(status_code=403, detail="路径超出音频目录")
    if candidate.suffix.lower() not in AUDIO_SUFFIXES:
        raise HTTPException(status_code=403, detail=f"不支持的文件类型: {candidate.suffix}")
    if not candidate.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return candidate


def _view(g: TtsGeneration) -> dict:
    return {
        "id": g.id,
        "model_id": g.model_id,
        "text": g.text,
        "voice": g.voice,
        "ref_audio": Path(g.ref_audio).name if g.ref_audio else None,
        "params": g.params or {},
        # 相对路径：视频流水线的客户端拿它拼在自己的基址后面
        "audio_url": f"/audio/{Path(g.audio_path).name}",
        "duration": g.duration,
        "elapsed": g.elapsed,
        "rtf": g.rtf,
        "sample_rate": g.sample_rate,
        "file_size": g.file_size,
        "waveform": json.loads(g.waveform) if g.waveform else [],
        "status": g.status,
        "error": g.error,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


class GenReq(BaseModel):
    model_id: str
    text: str
    voice: str | None = None
    ref_audio: str | None = None      # refs/ 下的文件名
    ref_text: str | None = None       # 参考音频转写（OmniVoice 可选）
    emotion: str | None = None
    emo_alpha: float | None = None
    instruct: str | None = None       # VoxCPM 的括号指令
    speed: float | None = 1.0


def create_tts_router(get_session: Callable[[], Iterator[Session]]) -> APIRouter:
    router = APIRouter(prefix="/tts", tags=["tts"])
    registry = Registry(_settings())
    # 注册表握着模型子进程的句柄。挂到 app.state 上（见 api.create_app），
    # 启动预热和退出卸载都要够得着它。
    router.registry = registry

    # ── 模型 ────────────────────────────────────────────────

    @router.get("/api/models")
    def api_models() -> dict:
        return {"models": registry.list()}

    @router.post("/api/models/{model_id}/load", dependencies=[Depends(_guard_write)])
    def api_load(model_id: str) -> dict:
        try:
            return registry.load(model_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    @router.post("/api/models/{model_id}/unload", dependencies=[Depends(_guard_write)])
    def api_unload(model_id: str) -> dict:
        try:
            return registry.unload(model_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e

    # ── 生成 ────────────────────────────────────────────────

    @router.post("/api/generate", dependencies=[Depends(_guard_write)])
    def api_generate(req: GenReq, session: Session = Depends(get_session)) -> dict:
        if not req.text.strip():
            raise HTTPException(status_code=400, detail="文本不能为空")
        try:
            st = registry.get(req.model_id)
        except KeyError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        if st.status != "ready":
            raise HTTPException(
                status_code=400,
                detail=f"{st.spec.name} 未就绪（当前 {st.status}），请先加载",
            )

        audio_dir, refs_dir = _dirs()
        gid = new_id()
        ext = "mp3" if req.model_id == "edge" else "wav"
        out = audio_dir / f"{gid}.{ext}"

        ref_path = None
        if req.ref_audio:
            ref = (refs_dir / Path(req.ref_audio).name).resolve()
            if not ref.is_file():
                raise HTTPException(status_code=400, detail=f"参考音频不存在: {req.ref_audio}")
            ref_path = str(ref)
        if req.model_id == "indextts2" and not ref_path:
            raise HTTPException(status_code=400, detail="IndexTTS-2 必须提供参考音频")

        params = req.model_dump(exclude={"model_id", "text", "ref_audio"})
        settings = _settings()
        try:
            if req.model_id == "edge":
                elapsed = engine.gen_edge(req.text, out, req.voice or "", req.speed or 1.0)
            elif req.model_id == "voxcpm2":
                elapsed = engine.gen_voxcpm(req.text, out, settings, ref_path,
                                            req.instruct or "")
            elif req.model_id == "indextts2":
                elapsed = engine.gen_indextts(registry, req.text, out, ref_path,
                                              req.emotion or "", req.emo_alpha, req.speed)
            elif req.model_id == "omnivoice":
                elapsed = engine.gen_omnivoice(
                    registry, req.text, out, ref_path, req.ref_text or "",
                    req.instruct or "", req.speed,
                )
            else:
                raise HTTPException(status_code=400, detail="不支持的模型")
        except HTTPException:
            raise
        except Exception as e:
            # 失败也留一条记录：配音失败的原因（模型没加载、参考音频不对）
            # 事后要能查，不能只在日志里一闪而过。
            #
            # 这里必须自己提交。请求抛异常时会话依赖会回滚，
            # 只 add 不 commit 的话，这条记录跟着异常一起没了——
            # 恰恰是最需要它的那次。
            session.add(TtsGeneration(
                id=gid, model_id=req.model_id, text=req.text, voice=req.voice,
                ref_audio=ref_path, params=params, audio_path=str(out),
                status="error", error=str(e)[:900],
            ))
            session.commit()
            logger.exception("配音失败: %s", req.model_id)
            raise HTTPException(status_code=500, detail=str(e)[:400]) from e

        dur, sr = engine.probe(out)
        rec = TtsGeneration(
            id=gid, model_id=req.model_id, text=req.text, voice=req.voice,
            ref_audio=ref_path, params=params, audio_path=str(out),
            duration=dur, elapsed=elapsed,
            rtf=round(elapsed / dur, 3) if dur else None,
            sample_rate=sr, file_size=out.stat().st_size if out.exists() else None,
            waveform=json.dumps(engine.extract_waveform(out)),
            status="ok",
        )
        session.add(rec)
        session.flush()
        return _view(rec)

    # ── 历史 ────────────────────────────────────────────────

    @router.get("/api/generations")
    def api_generations(
        limit: int = Query(50, ge=1, le=500),
        model_id: str | None = Query(None),
        session: Session = Depends(get_session),
    ) -> dict:
        q = select(TtsGeneration).order_by(TtsGeneration.created_at.desc())
        if model_id:
            q = q.where(TtsGeneration.model_id == model_id)
        rows = session.scalars(q.limit(limit)).all()
        return {"items": [_view(g) for g in rows]}

    @router.delete("/api/generations/{gid}", dependencies=[Depends(_guard_write)])
    def api_delete(gid: str, session: Session = Depends(get_session)) -> dict:
        g = session.get(TtsGeneration, gid)
        if g is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        audio_dir, _ = _dirs()
        target = Path(g.audio_path)
        # 只删自己产物目录里的文件。库里存的是绝对路径，
        # 万一被改过，也不该顺着它去删别处的东西。
        if target.resolve().is_relative_to(audio_dir):
            target.unlink(missing_ok=True)
        session.delete(g)
        return {"ok": True}

    # ── 参考音频 ────────────────────────────────────────────

    @router.get("/api/refs")
    def api_refs() -> dict:
        _, refs_dir = _dirs()
        items = []
        for f in sorted(refs_dir.glob("*")):
            if f.suffix.lower() in AUDIO_SUFFIXES:
                d, sr = engine.probe(f)
                items.append({"name": f.name, "duration": d, "sample_rate": sr,
                              "url": f"/tts/refs/{f.name}"})
        return {"items": items}

    @router.post("/api/upload-ref", dependencies=[Depends(_guard_write)])
    async def api_upload_ref(file: Annotated[UploadFile, File()]) -> dict:
        _, refs_dir = _dirs()
        name = Path(file.filename or "ref.wav").name
        if Path(name).suffix.lower() not in AUDIO_SUFFIXES:
            raise HTTPException(status_code=400, detail=f"只收音频文件: {name}")
        dst = refs_dir / name
        with dst.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        d, sr = engine.probe(dst)
        return {"name": name, "duration": d, "sample_rate": sr,
                "url": f"/tts/refs/{name}"}

    # ── 文件 ────────────────────────────────────────────────

    @router.get("/audio/{name}")
    def audio(name: str) -> FileResponse:
        audio_dir, _ = _dirs()
        return FileResponse(_safe_audio(audio_dir, name))

    @router.get("/refs/{name}")
    def ref(name: str) -> FileResponse:
        _, refs_dir = _dirs()
        return FileResponse(_safe_audio(refs_dir, name))

    return router
