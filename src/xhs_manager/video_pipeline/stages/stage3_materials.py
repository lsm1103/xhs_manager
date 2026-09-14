"""Stage 3: 素材收集 — 为每个视频脚本的每个场景收集/生成素材。

素材来源优先级:
  1. MoneyPrinterTurbo — Pexels 素材搜索 + TTS 语音（首选，已验证可用）
  2. Pixelle-Video API — 图片/视频生成
  3. 文字卡片兜底 — 简单 HTML 渲染

同时为每个场景生成 TTS 旁白音频。
"""

import logging
import shutil
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from xhs_manager.domain import new_id
from xhs_manager.video_pipeline.config import VideoPipelineSettings
from xhs_manager.video_pipeline.domain import (
    MaterialSource,
    MaterialType,
    StageError,
)
from xhs_manager.video_pipeline.integrations.moneyprinter import MoneyPrinterTurbo
from xhs_manager.video_pipeline.models import (
    VideoPipelineRun,
    VideoMaterial,
    VideoScript,
    VideoTopic,
)

logger = logging.getLogger(__name__)


def collect_materials(
    session: Session,
    run: VideoPipelineRun,
    settings: VideoPipelineSettings,
) -> dict[str, Any]:
    """为所有视频脚本收集素材。"""

    scripts = (
        session.query(VideoScript)
        .join(VideoTopic, VideoScript.topic_id == VideoTopic.id)
        .filter(
            VideoTopic.pipeline_run_id == run.id,
            VideoScript.status == "ready",
        )
        .all()
    )

    if not scripts:
        raise StageError("collect_materials", "没有可处理的视频脚本")

    # 初始化工具
    mpt = MoneyPrinterTurbo(settings.moneyprinter_path)

    total_materials = 0
    total_tts = 0
    video_stats: list[dict] = []

    for script in scripts:
        topic = session.get(VideoTopic, script.topic_id)
        topic_title = topic.title if topic else "unknown"

        # 创建素材输出目录
        output_dir = Path(settings.output_base_dir) / run.id / script.id
        output_dir.mkdir(parents=True, exist_ok=True)

        scene_count = 0
        tts_count = 0

        # ── 方式 1: 用 MoneyPrinterTurbo 批量搜索素材 ──
        if mpt.available:
            batch_result = _collect_via_moneyprinter(
                session, mpt, script, output_dir, settings,
            )
            scene_count += batch_result["materials_saved"]
            tts_count += batch_result["tts_saved"]

        # ── 方式 2: 对仍缺视觉素材的场景逐个补充 ──
        # 注意：音频不在此循环处理。整篇旁白由上面的批量步骤一次产出，
        # 分场景音频会与整篇音轨冲突，渲染时无法叠加。
        for scene in script.scenes:
            scene_id = scene.get("scene_id", f"s{scene.get('order', 0):02d}")

            has_visual = (
                session.query(VideoMaterial.id)
                .filter(
                    VideoMaterial.script_id == script.id,
                    VideoMaterial.scene_id == scene_id,
                    VideoMaterial.material_type != "audio",
                    VideoMaterial.selected == True,
                )
                .first()
            )
            if has_visual:
                continue

            # 尝试 Pixelle-Video API 生成
            if settings.pixelle_available:
                try:
                    material = _pixelle_generate(
                        session, script.id, scene, output_dir, settings,
                    )
                    if material:
                        session.add(material)
                        scene_count += 1
                        continue
                except Exception as e:
                    logger.warning("Pixelle 生成失败 (场景 %s): %s", scene_id, e)

            # 兜底: 文字卡片
            try:
                material = _generate_text_card(
                    session, script.id, scene, output_dir,
                )
                if material:
                    session.add(material)
                    scene_count += 1
            except Exception as e:
                logger.warning("文字卡片生成失败 (场景 %s): %s", scene_id, e)

        total_materials += scene_count
        total_tts += tts_count
        video_stats.append({
            "topic": topic_title[:30],
            "script_id": script.id,
            "scenes": len(script.scenes),
            "materials_collected": scene_count,
            "tts_generated": tts_count,
        })

    run.video_count = len(scripts)

    if total_materials == 0:
        raise StageError("collect_materials", "未能收集到任何素材")

    return {
        "total_materials": total_materials,
        "total_tts": total_tts,
        "videos": video_stats,
    }


# ═══════════════════════════════════════════════════════════════════
# MoneyPrinterTurbo 集成
# ═══════════════════════════════════════════════════════════════════


def _collect_via_moneyprinter(
    session: Session,
    mpt: MoneyPrinterTurbo,
    script: VideoScript,
    output_dir: Path,
    settings: VideoPipelineSettings,
) -> dict[str, int]:
    """用 MoneyPrinterTurbo 批量获取素材和 TTS。"""
    materials_saved = 0
    tts_saved = 0

    # 1. 逐场景搜素材
    #
    # 原来是「把所有关键词拼成一次搜索，再按 i % len(materials) 轮着分」。
    # 这在 6 场景的短片上还看得过去，17 个场景就穿帮了：
    # 素材池只有 5 条，同一段背景要出现三四次，而且第 12 个场景配的
    # 是第 3 个场景的搜索词搜来的画面——画面和内容完全对不上。
    #
    # 改成每个场景用自己的 material_hints 搜，并且跨场景去重。
    # 代价是 MPT CLI 要跑 N 次（慢），换来的是画面真的对得上内容。
    scenes = script.scenes
    n_scenes = max(len(scenes), 1)
    used_paths: set[str] = set()
    leftovers: list[dict[str, Any]] = []
    task_id = ""

    def _save(scene_id: str, mat: dict[str, Any]) -> bool:
        """把一条素材落盘并入库。"""
        dst_path = output_dir / f"{scene_id}_clip.mp4"
        try:
            shutil.copy2(mat["path"], str(dst_path))
        except Exception as e:
            logger.warning("素材复制失败 (场景 %s): %s", scene_id, e)
            return False
        session.add(VideoMaterial(
            id=new_id(),
            script_id=script.id,
            scene_id=scene_id,
            material_type=MaterialType.VIDEO_CLIP.value,
            source_tool="moneyprinter",
            source_url=mat.get("source_url"),
            local_path=str(dst_path),
            license_type="pexels",
            file_size=mat.get("size"),
            selected=True,
            extra_meta={"mpt_task_id": task_id, "search_terms": mat.get("terms", [])},
        ))
        used_paths.add(mat["path"])
        return True

    pending: list[tuple[str, int]] = []      # 本轮没搜到素材的场景
    for scene in scenes:
        scene_id = scene.get("scene_id", f"s{scene.get('order', 0):02d}")
        terms = _search_terms_for_scene(scene)
        if not terms:
            pending.append((scene_id, int(scene.get("duration", 6))))
            continue

        clip_dur = max(3, int(scene.get("duration", script.total_duration // n_scenes)))
        result = mpt.search_materials(
            search_terms=terms,
            video_aspect="9:16",
            source="pexels",
            clip_duration=clip_dur,
        )
        task_id = result.get("task_id", task_id)
        candidates = _reject_portrait_materials(result.get("materials") or [])
        for c in candidates:
            c.setdefault("terms", terms)

        fresh = [c for c in candidates if c["path"] not in used_paths]
        if fresh:
            if _save(scene_id, fresh[0]):
                materials_saved += 1
            leftovers.extend(fresh[1:])
        else:
            # 这一轮全是已经用过的画面，先记下来，最后用别的场景的余料补
            leftovers.extend(candidates)
            pending.append((scene_id, clip_dur))
        logger.info(
            "场景 %s: 搜到 %d 条，可用 %d 条（关键词 %s）",
            scene_id, len(candidates), len(fresh), "/".join(terms[:2]),
        )

    # 2. 补齐：没搜到自己画面的场景，用别处的余料顶上（仍然优先没用过的）
    for scene_id, _dur in pending:
        spare = next((m for m in leftovers if m["path"] not in used_paths), None)
        if spare is None:
            continue
        if _save(scene_id, spare):
            materials_saved += 1
            logger.info("场景 %s: 用余料补位", scene_id)

    # 3. 逐场景合成旁白，并用真实语音时长校准场景时长
    #
    # 以前这里是「把 17 段旁白拼成一整段丢给 MPT 合成」。问题在于
    # 脚本里的 duration 是人写的估算值，而 TTS 念完要多久是另一回事：
    # 本片标称 195 秒，整段合成出来 228 秒——成片按 195 秒截断，
    # 最后三个场景的话直接没了，BGM 的情绪转折点也全部错位。
    #
    # audio/narration.py 早就实现了「逐段合成 → 量出真实时长 → 回写场景时长」，
    # 但一直没有人调用它。这里把它接上：时间轴以语音为准，而不是以估算为准。
    from xhs_manager.video_pipeline.audio.narration import build_aligned_narration

    scenes = list(script.scenes)   # 副本，校准后整体回写才能让 JSON 列生效
    track, total = build_aligned_narration(scenes, output_dir, settings)

    if track is None:
        logger.warning("逐场景旁白合成失败，退回 MPT 整段合成（时长可能对不齐）")
        full_script = "\n\n".join(
            sc.get("narration", "") for sc in scenes if sc.get("narration")
        )
        if full_script:
            tts_result = mpt.generate_tts(
                script_text=full_script,
                voice_name=settings.tts_voice.replace("Neural", "Neural-Female"),
            )
            if tts_result["success"] and tts_result.get("audio_path"):
                src = Path(tts_result["audio_path"])
                if src.exists():
                    track = output_dir / "full_narration.mp3"
                    shutil.copy2(str(src), str(track))
    else:
        script.scenes = scenes
        script.total_duration = int(round(total))
        logger.info("场景时长已按真实语音校准：总时长 %.1fs", total)

    if track and Path(track).exists():
        session.add(VideoMaterial(
            id=new_id(),
            script_id=script.id,
            scene_id=scenes[0].get("scene_id", "s01"),
            material_type="audio",
            source_tool="tts",
            local_path=str(track),
            license_type="generated",
            selected=True,
            extra_meta={"type": "full_narration", "aligned": track is not None},
        ))
        tts_saved += 1

    return {"materials_saved": materials_saved, "tts_saved": tts_saved}



# ── 肖像权防御：搜索词层 ──────────────────────────────────────────
# Pexels 的免版权授权覆盖拍摄者著作权，**不覆盖被拍摄者的肖像权**。
# 公开发布可辨识的人脸有侵权风险（2026-09-04 已因此下架过一条视频）。
#
# 这一层的职责是**降低搜出人脸素材的概率**，不是保证合规。
# 保证合规的是 `portrait_guard` 的结果侧人脸检测——因为搜索词和 Pexels
# 返回什么之间没有可靠映射：下架那条片子的词是 "office worker desk computer"，
# 完全正常，返回的却是店员的清晰正脸。词表拦不住这种情况，也不该拦。
#
# 设计换过一次：原来是枚举「危险短语」（close-up face / person smiling …）。
# 枚举开放集合必然漏——"young woman looking into camera" 就不在表里。
# 改成检测**人物主体词**：人物名词是封闭集合，覆盖率高得多。

# 以人为主体的名词。命中说明这个词会让 Pexels 返回以真人为画面主体的素材。
#
# 注意 crowd / audience 故意**不在**表里：无法辨识个人的远景人群是允许的素材，
# 删掉它们等于砍掉一种正当表达。这类词命中人脸的概率确实高，
# 但那正是结果侧检测该管的事。
_PERSON_SUBJECTS = frozenset("""
person persons people human humans someone somebody
man men woman women guy guys girl girls boy boys lady ladies
kid kids child children baby babies teenager adult elderly senior
worker workers employee employees staff colleague colleagues
customer customers client clients student students teacher teachers
doctor nurse engineer developer programmer designer manager boss
scientist researcher analyst chef waiter waitress barista cashier
businessman businesswoman entrepreneur freelancer artist athlete
team group family friends couple
model models actor actress
""".split())

# 直接指向面部的线索词。不是名词主体，但同样会拉来人脸画面。
_FACE_CUES = frozenset("""
face faces facial headshot headshots portrait portraits selfie selfies
smiling smile smiles eyes expression expressions
""".split())

# 多词短语要先处理，否则拆成单词后语义就没了
_RISKY_PHRASES = {
    "looking at camera": "",
    "looking into camera": "",
    "close up": "close-up",
    "close-up shot": "close-up",
}

# 删掉人物词后残留的虚词。"portrait of a scientist" 去掉人物词会剩下
# "of a"，这种残渣对 Pexels 检索毫无价值，只会稀释真正的关键词。
_STOPWORDS = frozenset("""
a an the of at in on to with and or for from by is are was were
his her their its my your our that this these those
""".split())

# 人物词被删光后如果剩不下东西，用它兜底：
# 手部画面既能表达"有人在做事"，又没有肖像权问题。
_FALLBACK_SUBJECT = "hands"


def sanitize_search_term(term: str) -> tuple[str, bool]:
    """把以人为主体的搜索词改写成不以人为主体的等价物。

    策略是**删除**人物主体词而不是替换成另一个近似词。
    原来的替换表里 "person smiling" → "people walking wide shot"、
    "portrait" → "wide shot silhouette"，替换结果本身仍然是拍人的词，
    照样能返回清晰正脸——等于没改。删掉之后剩下的物件/场景词才是安全的。

    Returns:
        (改写后的词, 是否发生了改写)
    """
    low = term.lower().strip()
    changed = False

    for phrase, repl in _RISKY_PHRASES.items():
        if phrase in low:
            low = low.replace(phrase, repl)
            changed = True

    tokens = []
    for raw in low.split():
        w = raw.strip(",.;:!?\"'()")
        if not w:
            continue
        if w in _PERSON_SUBJECTS or w in _FACE_CUES:
            changed = True
            continue
        tokens.append(w)

    # 只在真的删过人物词时才清停用词。没改写的词原样返回，
    # 避免对本来就合规的搜索词做无谓改动。
    kept = [w for w in tokens if w not in _STOPWORDS] if changed else tokens

    # 删完只剩零星修饰词（"office desk" 还行，"close-up" 就没法搜了），补个安全主体
    if changed and len(kept) < 2:
        kept.append(_FALLBACK_SUBJECT)

    return (" ".join(kept).strip(), changed)


# ── 肖像权防御：结果侧 ────────────────────────────────────────────


def _reject_portrait_materials(materials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """对已下载的素材做人脸检测，可辨识的直接剔除。

    这是唯一能兜住「无辜搜索词返回人脸画面」的一层，所以不设开关。
    全被剔除时返回空列表，上层会降级到 Pixelle 生成或文字卡片——
    宁可画面朴素，也不能带着可辨识人脸发出去。
    """
    from xhs_manager.video_pipeline import portrait_guard

    ok, why = portrait_guard.available()
    if not ok:
        logger.error(
            "人脸检测不可用（%s），按肖像权红线拒收全部 %d 个素材。"
            "修好检测器再跑，否则只能出文字卡片视频。",
            why, len(materials),
        )
        return []

    kept: list[dict[str, Any]] = []
    for m in materials:
        path = Path(m.get("path", ""))
        if not path.exists():
            continue
        scan = portrait_guard.scan(path)
        if scan.rejected:
            logger.warning("素材因肖像权被剔除: %s — %s", path.name, scan.detail)
            continue
        kept.append(m)

    if len(kept) < len(materials):
        logger.info(
            "肖像权过滤: %d 个素材通过，%d 个被剔除",
            len(kept), len(materials) - len(kept),
        )
    return kept



def _search_terms_for_scene(scene: dict[str, Any]) -> list[str]:
    """为单个场景挑出适合 Pexels 的英文搜索词。

    优先级：search: 提示 > gen: 提示（生图 prompt 本身是英文，可直接当搜索词）
    > 纯 ASCII 的 visual_desc。中文描述不拿去搜 Pexels —— 命中率极低。
    """
    hints = scene.get("material_hints") or []
    terms = [h[7:].strip() for h in hints if h.startswith("search:") and h[7:].strip()]
    if not terms:
        terms = [h[4:].strip()[:80] for h in hints if h.startswith("gen:") and h[4:].strip()]
    if not terms:
        visual = (scene.get("visual_desc") or "").strip()
        terms = [visual[:80]] if visual and visual.isascii() else []

    # 肖像权硬过滤：LLM 可能无视 prompt 约束，这里兜底改写
    safe: list[str] = []
    for t in terms:
        cleaned, changed = sanitize_search_term(t)
        if changed:
            logger.warning("素材词有肖像权风险，已改写: %r → %r", t, cleaned)
        safe.append(cleaned)
    return safe


def _mpt_generate_tts_single(
    mpt: MoneyPrinterTurbo,
    session: Session,
    script_id: str,
    scene_id: str,
    narration: str,
    output_dir: Path,
    settings: VideoPipelineSettings,
) -> VideoMaterial | None:
    """用 MoneyPrinterTurbo 为单个场景生成 TTS。"""
    result = mpt.generate_tts(
        script_text=narration,
        voice_name=settings.tts_voice.replace("Neural", "Neural-Female"),
    )

    if result["success"] and result.get("audio_path"):
        audio_src = Path(result["audio_path"])
        if audio_src.exists():
            audio_dst = output_dir / f"{scene_id}_narration.mp3"
            shutil.copy2(str(audio_src), str(audio_dst))

            return VideoMaterial(
                id=new_id(),
                script_id=script_id,
                scene_id=scene_id,
                material_type="audio",
                source_tool="moneyprinter",
                local_path=str(audio_dst),
                license_type="generated",
                selected=True,
                extra_meta={"narration": narration[:200]},
            )
    return None


# ═══════════════════════════════════════════════════════════════════
# Pixelle-Video 集成
# ═══════════════════════════════════════════════════════════════════


def _pixelle_generate(
    session: Session,
    script_id: str,
    scene: dict[str, Any],
    output_dir: Path,
    settings: VideoPipelineSettings,
) -> VideoMaterial | None:
    """通过 Pixelle-Video API 生成图片素材。"""
    import httpx

    scene_id = scene.get("scene_id", f"s{scene.get('order', 0):02d}")
    visual_desc = scene.get("visual_desc", "")
    if not visual_desc:
        return None

    try:
        with httpx.Client(timeout=120) as client:
            resp = client.post(
                f"{settings.pixelle_api_url}/api/image/generate",
                json={"prompt": visual_desc, "width": 1080, "height": 1920},
            )
            resp.raise_for_status()
            data = resp.json()

            image_url = data.get("image_url") or data.get("url")
            if not image_url:
                return None
            if not image_url.startswith("http"):
                image_url = f"{settings.pixelle_api_url}{image_url}"

            img_resp = client.get(image_url)
            img_resp.raise_for_status()

            save_path = output_dir / f"{scene_id}_visual.png"
            save_path.write_bytes(img_resp.content)

            return VideoMaterial(
                id=new_id(),
                script_id=script_id,
                scene_id=scene_id,
                material_type=MaterialType.GENERATED_IMAGE.value,
                source_tool=MaterialSource.PIXELLE.value,
                local_path=str(save_path),
                license_type="generated",
                width=1080,
                height=1920,
                file_size=len(img_resp.content),
                selected=True,
                extra_meta={"prompt": visual_desc},
            )
    except Exception as e:
        logger.debug("Pixelle 图片生成异常: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════
# 兜底: 文字卡片
# ═══════════════════════════════════════════════════════════════════


def _generate_text_card(
    session: Session,
    script_id: str,
    scene: dict[str, Any],
    output_dir: Path,
) -> VideoMaterial | None:
    """生成简单的文字卡片 HTML 作为兜底素材。"""
    scene_id = scene.get("scene_id", f"s{scene.get('order', 0):02d}")
    text_overlay = scene.get("text_overlay", {})
    visual_desc = scene.get("visual_desc", "")

    main_text = ""
    if isinstance(text_overlay, dict):
        main_text = text_overlay.get("main", "")
    elif isinstance(text_overlay, str):
        main_text = text_overlay

    display_text = main_text or visual_desc[:50]
    if not display_text:
        return None

    html_content = f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{
    width:1080px; height:1920px;
    display:flex; align-items:center; justify-content:center;
    background: linear-gradient(135deg, #0a0a0a 0%, #1a1a2e 50%, #16213e 100%);
    font-family: 'Noto Sans SC','PingFang SC',sans-serif;
    color:#fff; padding:80px;
  }}
  .card {{ text-align:center; max-width:900px; }}
  .card h1 {{
    font-size:72px; font-weight:700; line-height:1.3;
    background: linear-gradient(135deg,#667eea,#764ba2);
    -webkit-background-clip:text; -webkit-text-fill-color:transparent;
  }}
</style></head>
<body><div class="card"><h1>{display_text}</h1></div></body></html>"""

    html_path = output_dir / f"{scene_id}_card.html"
    html_path.write_text(html_content, encoding="utf-8")

    return VideoMaterial(
        id=new_id(),
        script_id=script_id,
        scene_id=scene_id,
        material_type=MaterialType.TEXT_CARD.value,
        source_tool=MaterialSource.IMAGE_GEN.value,
        local_path=str(html_path),
        license_type="generated",
        width=1080,
        height=1920,
        selected=True,
        extra_meta={"text": display_text},
    )
