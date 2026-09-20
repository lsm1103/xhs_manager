"""长阶段不能攥着 SQLite 的写锁不放。

SQLite 全库只有一把写锁：从阶段里第一条 INSERT 到 commit 之间，**整个系统**
一个字都写不进去。渲染一支片子要好几分钟，这几分钟里卡住的不只是 worker 心跳
的续租 UPDATE，还有控制台的审批、登记已发布、重跑，以及别的 worker 的
claim_next——它们都在另一个连接上，一律等满 busy_timeout=5000 然后报
database is locked。用户在控制台点一下「我已发布」就直接报错。

租约那条链另算，而且已经和当初不一样了。c3339ab 之前，心跳连续三次续租失败
就永久停摆，租约一过期工作项就被 claim_next 当成「过期」重新领走，同一支片子
渲两遍；现在心跳只在租约真被接管时才退出，瞬时锁库会一路重试，所以只有持锁
时长能逼近该步骤租约的地方才谈得上被接管。对着 STEP_LEASE_SECONDS 数一遍，
本文件覆盖的两个阶段都不在那条线上（materialize / render 的租约都是 1800 秒，
实际持锁 500 秒以内）——它们守的是上面那条「持锁期间整个系统写不进去」。
真正贴着租约的是 stage2 选题（3 个选题 × 90 秒 vs 300 秒租约）和 stage6 发布
（平台间 sleep 默认 5 分钟，两个平台顶满 600 秒租约）。stage2 本文件末尾已经守住；
stage6 还没有——那条路径上 commit 必须留在平台间 sleep 之前，谁整理代码时把它
挪到 sleep 下面，锁就被攥着睡过去了，而这种改动在 review 里看着完全无辜。

规矩不变：阶段里的写只在真正写的那一瞬间持锁，长活（渲染、下载、claude -p、
上传、sleep）一律在事务外面跑。
"""

import time
from datetime import date

import pytest
from sqlalchemy.exc import OperationalError

from xhs_manager.domain import new_id
from xhs_manager.video_pipeline.config import VideoPipelineSettings
from xhs_manager.video_pipeline.domain import PipelineStatus
from xhs_manager.video_pipeline.models import (
    VideoComposition,
    VideoMaterial,
    VideoPipelineRun,
    VideoScript,
    VideoTopic,
)
from xhs_manager.video_pipeline.stages import stage3_materials, stage5_render


@pytest.fixture
def video_settings(tmp_path) -> VideoPipelineSettings:
    return VideoPipelineSettings(
        output_base_dir=str(tmp_path / "out"),
        moneyprinter_path=str(tmp_path / "no-mpt"),   # 不可用，走 HTML 渲染分支
    )


@pytest.fixture
def ready_composition(session_factory, tmp_path) -> str:
    """一个等着渲染的组合，返回 run_id。"""
    with session_factory() as s:
        run = VideoPipelineRun(
            id=new_id(), run_date=date(2026, 9, 20),
            status=PipelineStatus.RENDERING.value, trigger_type="manual",
        )
        s.add(run)
        s.flush()
        topic = VideoTopic(
            id=new_id(), pipeline_run_id=run.id, rank=1, title="测试选题",
            angle="", why_now="", target_audience="", video_type="explainer",
            estimated_duration=60, scores={}, total_score=8.0,
            source_signal_ids=[], status="selected",
        )
        s.add(topic)
        s.flush()
        script = VideoScript(
            id=new_id(), topic_id=topic.id, total_duration=60,
            scenes=[{"scene_id": "s01", "order": 1, "duration": 60}],
            platform_metadata={}, generation_model="test",
            generation_prompt_hash="x", status="ready",
        )
        s.add(script)
        s.flush()
        comp = VideoComposition(
            id=new_id(), script_id=script.id,
            composition_dir=str(tmp_path / "comp"),
            html_path=str(tmp_path / "comp" / "index.html"),
            total_duration=60.0, status="render_ready",
        )
        s.add(comp)
        s.commit()
        return run.id


def test_render_stage_releases_the_write_lock_while_rendering(
    session_factory, ready_composition, video_settings, tmp_path, monkeypatch
):
    """渲染进行中，另一个连接（现实中是 worker 的心跳）必须还能写库。"""
    probe: dict = {}

    def fake_render(comp, render, settings, scenes=None):
        # 此刻渲染记录已经入库，ffmpeg 正在跑。换一个连接写一笔试试。
        began = time.monotonic()
        try:
            with session_factory() as other:
                run = other.get(VideoPipelineRun, ready_composition)
                run.error_detail = "心跳到此一游"
                other.commit()
            probe["ok"] = True
        except OperationalError as exc:
            probe["ok"] = False
            probe["error"] = str(exc)
        probe["seconds"] = time.monotonic() - began

        out = tmp_path / "video.mp4"
        out.write_bytes(b"not really a video")
        return out

    monkeypatch.setattr(stage5_render, "_render_html_to_mp4", fake_render)
    monkeypatch.setattr(stage5_render, "_generate_covers", lambda *a, **k: {})
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.integrations.renderer.probe_duration",
        lambda _p: 60.0,
    )

    with session_factory() as session:
        run = session.get(VideoPipelineRun, ready_composition)
        result = stage5_render.render_videos(session, run, video_settings)
        session.commit()

    assert result["renders_completed"] == 1
    assert probe.get("ok"), f"渲染期间写锁没放开: {probe.get('error')}"
    # busy_timeout 是 5 秒；真被锁住的话这里会卡满 5 秒再抛错
    assert probe["seconds"] < 2, f"写入等了 {probe['seconds']:.1f} 秒，锁还攥着"


def test_failed_render_can_be_picked_up_again(
    session_factory, ready_composition, video_settings, tmp_path, monkeypatch
):
    """渲染失败当场落库之后，重跑还得能把这支片子捡回来。

    失败状态以前靠「整段事务回滚」消失，现在它是真写进去的——
    如果重跑只认 render_ready，一次瞬时失败就永久卡死这支片子。
    """
    monkeypatch.setattr(
        stage5_render, "_render_html_to_mp4",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ffmpeg 挂了")),
    )

    from xhs_manager.video_pipeline.domain import StageError

    with session_factory() as session:
        run = session.get(VideoPipelineRun, ready_composition)
        with pytest.raises(StageError):
            stage5_render.render_videos(session, run, video_settings)

    with session_factory() as session:
        comp = session.query(VideoComposition).one()
        assert comp.status == "error"       # 失败状态确实落库了

    # 重跑：换成能成的渲染
    out = tmp_path / "video.mp4"
    out.write_bytes(b"not really a video")
    monkeypatch.setattr(stage5_render, "_render_html_to_mp4", lambda *a, **k: out)
    monkeypatch.setattr(stage5_render, "_generate_covers", lambda *a, **k: {})
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.integrations.renderer.probe_duration",
        lambda _p: 60.0,
    )

    with session_factory() as session:
        run = session.get(VideoPipelineRun, ready_composition)
        result = stage5_render.render_videos(session, run, video_settings)
        session.commit()

    assert result["renders_completed"] == 1


# ── Stage 3：素材收集 ─────────────────────────────────────────────
#
# 和渲染是同一条规矩，只是长活换成了「每个场景一次 MPT CLI（十几秒）
# 加一次 TTS 合成」。场景一多，攥着锁的时间比渲染还长。


def _write_png(path, width: int, height: int):
    """写一个头部合法的最小 PNG。probe_image_size 只读前 24 字节，够用。"""
    import struct

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13) + b"IHDR"
        + struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
        + b"\x00" * 4
    )
    return path


def _probe_another_connection(session_factory, run_id: str) -> dict:
    """换一个连接写一笔，记下成功与否和耗时。现实中这就是 worker 的心跳。"""
    probe: dict = {}
    began = time.monotonic()
    try:
        with session_factory() as other:
            run = other.get(VideoPipelineRun, run_id)
            run.error_detail = "心跳到此一游"
            other.commit()
        probe["ok"] = True
    except OperationalError as exc:
        probe["ok"] = False
        probe["error"] = str(exc)
    probe["seconds"] = time.monotonic() - began
    return probe


def _assert_lock_was_free(probe: dict) -> None:
    assert probe, "探针没被调用到，测试没覆盖到目标代码路径"
    assert probe.get("ok"), f"素材收集期间写锁没放开: {probe.get('error')}"
    # busy_timeout 是 5 秒；真被锁住的话这里会卡满 5 秒再抛错
    assert probe["seconds"] < 2, f"写入等了 {probe['seconds']:.1f} 秒，锁还攥着"


def _seed_script(session_factory, scenes: list[dict]) -> str:
    """建一条等着收素材的脚本，返回 run_id。"""
    with session_factory() as s:
        run = VideoPipelineRun(
            id=new_id(), run_date=date(2026, 9, 21),
            status=PipelineStatus.MATERIALIZING.value, trigger_type="manual",
        )
        s.add(run)
        s.flush()
        topic = VideoTopic(
            id=new_id(), pipeline_run_id=run.id, rank=1, title="测试选题",
            angle="", why_now="", target_audience="", video_type="explainer",
            estimated_duration=12, scores={}, total_score=8.0,
            source_signal_ids=[], status="selected",
        )
        s.add(topic)
        s.flush()
        s.add(VideoScript(
            id=new_id(), topic_id=topic.id, total_duration=12, scenes=scenes,
            platform_metadata={}, generation_model="test",
            generation_prompt_hash="x", status="ready",
        ))
        s.commit()
        return run.id


@pytest.fixture
def material_settings(tmp_path) -> VideoPipelineSettings:
    return VideoPipelineSettings(
        output_base_dir=str(tmp_path / "out"),
        moneyprinter_path=str(tmp_path / "no-mpt"),     # 不可用，走兜底链
        pixelle_path="",                                # 同上
        local_material_dirs=[str(tmp_path / "shots")],
    )


def test_local_material_claim_releases_the_write_lock_between_scenes(
    session_factory, material_settings, tmp_path, monkeypatch
):
    """认领第二个场景的本地素材时，第一个已经入库——这时别的连接必须还能写。

    每认领一条就提交，靠的就是这个：认领之间要拷文件、要读图片头，
    素材一多就是好几秒，攥着锁的话 worker 心跳续租直接超时。
    """
    shots = tmp_path / "shots"
    shots.mkdir()
    _write_png(shots / "a.png", 800, 400)
    _write_png(shots / "b.png", 800, 400)

    run_id = _seed_script(session_factory, [
        {"scene_id": "s01", "order": 1, "duration": 6,
         "material_hints": ["local:a.png"]},
        {"scene_id": "s02", "order": 2, "duration": 6,
         "material_hints": ["local:b.png"]},
    ])

    probe: dict = {}
    real_probe_size = stage3_materials.probe_image_size
    calls: list = []

    def probing(path):
        calls.append(path)
        if len(calls) == 2:      # 第二个场景：第一条素材此刻已经 commit 了
            probe.update(_probe_another_connection(session_factory, run_id))
        return real_probe_size(path)

    monkeypatch.setattr(stage3_materials, "probe_image_size", probing)

    with session_factory() as session:
        run = session.get(VideoPipelineRun, run_id)
        result = stage3_materials.collect_materials(session, run, material_settings)
        session.commit()

    assert result["total_materials"] == 2
    _assert_lock_was_free(probe)


def test_fallback_card_generation_releases_the_write_lock_between_scenes(
    session_factory, material_settings, tmp_path, monkeypatch
):
    """兜底链同理：生成第二张文字卡片时，第一张已经入库。

    守的是 collect_materials 里 Pixelle / 文字卡片两个分支的 commit。
    改回 flush 的话，整段兜底循环会攥着锁跑完所有场景。
    """
    (tmp_path / "shots").mkdir()
    run_id = _seed_script(session_factory, [
        {"scene_id": "s01", "order": 1, "duration": 6, "material_hints": []},
        {"scene_id": "s02", "order": 2, "duration": 6, "material_hints": []},
    ])

    probe: dict = {}
    calls: list = []

    def fake_card(session, script_id, scene, output_dir):
        calls.append(scene["scene_id"])
        if len(calls) == 2:
            probe.update(_probe_another_connection(session_factory, run_id))
        return VideoMaterial(
            id=new_id(), script_id=script_id, scene_id=scene["scene_id"],
            material_type="text_card", source_tool="fallback",
            local_path=str(tmp_path / f"{scene['scene_id']}.html"),
            license_type="generated", selected=True,
        )

    monkeypatch.setattr(stage3_materials, "_generate_text_card", fake_card)

    with session_factory() as session:
        run = session.get(VideoPipelineRun, run_id)
        result = stage3_materials.collect_materials(session, run, material_settings)
        session.commit()

    assert result["total_materials"] == 2
    _assert_lock_was_free(probe)


# ── Stage 3：MPT 批量路径 ─────────────────────────────────────────
#
# 上面两条覆盖的是兜底链。MPT 装着的时候素材走的是这条，兜底链根本不进——
# 而且它攥锁的时间最长：每个场景一次 CLI 调用（十几秒），末尾还有一整段
# TTS（分钟级）。当初日志里真正卡死的量级在这里。


@pytest.fixture
def mpt_settings(tmp_path) -> VideoPipelineSettings:
    """让 MoneyPrinterTurbo.available 为真。

    available 只是 install_path/cli.py 的存在性检查（moneyprinter.py:40-42），
    touch 一个空文件就够，不需要真装 MPT。
    """
    mpt = tmp_path / "mpt"
    mpt.mkdir()
    (mpt / "cli.py").touch()
    return VideoPipelineSettings(
        output_base_dir=str(tmp_path / "out"),
        moneyprinter_path=str(mpt),
        pixelle_path="",
    )


def _let_portrait_guard_pass(monkeypatch, tmp_path):
    """放行人脸检测。

    ⚠️ 这是**写锁测试**的权宜之计，不是可以抄走的模板。

    _reject_portrait_materials 是肖像权红线上唯一有效的一层，它自己的文档
    写明「不设开关」：检测器不可用时 fail-closed 拒收全部素材。这里之所以
    要打掉它，只是因为测试用的假 mp4（几个字节）根本过不了检测，打不到
    _save 就验不了写锁。

    任何**不是**在验证写锁/事务边界的测试，都不该复制这一段。要测素材
    筛选本身，请让真实的 portrait_guard 跑起来。
    """
    from xhs_manager.video_pipeline.stages import stage3_materials as m

    monkeypatch.setattr(m, "_reject_portrait_materials", lambda mats: list(mats))


def _fake_clip(tmp_path, name: str) -> str:
    clip = tmp_path / f"{name}.mp4"
    clip.write_bytes(b"not really a video")
    return str(clip)


def _stub_narration(monkeypatch, tmp_path):
    """让整段旁白合成立即返回一条现成音轨。

    build_aligned_narration 在 stage3 里是**函数内导入**，所以要打在
    narration 模块上，打 stage3 的模块属性不生效。
    返回真实存在的路径是为了走「合成成功」那条正路——退路会再去调
    MoneyPrinterTurbo.generate_tts，那就绕开了末尾那条 commit。
    """
    track = tmp_path / "narration.mp3"
    track.write_bytes(b"not really audio")
    monkeypatch.setattr(
        "xhs_manager.video_pipeline.audio.narration.build_aligned_narration",
        lambda scenes, out_dir, settings, **kw: (track, 12.0),
    )


def test_material_search_releases_the_write_lock_between_scenes(
    session_factory, mpt_settings, tmp_path, monkeypatch
):
    """搜第二个场景的素材时，第一条已经入库——这时别的连接必须还能写。

    守 _collect_via_moneyprinter 里 _save() 的 commit。现实中两次搜索之间
    隔着一整次 MPT CLI 调用（十几秒），攥着锁的话 worker 心跳必然超时。
    """
    _let_portrait_guard_pass(monkeypatch, tmp_path)
    _stub_narration(monkeypatch, tmp_path)

    run_id = _seed_script(session_factory, [
        {"scene_id": "s01", "order": 1, "duration": 6,
         "material_hints": ["search:blue abstract"]},
        {"scene_id": "s02", "order": 2, "duration": 6,
         "material_hints": ["search:green abstract"]},
    ])

    probe: dict = {}
    calls: list = []

    def fake_search(self, search_terms, **kw):
        calls.append(search_terms)
        if len(calls) == 2:      # 第二个场景：第一条素材此刻已经 commit 了
            probe.update(_probe_another_connection(session_factory, run_id))
        # 每次只给一条，避免留下余料去给别的场景补位
        return {
            "task_id": "t",
            "materials": [{"path": _fake_clip(tmp_path, f"c{len(calls)}"), "size": 1}],
        }

    monkeypatch.setattr(
        "xhs_manager.video_pipeline.integrations.moneyprinter"
        ".MoneyPrinterTurbo.search_materials",
        fake_search,
    )

    with session_factory() as session:
        run = session.get(VideoPipelineRun, run_id)
        stage3_materials.collect_materials(session, run, mpt_settings)
        session.commit()

    with session_factory() as s:
        clips = s.query(VideoMaterial).filter(
            VideoMaterial.material_type != "audio").count()
    assert clips == 2
    _assert_lock_was_free(probe)


def test_narration_phase_releases_the_lock_before_the_fallback_loop(
    session_factory, mpt_settings, tmp_path, monkeypatch
):
    """MPT 那一段收尾之后，兜底链开始之前，锁必须已经放开。

    守 _collect_via_moneyprinter 末尾那条 commit（它落的是校准过的场景时长
    和整轨旁白）。改回 flush 的话，锁会一路攥到兜底链——而兜底链要给剩下的
    场景逐个生成卡片，又是一段长活。

    第三个场景故意不给搜索词：它拿不到 MPT 素材，会落到兜底链，
    探针就挂在那里。
    """
    _let_portrait_guard_pass(monkeypatch, tmp_path)
    _stub_narration(monkeypatch, tmp_path)

    run_id = _seed_script(session_factory, [
        {"scene_id": "s01", "order": 1, "duration": 4,
         "material_hints": ["search:blue abstract"]},
        {"scene_id": "s02", "order": 2, "duration": 4,
         "material_hints": ["search:green abstract"]},
        {"scene_id": "s03", "order": 3, "duration": 4, "material_hints": []},
    ])

    calls: list = []

    def fake_search(self, search_terms, **kw):
        calls.append(search_terms)
        return {
            "task_id": "t",
            "materials": [{"path": _fake_clip(tmp_path, f"c{len(calls)}"), "size": 1}],
        }

    monkeypatch.setattr(
        "xhs_manager.video_pipeline.integrations.moneyprinter"
        ".MoneyPrinterTurbo.search_materials",
        fake_search,
    )

    probe: dict = {}

    def fake_card(session, script_id, scene, output_dir):
        # 此刻 MPT 那一段已经整个走完（含末尾 commit），兜底链刚开始
        probe.update(_probe_another_connection(session_factory, run_id))
        return VideoMaterial(
            id=new_id(), script_id=script_id, scene_id=scene["scene_id"],
            material_type="text_card", source_tool="fallback",
            local_path=str(tmp_path / f"{scene['scene_id']}.html"),
            license_type="generated", selected=True,
        )

    monkeypatch.setattr(stage3_materials, "_generate_text_card", fake_card)

    with session_factory() as session:
        run = session.get(VideoPipelineRun, run_id)
        stage3_materials.collect_materials(session, run, mpt_settings)
        session.commit()

    _assert_lock_was_free(probe)


# ── Stage 2：选题 + 脚本 ──────────────────────────────────────────
#
# 这是目前唯一一个持锁时长真的能逼近租约的阶段：video_select 的租约是 300 秒，
# 而每个选题要走一次 claude -p 生成脚本，60~120 秒一次，3 个选题就 270 秒。
# 其余阶段的租约（materialize / render 都是 1800）离实际持锁还很远。


def _seed_signals(session_factory, run_id: str, n: int = 3) -> None:
    from xhs_manager.video_pipeline.models import VideoTrendSignal

    with session_factory() as s:
        for i in range(n):
            s.add(VideoTrendSignal(
                id=new_id(), pipeline_run_id=run_id, platform="bilibili",
                source_url=f"https://example.invalid/{i}",
                title=f"信号 {i}", summary="摘要",
                content_digest=f"digest-{i}", heat_score=90 - i,
            ))
        s.commit()


def _seed_run_for_selection(session_factory) -> str:
    with session_factory() as s:
        run = VideoPipelineRun(
            id=new_id(), run_date=date(2026, 9, 22),
            status=PipelineStatus.SELECTING.value, trigger_type="manual",
        )
        s.add(run)
        s.commit()
        run_id = run.id
    _seed_signals(session_factory, run_id)
    return run_id


def _fake_llm(topics: int, on_call):
    """替掉 claude -p 通道。

    第一次调用是选题评分，之后每个选题一次脚本生成。on_call 收到的是
    「这是第几次调用」，探针靠它挑时机。
    """
    calls = {"n": 0}

    def call_structured(prompt, schema, **kw):
        calls["n"] += 1
        on_call(calls["n"])
        if calls["n"] == 1:
            return {"topics": [
                {
                    "rank": i + 1, "title": f"选题 {i + 1}", "angle": "角度",
                    "why_now": "时机", "target_audience": "受众",
                    "estimated_duration": 60,
                    "scores": {"heat": 8, "uniqueness": 8, "visual": 8,
                               "timeliness": 8, "platform_fit": 8},
                    "source_signal_ids": [],
                }
                for i in range(topics)
            ]}
        return {
            "total_duration": 60,
            "scenes": [{"scene_id": "s01", "order": 1, "duration": 60}],
            "bgm_style": "",
            "platform_metadata": {},
        }

    return call_structured


@pytest.fixture
def select_settings(tmp_path) -> VideoPipelineSettings:
    return VideoPipelineSettings(
        output_base_dir=str(tmp_path / "out"),
        topics_per_run=2,
    )


def test_topic_insert_is_committed_before_the_first_script_call(
    session_factory, select_settings, monkeypatch
):
    """选题插完之后、第一次生成脚本之前，锁必须已经放开。

    守 select_topics 里那条「提交而不是 flush」。第 2 次 call_structured 是
    第一个选题的脚本生成——现实中它要跑 60~120 秒，锁攥着的话整个系统写不进去。
    """
    probe: dict = {}
    run_id_box: dict = {}

    def on_call(n):
        if n == 2 and run_id_box:
            probe.update(_probe_another_connection(session_factory, run_id_box["id"]))

    from xhs_manager.video_pipeline.stages import stage2_topics

    run_id = _seed_run_for_selection(session_factory)
    run_id_box["id"] = run_id
    monkeypatch.setattr(stage2_topics, "call_structured", _fake_llm(2, on_call))

    with session_factory() as session:
        run = session.get(VideoPipelineRun, run_id)
        result = stage2_topics.select_topics(session, run, select_settings)
        session.commit()

    assert result["scripts_created"] == 2
    _assert_lock_was_free(probe)


def test_each_script_is_committed_before_the_next_llm_call(
    session_factory, select_settings, monkeypatch
):
    """每个脚本落库之后、下一次 claude -p 之前，锁必须已经放开。

    守脚本生成循环末尾那条 commit。第 3 次 call_structured 是第二个选题的
    脚本生成，那一刻第一个选题的脚本已经入库——3 个选题就是 3 次这样的等待，
    加起来 270 秒，贴着 video_select 那 300 秒的租约。
    """
    probe: dict = {}
    run_id_box: dict = {}

    def on_call(n):
        if n == 3 and run_id_box:
            probe.update(_probe_another_connection(session_factory, run_id_box["id"]))

    from xhs_manager.video_pipeline.stages import stage2_topics

    run_id = _seed_run_for_selection(session_factory)
    run_id_box["id"] = run_id
    monkeypatch.setattr(stage2_topics, "call_structured", _fake_llm(2, on_call))

    with session_factory() as session:
        run = session.get(VideoPipelineRun, run_id)
        result = stage2_topics.select_topics(session, run, select_settings)
        session.commit()

    assert result["scripts_created"] == 2
    _assert_lock_was_free(probe)
