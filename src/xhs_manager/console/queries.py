"""控制台的只读查询层。

这里把「为了查一次库而反复手写的一次性脚本」固化下来。所有函数都只读，
不改任何数据，也不触发流水线——P0 的全部承诺就是「看得见」。

两条内容线在这里被统一成同一个「任务」形状：
  图文线  content_tasks          —— 主系统的任务，带审批与排期
  视频线  video_topics           —— 目前还没挂到 content_tasks 上

视频线的任务标记为 orphan=True（界面上叫「游离任务」）。这是诚实的表示：
外键要到 P2 才加，在那之前硬造一个占位 task 只会让数据更难解释。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.models import Account, ContentTask
from xhs_manager.video_pipeline.domain import PIPELINE_STAGE_ORDER, PipelineStatus
from xhs_manager.video_pipeline.models import (
    VideoComposition,
    VideoMaterial,
    VideoPipelineRun,
    VideoPublication,
    VideoRender,
    VideoScript,
    VideoTopic,
    VideoTrendSignal,
)

# 任务在界面上的四个桶。顺序即优先级：要你动手的排最前面。
BUCKET_ORDER = ("act", "err", "run", "done")


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite 读回来的 datetime 不带时区，统一补成 UTC 再比较。"""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    aware = _aware(dt)
    return aware.isoformat() if aware else None


def _fmt_duration(seconds: float | None) -> str:
    if not seconds:
        return "—"
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


# ── 视频线：把一个选题铺开成一条任务 ────────────────────────────


class _VideoBundle:
    """一个视频选题及其下游产物。一次查库取齐，避免 N+1。"""

    __slots__ = ("topic", "run", "script", "composition", "render", "publications")

    def __init__(self, topic, run, script, composition, render, publications):
        self.topic = topic
        self.run = run
        self.script = script
        self.composition = composition
        self.render = render
        self.publications = publications


def _load_video_bundles(session: Session, topic_id: str | None = None) -> list[_VideoBundle]:
    topics_q = session.query(VideoTopic)
    if topic_id:
        topics_q = topics_q.filter(VideoTopic.id == topic_id)
    topics = topics_q.all()
    if not topics:
        return []

    topic_ids = [t.id for t in topics]
    run_ids = {t.pipeline_run_id for t in topics}

    runs = {r.id: r for r in session.query(VideoPipelineRun)
            .filter(VideoPipelineRun.id.in_(run_ids)).all()}

    scripts: dict[str, Any] = {}
    for sc in (session.query(VideoScript)
               .filter(VideoScript.topic_id.in_(topic_ids))
               .order_by(VideoScript.version).all()):
        scripts[sc.topic_id] = sc          # 同一选题多版时取版本号最大的

    script_ids = [sc.id for sc in scripts.values()]
    comps: dict[str, Any] = {}
    if script_ids:
        for c in (session.query(VideoComposition)
                  .filter(VideoComposition.script_id.in_(script_ids))
                  .order_by(VideoComposition.created_at).all()):
            comps[c.script_id] = c

    comp_ids = [c.id for c in comps.values()]
    renders: dict[str, Any] = {}
    if comp_ids:
        for r in (session.query(VideoRender)
                  .filter(VideoRender.composition_id.in_(comp_ids))
                  .order_by(VideoRender.created_at).all()):
            renders[r.composition_id] = r

    pubs: dict[str, list] = {}
    for p in (session.query(VideoPublication)
              .filter(VideoPublication.topic_id.in_(topic_ids)).all()):
        pubs.setdefault(p.topic_id, []).append(p)

    bundles = []
    for t in topics:
        script = scripts.get(t.id)
        comp = comps.get(script.id) if script else None
        render = renders.get(comp.id) if comp else None
        bundles.append(_VideoBundle(
            topic=t, run=runs.get(t.pipeline_run_id), script=script,
            composition=comp, render=render, publications=pubs.get(t.id, []),
        ))
    return bundles


def _video_state(b: _VideoBundle) -> tuple[str, str, str]:
    """(状态码, 中文标签, 桶)。

    视频线目前没有审批环节，所以没有 act 桶——渲染完就等人工发布。
    等 P4 接上审批之后，「待发布审批」才会真正出现在 act 里。
    """
    published = [p for p in b.publications if p.status == "published"]
    failed_pubs = [p for p in b.publications if p.status == "failed"]

    if published:
        return "published", "已发布", "done"
    if failed_pubs:
        return "publish_failed", "发布失败", "err"
    if b.render is not None:
        if b.render.status == "failed":
            return "render_failed", "渲染失败", "err"
        if b.render.status == "completed" and b.render.output_path:
            return "rendered", "待发布", "act"
        return "rendering", "渲染中", "run"
    if b.run is not None and b.run.status == PipelineStatus.FAILED.value:
        return "failed", "流水线失败", "err"
    if b.composition is not None:
        return "composed", "待渲染", "run"
    if b.script is not None:
        return "scripted", "待组合", "run"
    return "drafted", "制作中", "run"


def _video_updated(b: _VideoBundle) -> datetime | None:
    stamps = [_aware(b.topic.created_at)]
    for obj in (b.script, b.composition, b.render):
        if obj is not None:
            stamps.append(_aware(getattr(obj, "created_at", None)))
    if b.render is not None:
        stamps.append(_aware(b.render.completed_at))
    for p in b.publications:
        stamps.append(_aware(p.published_at) or _aware(p.created_at))
    return max([s for s in stamps if s], default=None)


def _video_output(b: _VideoBundle) -> str:
    if b.render is not None and b.render.duration:
        scenes = len(b.script.scenes) if b.script else 0
        size = f" · {b.render.file_size / 1024 / 1024:.1f} MB" if b.render.file_size else ""
        return f"{_fmt_duration(b.render.duration)} · {scenes} 场景{size}"
    if b.script is not None:
        return f"{len(b.script.scenes)} 场景 · 约 {b.script.total_duration}s"
    return "—"


def _task_from_video(b: _VideoBundle) -> dict[str, Any]:
    code, label, bucket = _video_state(b)
    return {
        "id": b.topic.id,
        "kind": "video",
        "title": b.topic.title,
        "format": "video",
        "state": code,
        "state_label": label,
        "bucket": bucket,
        "output": _video_output(b),
        "updated_at": _iso(_video_updated(b)),
        "run_id": b.topic.pipeline_run_id,
        "task_id": b.topic.task_id,
        # 没认领进任务的片子标成「游离」。这不是缺陷，是如实表示：
        # 视频线可以脱离任务独立跑，硬造占位任务只会让数据更难解释。
        "orphan": b.topic.task_id is None,
        "account": None,
        "score": b.topic.total_score,
    }


def _task_from_content(task: ContentTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "kind": "article",
        "title": task.content_pillar or "（未命名图文任务）",
        "format": "article",
        "state": task.state,
        "state_label": task.state,
        "bucket": "run",
        "output": "—",
        "updated_at": _iso(task.updated_at),
        "run_id": None,
        "orphan": False,
        "score": None,
    }


def _account_names(session: Session) -> dict[str, str]:
    return {a.id: a.name for a in session.query(Account).all()}


def list_tasks(session: Session) -> dict[str, Any]:
    """统一的任务列表。图文与视频在同一张表里，用 format 区分。"""
    tasks = [_task_from_video(b) for b in _load_video_bundles(session)]

    # 已认领的视频要显示归属账号——这是 P2 打通之后才有的信息
    content_tasks = {t.id: t for t in session.query(ContentTask).all()}
    names = _account_names(session)
    linked_ids = set()
    for t in tasks:
        owner = content_tasks.get(t["task_id"]) if t["task_id"] else None
        if owner is not None:
            linked_ids.add(owner.id)
            t["account"] = names.get(owner.account_id)

    # 只列出没有视频挂靠的图文任务，避免同一个任务出现两行
    tasks += [_task_from_content(t) for t in content_tasks.values()
              if t.id not in linked_ids]

    # 先按桶（要你动手的在前），桶内按更新时间倒序
    tasks.sort(key=lambda t: (
        BUCKET_ORDER.index(t["bucket"]) if t["bucket"] in BUCKET_ORDER else 9,
        _neg_key(t["updated_at"]),
    ))

    counts = {b: sum(1 for t in tasks if t["bucket"] == b) for b in BUCKET_ORDER}
    return {"tasks": tasks, "counts": counts, "total": len(tasks)}


def _neg_key(iso: str | None) -> tuple[int, str]:
    """把 ISO 时间变成「越新越靠前」的排序键。没有时间的一律排最后。"""
    if not iso:
        return (1, "")
    # 取反：同长度的 ISO 字符串按字符逐位取补，即可用升序实现倒序
    return (0, "".join(chr(0x7E - ord(c) + 0x20) for c in iso[:19]))


# ── 一致性校验：四个总时长必须对得上 ──────────────────────────


def consistency(b: _VideoBundle) -> dict[str, Any] | None:
    """脚本 / 场景合计 / 组合时间轴 / 成片，四个时长的一致性。

    存在的理由是一次真实事故：校准后的场景时长没写进库，
    画面按 195 秒排版、音频只有 168 秒，从第二个场景开始就越差越多。
    这四个数并排放，那次事故一眼就能看出来。
    """
    if b.script is None:
        return None

    scenes_total = sum(float(s.get("duration") or 0) for s in (b.script.scenes or []))
    items = [
        {"key": "script", "label": "脚本声明", "value": float(b.script.total_duration or 0)},
        {"key": "scenes", "label": "场景合计", "value": round(scenes_total, 2)},
        {"key": "composition", "label": "组合时间轴",
         "value": float(b.composition.total_duration) if b.composition else None},
        {"key": "render", "label": "成片",
         "value": float(b.render.duration) if (b.render and b.render.duration) else None},
    ]
    known = [i["value"] for i in items if i["value"]]
    drift = round(max(known) - min(known), 2) if len(known) > 1 else 0.0
    # 1 秒以内属于编码取整的正常误差，超过就是真的对不上了
    return {"items": items, "drift": drift, "pass": drift <= 1.0, "measured": len(known)}


# ── 任务详情 ────────────────────────────────────────────────


_STAGE_LABEL = {
    PipelineStatus.COLLECTING: "采集",
    PipelineStatus.SELECTING: "选题脚本",
    PipelineStatus.MATERIALIZING: "素材配音",
    PipelineStatus.COMPOSING: "HTML 组合",
    PipelineStatus.RENDERING: "渲染",
    PipelineStatus.PUBLISHING: "发布",
}


def _sub_pipeline(session: Session, b: _VideoBundle) -> list[dict[str, Any]]:
    """视频子流水线的六个阶段，每段带上它自己的产物。"""
    run = b.run
    reached = -1
    if run is not None:
        try:
            cur = PipelineStatus(run.status)
            reached = (len(PIPELINE_STAGE_ORDER) - 1 if cur == PipelineStatus.COMPLETED
                       else PIPELINE_STAGE_ORDER.index(cur))
        except ValueError:
            reached = -1

    signals = (session.query(VideoTrendSignal)
               .filter(VideoTrendSignal.pipeline_run_id == run.id).count()) if run else 0
    platforms = []
    if run is not None:
        rows = (session.query(VideoTrendSignal.platform)
                .filter(VideoTrendSignal.pipeline_run_id == run.id).distinct().all())
        platforms = sorted(r[0] for r in rows)

    materials = []
    if b.script is not None:
        materials = (session.query(VideoMaterial)
                     .filter(VideoMaterial.script_id == b.script.id,
                             VideoMaterial.material_type != "audio").all())

    stages: list[dict[str, Any]] = []

    def add(stage: PipelineStatus, detail: str, done: bool, extra: dict | None = None) -> None:
        idx = PIPELINE_STAGE_ORDER.index(stage)
        state = "done" if done else ("current" if idx == reached + 1 else "todo")
        stages.append({
            "stage": stage.value, "label": _STAGE_LABEL[stage],
            "detail": detail, "state": state, **(extra or {}),
        })

    add(PipelineStatus.COLLECTING,
        f"{len(platforms)} 平台 / {signals} 条信号" if signals else "无信号（脚本直接喂入）",
        signals > 0, {"platforms": platforms})

    add(PipelineStatus.SELECTING,
        (f"{len(b.script.scenes)} 场景 · 由 {b.script.generation_model} 生成"
         if b.script else "未生成脚本"),
        b.script is not None)

    add(PipelineStatus.MATERIALIZING,
        f"{len(materials)} 个素材 · {'旁白已对齐' if _has_marks(b.script) else '旁白未对齐'}"
        if materials else "未收集素材",
        bool(materials))

    add(PipelineStatus.COMPOSING,
        (f"{b.composition.total_duration:.0f}s · {len(b.composition.transition_effects)} 种转场"
         if b.composition else "未组合"),
        b.composition is not None,
        {"html_path": b.composition.html_path if b.composition else None})

    render_detail = "未渲染"
    if b.render is not None:
        if b.render.status == "failed":
            render_detail = b.render.error_detail or "渲染失败"
        elif b.render.duration:
            frames = int(b.render.duration * (b.render.fps or 30))
            size = f" · {b.render.file_size / 1024 / 1024:.1f} MB" if b.render.file_size else ""
            render_detail = f"{frames} 帧 · {b.render.format or ''}{size}"
    add(PipelineStatus.RENDERING, render_detail,
        b.render is not None and b.render.status == "completed",
        {"output_path": b.render.output_path if b.render else None,
         "failed": bool(b.render and b.render.status == "failed")})

    if b.publications:
        pub_detail = " · ".join(f"{p.platform} {p.status}" for p in b.publications)
    else:
        pub_detail = "未发布"
    add(PipelineStatus.PUBLISHING, pub_detail,
        any(p.status == "published" for p in b.publications))

    return stages


def _has_marks(script) -> bool:
    if script is None:
        return False
    return any(s.get("speech_marks") for s in (script.scenes or []))


def get_task(session: Session, task_id: str) -> dict[str, Any] | None:
    bundles = _load_video_bundles(session, topic_id=task_id)
    if not bundles:
        return None
    b = bundles[0]
    base = _task_from_video(b)

    scenes = []
    for i, s in enumerate((b.script.scenes or []) if b.script else [], 1):
        overlay = s.get("text_overlay") or {}
        scenes.append({
            "index": i,
            "scene_id": s.get("scene_id") or f"s{i:02d}",
            "layout": s.get("layout") or "—",
            "duration": s.get("duration"),
            "transition": s.get("transition") or "none",
            "main": overlay.get("main", "") if isinstance(overlay, dict) else str(overlay),
            "sub": overlay.get("sub", "") if isinstance(overlay, dict) else "",
            "narration": s.get("narration", ""),
            "marks": len(s.get("speech_marks") or []),
        })

    base.update({
        "angle": b.topic.angle,
        "why_now": b.topic.why_now,
        "audience": b.topic.target_audience,
        "scores": b.topic.scores,
        "run": {
            "id": b.run.id, "date": str(b.run.run_date), "status": b.run.status,
            "error": b.run.error_detail,
        } if b.run else None,
        "consistency": consistency(b),
        "stages": _sub_pipeline(session, b),
        "scenes": scenes,
        "publications": [{
            "platform": p.platform, "status": p.status, "title": p.title,
            "url": p.external_url, "error": p.error_detail,
            "published_at": _iso(p.published_at),
        } for p in b.publications],
    })
    return base


def list_signals(session: Session, *, run_id: str | None = None,
                 platform: str | None = None, limit: int = 300) -> dict[str, Any]:
    """采集信号浏览器。

    「互动」按平台各自的指标给：B站是播放、V2EX 是回复、小红书是点赞。
    压成同一个「点赞」列会让多数行变成空值——那是数据模型的真实差异，
    不是可以被平均掉的噪音。
    """
    q = session.query(VideoTrendSignal)
    if run_id:
        q = q.filter(VideoTrendSignal.pipeline_run_id == run_id)
    if platform:
        q = q.filter(VideoTrendSignal.platform == platform)

    rows = q.order_by(VideoTrendSignal.heat_score.desc()).limit(limit).all()
    now = datetime.now(timezone.utc)

    def engagement(r) -> str:
        e = r.engagement or {}
        if r.platform == "bilibili" and e.get("views"):
            views = e["views"]
            return f"{views / 10000:.1f}万 播放" if views >= 10000 else f"{views} 播放"
        if r.platform == "v2ex" and e.get("comments"):
            return f"{e['comments']} 回复"
        if e.get("likes"):
            return f"{e['likes']:,} 赞"
        if e.get("comments"):
            return f"{e['comments']} 评论"
        return "—"

    items = []
    for r in rows:
        published = _aware(r.published_at)
        age = (now - published).days if published else None
        items.append({
            "id": r.id,
            "platform": r.platform,
            "title": r.title,
            "author": r.author,
            "url": r.source_url,
            "engagement": engagement(r),
            "heat": r.heat_score,
            "published_at": _iso(r.published_at),
            "age_days": age,
            # 站外索引拿不到互动数据，来源要标出来，否则会被当成同等质量的样本
            "via": "索引" if "索引通道" in (r.tags or []) else "站内",
        })

    platforms = sorted({p[0] for p in session.query(VideoTrendSignal.platform).distinct()})
    return {"signals": items, "platforms": platforms, "total": len(items)}


def list_assets(session: Session) -> dict[str, Any]:
    """产物浏览器：按任务分组，一个任务的成片、封面、旁白在一起。

    存在的意义就是不用再 cd 到两层 uuid 目录里翻文件。
    """
    groups = []
    for b in _load_video_bundles(session):
        if b.script is None:
            continue
        items: list[dict[str, Any]] = []

        if b.render is not None and b.render.output_path:
            items.append({
                "kind": "成片", "path": b.render.output_path,
                "meta": f"{_fmt_duration(b.render.duration)}"
                        + (f" · {b.render.file_size / 1024 / 1024:.1f} MB"
                           if b.render.file_size else ""),
                "playable": True,
            })
            for platform, cover in (b.render.covers or {}).items():
                items.append({"kind": f"封面 · {platform}", "path": cover,
                              "meta": "", "playable": False})
        if b.composition is not None and b.composition.html_path:
            items.append({
                "kind": "HTML 组合", "path": b.composition.html_path,
                "meta": f"{b.composition.total_duration:.0f}s · 可拖时间轴",
                "playable": False,
            })

        if not items:
            continue
        code, label, bucket = _video_state(b)
        groups.append({
            "task_id": b.topic.task_id,
            "topic_id": b.topic.id,
            "title": b.topic.title,
            "state_label": label,
            "bucket": bucket,
            "items": items,
        })

    total = sum(len(g["items"]) for g in groups)
    return {"groups": groups, "total": total}


def list_runs(session: Session) -> list[dict[str, Any]]:
    """流水线运行列表。任务是「产出」视角，运行是「批次」视角，两者都要能看。"""
    runs = (session.query(VideoPipelineRun)
            .order_by(VideoPipelineRun.created_at.desc()).all())
    return [{
        "id": r.id, "date": str(r.run_date), "status": r.status,
        "trigger": r.trigger_type,
        "trends": r.trend_count, "topics": r.topic_count,
        "videos": r.video_count, "published": r.published_count,
        "error": r.error_detail,
        "created_at": _iso(r.created_at),
    } for r in runs]
