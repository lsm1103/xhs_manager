"""配音并入主服务之后的接口测试。

这些测试不碰真的 TTS：engine 里的生成函数被替换掉。
要验的是路由、鉴权、落库和文件边界，不是模型本身。
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from xhs_manager.api import create_app
from xhs_manager.models import TtsGeneration

HEADERS = {"X-Console-Action": "1"}


@pytest.fixture
def tts_dirs(tmp_path, monkeypatch):
    """把产物目录指到临时目录，并关掉开机预热。

    预热是个后台线程，跑起来会让「哪个模型就绪」变得不确定。
    """
    audio = tmp_path / "audio"
    refs = tmp_path / "refs"
    audio.mkdir()
    refs.mkdir()
    monkeypatch.setenv("TTS_STUDIO_AUDIO_DIR", str(audio))
    monkeypatch.setenv("TTS_STUDIO_UPLOAD_DIR", str(refs))
    monkeypatch.setenv("TTS_STUDIO_AUTOLOAD", "[]")
    return {"audio": audio, "refs": refs}


@pytest.fixture
def fake_engine(monkeypatch):
    """把真的合成换成写一个小文件，其余照常走。"""
    from xhs_manager.tts_studio import api as tts_api

    def gen(text, out, *args, **kwargs):
        out.write_bytes(b"ID3fake-audio-bytes")
        return 0.42

    monkeypatch.setattr(tts_api.engine, "gen_edge", gen)
    monkeypatch.setattr(tts_api.engine, "probe", lambda p: (1.25, 24000))
    monkeypatch.setattr(tts_api.engine, "extract_waveform", lambda p, **k: [0.1, 0.8, 0.3])


@pytest.fixture
def tts_client(engine, settings, tts_dirs):
    with TestClient(create_app(settings=settings, engine=engine)) as c:
        yield c


@pytest.fixture
def ready_edge(tts_client):
    """edge 没有模型要加载，load 只是一次可用性检查。"""
    r = tts_client.post("/tts/api/models/edge/load", headers=HEADERS)
    assert r.status_code == 200 and r.json()["status"] == "ready"
    return tts_client


def test_models_are_listed_with_their_real_loading_semantics(tts_client):
    """四个模型的加载代价差三个数量级，接口要如实区分，不能抹平成一个布尔值。"""
    models = {m["id"]: m for m in tts_client.get("/tts/api/models").json()["models"]}
    assert set(models) == {"edge", "voxcpm2", "indextts2", "omnivoice"}
    assert models["edge"]["kind"] == "stateless"
    assert models["edge"]["resident"] is False
    assert models["indextts2"]["resident"] is True      # 常驻子进程，卸载是真杀进程
    assert models["indextts2"]["supports_ref"] is True


@pytest.mark.parametrize("path,method", [
    ("/tts/api/models/edge/load", "post"),
    ("/tts/api/models/edge/unload", "post"),
    ("/tts/api/generate", "post"),
    ("/tts/api/upload-ref", "post"),
    ("/tts/api/generations/whatever", "delete"),
])
def test_write_endpoints_need_the_console_header(tts_client, path, method):
    """和控制台同一道防线：跨源页面发不出自定义头。"""
    call = getattr(tts_client, method)
    resp = call(path) if method == "delete" else call(path, json={})
    assert resp.status_code == 403


def test_generating_writes_a_record_and_a_file(ready_edge, fake_engine, tts_dirs,
                                               session_factory):
    resp = ready_edge.post("/tts/api/generate", headers=HEADERS, json={
        "model_id": "edge", "text": "把配音并进管理服务。", "voice": "zh-CN-XiaoxiaoNeural",
        "speed": 1.1,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["duration"] == 1.25
    assert body["rtf"] == round(0.42 / 1.25, 3)
    assert body["waveform"] == [0.1, 0.8, 0.3]
    # 相对路径：视频流水线把它拼在自己的基址后面，不能带前缀
    assert body["audio_url"] == f"/audio/{body['id']}.mp3"
    assert (tts_dirs["audio"] / f"{body['id']}.mp3").is_file()

    with session_factory() as s:
        rec = s.get(TtsGeneration, body["id"])
        assert rec.status == "ok" and rec.model_id == "edge"
        assert rec.params["speed"] == 1.1      # 语速要真的存下来，不然事后没法复现


def test_generating_with_an_unloaded_model_is_refused(tts_client, fake_engine):
    resp = tts_client.post("/tts/api/generate", headers=HEADERS,
                           json={"model_id": "edge", "text": "还没加载"})
    assert resp.status_code == 400
    assert "未就绪" in resp.json()["detail"]


def test_empty_text_is_refused(ready_edge, fake_engine):
    resp = ready_edge.post("/tts/api/generate", headers=HEADERS,
                           json={"model_id": "edge", "text": "   "})
    assert resp.status_code == 400


def test_indextts_without_a_reference_is_refused(tts_client):
    """IndexTTS-2 没有参考音频根本跑不了，要在生成之前就挡住。"""
    # 不真的起 worker：直接把状态摆成 ready
    tts_client.app.state.tts_registry.get("indextts2").status = "ready"

    resp = tts_client.post("/tts/api/generate", headers=HEADERS,
                           json={"model_id": "indextts2", "text": "没有参考音频"})
    assert resp.status_code == 400
    assert "参考音频" in resp.json()["detail"]


def test_a_failed_generation_is_still_recorded(ready_edge, monkeypatch, session_factory):
    """失败也要留痕。原因只写进日志的话，事后没人查得到。"""
    from xhs_manager.tts_studio import api as tts_api

    def boom(*a, **k):
        raise RuntimeError("模型炸了")

    monkeypatch.setattr(tts_api.engine, "gen_edge", boom)
    resp = ready_edge.post("/tts/api/generate", headers=HEADERS,
                           json={"model_id": "edge", "text": "会失败的一句"})
    assert resp.status_code == 500

    with session_factory() as s:
        rec = s.query(TtsGeneration).one()
        assert rec.status == "error" and "模型炸了" in rec.error


def test_history_filters_by_model(ready_edge, fake_engine):
    for text in ("第一句", "第二句"):
        ready_edge.post("/tts/api/generate", headers=HEADERS,
                        json={"model_id": "edge", "text": text})
    assert len(ready_edge.get("/tts/api/generations").json()["items"]) == 2
    assert ready_edge.get("/tts/api/generations?model_id=voxcpm2").json()["items"] == []


def test_deleting_removes_the_record_and_the_audio(ready_edge, fake_engine, tts_dirs):
    gid = ready_edge.post("/tts/api/generate", headers=HEADERS,
                          json={"model_id": "edge", "text": "删我"}).json()["id"]
    audio = tts_dirs["audio"] / f"{gid}.mp3"
    assert audio.is_file()

    assert ready_edge.delete(f"/tts/api/generations/{gid}", headers=HEADERS).status_code == 200
    assert not audio.exists()
    assert ready_edge.get("/tts/api/generations").json()["items"] == []
    assert ready_edge.delete(f"/tts/api/generations/{gid}", headers=HEADERS).status_code == 404


def test_deleting_never_follows_a_path_outside_the_audio_dir(ready_edge, fake_engine,
                                                             tmp_path, session_factory):
    """库里存的是绝对路径。万一它指向别处，删记录不能顺手把那个文件删了。"""
    outsider = tmp_path / "别人的文件.mp3"
    outsider.write_bytes(b"not mine")

    gid = ready_edge.post("/tts/api/generate", headers=HEADERS,
                          json={"model_id": "edge", "text": "改路径"}).json()["id"]
    with session_factory() as s:
        s.get(TtsGeneration, gid).audio_path = str(outsider)
        s.commit()

    assert ready_edge.delete(f"/tts/api/generations/{gid}", headers=HEADERS).status_code == 200
    assert outsider.exists()          # 记录没了，别人的文件还在


def test_audio_is_served_and_the_route_is_not_a_file_reader(ready_edge, fake_engine,
                                                            tts_dirs):
    gid = ready_edge.post("/tts/api/generate", headers=HEADERS,
                          json={"model_id": "edge", "text": "放出来"}).json()["id"]
    assert ready_edge.get(f"/tts/audio/{gid}.mp3").status_code == 200

    (tts_dirs["audio"] / "secret.env").write_bytes(b"TOKEN=1")
    assert ready_edge.get("/tts/audio/secret.env").status_code == 403
    assert ready_edge.get("/tts/audio/nope.mp3").status_code == 404
    # 名字里的路径成分一律丢掉，落不到目录外面
    assert ready_edge.get("/tts/audio/..%2F..%2Fetc%2Fpasswd").status_code in (403, 404)


def test_uploading_a_reference_only_accepts_audio(ready_edge, tts_dirs):
    bad = ready_edge.post("/tts/api/upload-ref", headers=HEADERS,
                          files={"file": ("payload.sh", b"rm -rf /", "text/plain")})
    assert bad.status_code == 400
    assert not (tts_dirs["refs"] / "payload.sh").exists()


def test_reference_audio_is_listed(ready_edge, tts_dirs, monkeypatch):
    from xhs_manager.tts_studio import api as tts_api

    monkeypatch.setattr(tts_api.engine, "probe", lambda p: (3.5, 22050))
    (tts_dirs["refs"] / "声音.wav").write_bytes(b"RIFFfake")
    items = ready_edge.get("/tts/api/refs").json()["items"]
    assert [i["name"] for i in items] == ["声音.wav"]
    assert items[0]["url"] == "/tts/refs/声音.wav"


# ── 老库搬运 ────────────────────────────────────────────────


def test_legacy_history_is_imported_once(tmp_path, settings, engine, monkeypatch):
    """合并前的 56 条历史不能丢，重复跑也不能变成 112 条。"""
    from xhs_manager.tts_studio import import_legacy as mod

    legacy = tmp_path / "old.db"
    conn = sqlite3.connect(legacy)
    conn.execute("""create table generations (
        id text primary key, model_id text, text text, voice text, ref_audio text,
        params text, audio_path text, duration real, elapsed real, rtf real,
        sample_rate integer, file_size integer, waveform text, status text,
        error text, created_at text)""")
    conn.execute(
        "insert into generations values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        ("old-1", "indextts2", "老记录", None, "/tmp/r.wav", '{"speed": 1.0}',
         "/tmp/a.wav", 2.0, 4.0, 2.0, 22050, 100, "[0.2]", "ok", None,
         "2026-09-01T10:00:00+00:00"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("xhs_manager.config.get_settings", lambda: settings)
    monkeypatch.setattr(mod, "create_db_engine", lambda url: engine)

    first = mod.import_legacy(str(legacy))
    second = mod.import_legacy(str(legacy))
    assert first == {"found": 1, "imported": 1, "skipped": 0}
    assert second == {"found": 1, "imported": 0, "skipped": 1}

    from xhs_manager.db import create_session_factory
    with create_session_factory(engine)() as s:
        rec = s.get(TtsGeneration, "old-1")
        assert rec.model_id == "indextts2"
        assert rec.params == {"speed": 1.0}      # JSON 列要还原成 dict，不能留字符串


def test_importing_from_a_missing_legacy_db_is_a_no_op(tmp_path):
    from xhs_manager.tts_studio.import_legacy import import_legacy

    assert import_legacy(str(tmp_path / "不存在.db")) == {
        "found": 0, "imported": 0, "skipped": 0}
