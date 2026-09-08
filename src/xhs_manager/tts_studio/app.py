"""TTS Studio FastAPI 服务。

路由:
  GET  /                  → Studio 主页（生成 + 模型管理 + 历史）
  GET  /compare           → 已有的 A/B 对比页
  GET  /api/models        → 模型列表与加载状态
  POST /api/models/{id}/load|unload
  POST /api/generate      → 生成音频
  GET  /api/generations   → 历史列表
  DELETE /api/generations/{id}
  POST /api/upload-ref    → 上传参考音频
  GET  /audio/{name}      → 音频文件
  GET  /refs/{name}       → 参考音频文件
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from xhs_manager.domain import new_id
from xhs_manager.tts_studio import engine
from xhs_manager.tts_studio.config import get_settings
from xhs_manager.tts_studio.db import Generation, get_session, init_db
from xhs_manager.tts_studio.registry import Registry

logger = logging.getLogger(__name__)

settings = get_settings()
STATIC = Path(__file__).parent / "static"
# 全部转绝对路径：IndexTTS worker 的 cwd 在 mlx-indextts 目录下，
# 传相对路径过去会解析到错误位置（LibsndfileError: System error）。
AUDIO = Path(settings.audio_dir).resolve()
REFS = Path(settings.upload_dir).resolve()
COMPARE = Path("data/tts_compare").resolve()

app = FastAPI(title="TTS Studio")
registry = Registry(settings)


@app.on_event("startup")
def _startup():
    AUDIO.mkdir(parents=True, exist_ok=True)
    REFS.mkdir(parents=True, exist_ok=True)
    init_db(settings.db_path)
    for mid in settings.autoload:
        mid = mid.strip()
        if mid:
            logger.info("自动加载模型: %s", mid)
            registry.load(mid)


# ── 页面 ──────────────────────────────────────────────────────


@app.get("/", response_class=HTMLResponse)
def page_studio():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/compare", response_class=HTMLResponse)
def page_compare():
    p = COMPARE / "index.html"
    if not p.exists():
        raise HTTPException(404, "对比页不存在")
    # 对比页里的音频是相对路径，改指到 /compare-audio/
    html = p.read_text(encoding="utf-8")
    return html.replace('src="', 'src="/compare-audio/').replace(
        'src="/compare-audio/http', 'src="http')


@app.get("/compare-audio/{name}")
def compare_audio(name: str):
    p = COMPARE / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


# ── 模型管理 ──────────────────────────────────────────────────


@app.get("/api/models")
def api_models():
    return {"models": registry.list()}


@app.post("/api/models/{model_id}/load")
def api_load(model_id: str):
    try:
        return registry.load(model_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


@app.post("/api/models/{model_id}/unload")
def api_unload(model_id: str):
    try:
        return registry.unload(model_id)
    except KeyError as e:
        raise HTTPException(404, str(e))


# ── 生成 ──────────────────────────────────────────────────────


class GenReq(BaseModel):
    model_id: str
    text: str
    voice: Optional[str] = None
    ref_audio: Optional[str] = None       # refs/ 下的文件名
    emotion: Optional[str] = None
    emo_alpha: Optional[float] = None
    instruct: Optional[str] = None        # VoxCPM 的括号指令
    speed: Optional[float] = 1.0


@app.post("/api/generate")
def api_generate(req: GenReq):
    if not req.text.strip():
        raise HTTPException(400, "文本不能为空")
    st = registry.get(req.model_id)
    if st.status != "ready":
        raise HTTPException(400, f"{st.spec.name} 未就绪（当前 {st.status}），请先加载")

    gid = new_id()
    ext = "mp3" if req.model_id == "edge" else "wav"
    out = AUDIO / f"{gid}.{ext}"
    ref_path = str((REFS / req.ref_audio).resolve()) if req.ref_audio else None

    if ref_path and not Path(ref_path).exists():
        raise HTTPException(400, f"参考音频不存在: {req.ref_audio}")
    if st.spec.kind == "worker" and not ref_path:
        raise HTTPException(400, "IndexTTS-2 必须提供参考音频")

    params = req.model_dump(exclude={"model_id", "text", "ref_audio"})
    try:
        if req.model_id == "edge":
            elapsed = engine.gen_edge(req.text, out, req.voice or "", req.speed or 1.0)
        elif req.model_id == "voxcpm2":
            elapsed = engine.gen_voxcpm(req.text, out, settings, ref_path,
                                        req.instruct or "")
        elif req.model_id == "indextts2":
            elapsed = engine.gen_indextts(registry, req.text, out, ref_path,
                                          req.emotion or "", req.emo_alpha, req.speed)
        else:
            raise HTTPException(400, "不支持的模型")
    except Exception as e:
        rec = Generation(id=gid, model_id=req.model_id, text=req.text,
                         voice=req.voice, ref_audio=ref_path, params=params,
                         audio_path=str(out), status="error", error=str(e)[:900])
        with get_session() as s:
            s.add(rec); s.commit()
        raise HTTPException(500, str(e)[:400])

    dur, sr = engine.probe(out)
    rec = Generation(
        id=gid, model_id=req.model_id, text=req.text, voice=req.voice,
        ref_audio=ref_path, params=params, audio_path=str(out),
        duration=dur, elapsed=elapsed,
        rtf=round(elapsed / dur, 3) if dur else None,
        sample_rate=sr, file_size=out.stat().st_size if out.exists() else None,
        waveform=json.dumps(engine.extract_waveform(out)),
        status="ok",
    )
    with get_session() as s:
        s.add(rec); s.commit()
        return rec.to_dict()


# ── 历史 ──────────────────────────────────────────────────────


@app.get("/api/generations")
def api_generations(limit: int = 50, model_id: Optional[str] = None):
    with get_session() as s:
        q = s.query(Generation).order_by(Generation.created_at.desc())
        if model_id:
            q = q.filter(Generation.model_id == model_id)
        return {"items": [g.to_dict() for g in q.limit(limit).all()]}


@app.delete("/api/generations/{gid}")
def api_delete(gid: str):
    with get_session() as s:
        g = s.get(Generation, gid)
        if not g:
            raise HTTPException(404)
        Path(g.audio_path).unlink(missing_ok=True)
        s.delete(g); s.commit()
    return {"ok": True}


# ── 参考音频 ──────────────────────────────────────────────────


@app.get("/api/refs")
def api_refs():
    items = []
    for f in sorted(REFS.glob("*")):
        if f.suffix.lower() in (".wav", ".mp3", ".m4a", ".flac"):
            d, sr = engine.probe(f)
            items.append({"name": f.name, "duration": d, "sample_rate": sr,
                          "url": f"/refs/{f.name}"})
    return {"items": items}


@app.post("/api/upload-ref")
async def api_upload_ref(file: UploadFile = File(...)):
    name = Path(file.filename or "ref.wav").name
    dst = REFS / name
    with dst.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    d, sr = engine.probe(dst)
    return {"name": name, "duration": d, "sample_rate": sr, "url": f"/refs/{name}"}


@app.get("/audio/{name}")
def audio(name: str):
    p = AUDIO / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/refs/{name}")
def ref(name: str):
    p = REFS / name
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p)


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
