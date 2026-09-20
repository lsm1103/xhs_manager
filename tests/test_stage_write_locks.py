"""长阶段不能攥着 SQLite 的写锁不放。

SQLite 全库只有一把写锁：从阶段里第一条 INSERT 到 commit 之间，别的连接
一个字都写不进去。而渲染一支片子要好几分钟——worker 的心跳想 UPDATE
work_items 续租，只能等满 busy_timeout 然后报 database is locked。
心跳一停，租约过期，工作项就会被另一个 worker 当成「过期」重新领走，
同一支片子渲两遍。

所以规矩是：阶段里的写只在真正写的那一瞬间持锁，长活（渲染、下载、
claude -p、上传）一律在事务外面跑。
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
