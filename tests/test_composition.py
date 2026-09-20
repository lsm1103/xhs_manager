"""video_pipeline.composition 的单元测试。

重点覆盖两类曾经真实出过问题的地方：
  1. 字幕切分（切在词中间 / 掉出只有一个句号的碎片）
  2. 确定性动画（有动画规则漏了 paused + --t，画面就不可复现）
"""

import re
import shutil
from pathlib import Path

import pytest

from xhs_manager.video_pipeline.composition import (
    SceneMedia,
    build_captions,
    build_composition_html,
    classify_media,
    infer_layout,
    plan_timeline,
    resolve_theme,
    split_caption_text,
    transition_duration,
)
from xhs_manager.video_pipeline.composition.theme import DEFAULT_THEME, THEMES

CSS_PATH = (
    Path(__file__).resolve().parents[1]
    / "src/xhs_manager/video_pipeline/composition/assets/base.css"
)


def _scene(**kw):
    base = {
        "scene_id": "s01",
        "order": 1,
        "duration": 5,
        "visual_desc": "desc",
        "text_overlay": {"main": "主标题", "sub": "副标题", "animation": "pop_in"},
        "transition": "fade",
        "narration": "旁白文本。",
        "bgm_mood": "explain",
        "material_hints": [],
    }
    overlay = kw.pop("text_overlay", None)
    base.update(kw)
    if overlay:
        base["text_overlay"] = overlay
    return base


# ── 字幕切分 ──────────────────────────────────────────────────────


def test_caption_split_prefers_punctuation_over_hard_cut():
    """22 字的句子该按逗号切，而不是按字数硬切。

    回归：句号级切不动时曾直接跳到硬切，切出 '说AI已经能写大' 这种半截词。
    """
    out = split_caption_text("最近有个帖子吵翻了，说AI已经能写大部分代码。")
    assert out == ["最近有个帖子吵翻了，", "说AI已经能写大部分代码。"]


def test_caption_split_never_orphans_trailing_punctuation():
    """不能切出只剩一个句号的碎片。"""
    out = split_caption_text("调查显示，八成七的开发者每天都在用AI编码工具。")
    assert all(c.strip("。，、！？") for c in out)
    assert "。" not in out


def test_caption_split_hard_cuts_when_no_punctuation():
    """完全没有标点的长句必须有硬切兜底，否则会一行糊满屏。"""
    text = "这是一句完全没有任何标点符号的超长句子需要被硬切才能放进竖屏字幕里显示出来"
    out = split_caption_text(text, max_chars=18)
    assert len(out) > 1
    assert all(len(c) <= 18 + 4 for c in out)
    assert "".join(out) == text


def test_caption_split_empty_returns_empty():
    assert split_caption_text("") == []
    assert split_caption_text("   ") == []


def test_captions_tile_the_scene_without_gaps():
    """字幕必须铺满整个场景时长，且首尾严丝合缝。"""
    cues = build_captions("第一句话。第二句话。第三句话。", start=10.0, duration=6.0)
    assert cues
    assert cues[0].start == pytest.approx(10.0)
    assert cues[-1].end == pytest.approx(16.0)
    for a, b in zip(cues, cues[1:]):
        assert a.end == pytest.approx(b.start)


# ── 版面推断 ──────────────────────────────────────────────────────


def test_layout_first_and_last_scene():
    scenes = [_scene() for _ in range(4)]
    assert infer_layout(scenes[0], 0, 4) == "hook"
    assert infer_layout(scenes[-1], 3, 4) == "outro"


def test_layout_detects_stat_quote_bullets_compare():
    mid = lambda ov: infer_layout(_scene(text_overlay=ov), 1, 5)  # noqa: E731
    assert mid({"main": "87%", "sub": "的开发者", "animation": "pop_in"}) == "stat"
    assert mid({"main": "「代码是写给人看的」", "sub": "", "animation": "pop_in"}) == "quote"
    assert mid({"main": "三点", "sub": "快；准；省", "animation": "pop_in"}) == "bullets"
    assert mid({"main": "手写 vs AI", "sub": "", "animation": "pop_in"}) == "compare"


def test_explicit_layout_wins_over_inference():
    s = _scene(layout="quote", text_overlay={"main": "87%", "sub": "", "animation": "counter"})
    assert infer_layout(s, 1, 5) == "quote"


def test_compare_without_two_sides_falls_back_to_statement():
    """推断成 compare 但拆不出左右两边时，必须退回 statement，不能渲染半个空卡片。"""
    tl = plan_timeline([
        _scene(),
        _scene(scene_id="s02", text_overlay={"main": "vs", "sub": "", "animation": "pop_in"}),
        _scene(scene_id="s03"),
    ])
    assert tl.scenes[1].layout == "statement"


# ── 时间轴 ────────────────────────────────────────────────────────


def test_timeline_scenes_are_contiguous():
    tl = plan_timeline([
        _scene(scene_id="s01", duration=5),
        _scene(scene_id="s02", duration=7),
        _scene(scene_id="s03", duration=4),
    ])
    assert [s.start for s in tl.scenes] == [0.0, 5.0, 12.0]
    assert tl.total_duration == 16.0


def test_timeline_handles_string_text_overlay():
    """旧脚本里 text_overlay 可能是纯字符串，不能因此炸掉。"""
    tl = plan_timeline([_scene(text_overlay="就一句话")])
    assert tl.scenes[0].text_main == "就一句话"


# ── 素材 ──────────────────────────────────────────────────────────


def test_classify_media_by_suffix():
    assert classify_media("/a/b.mp4") == SceneMedia("b.mp4", "video")
    assert classify_media("/a/b.PNG") == SceneMedia("b.PNG", "image")
    assert classify_media("/a/b.mp3") is None


def test_transition_duration_glitch_is_shorter():
    assert transition_duration("glitch") < transition_duration("fade")
    assert transition_duration("none") == 0.0


# ── 主题 ──────────────────────────────────────────────────────────


def test_resolve_theme_falls_back_on_unknown_name():
    assert resolve_theme("tech_night") is THEMES["tech_night"]
    assert resolve_theme("does-not-exist") is DEFAULT_THEME
    assert resolve_theme(None) is DEFAULT_THEME


# ── HTML 产物 ─────────────────────────────────────────────────────


def _build(scenes, **kw):
    html, tl = build_composition_html(scenes, media_lookup=lambda sid: None, **kw)
    return html, tl


def test_html_contains_every_scene_and_the_shell():
    scenes = [_scene(scene_id=f"s{i:02d}", order=i) for i in range(1, 6)]
    html, tl = _build(scenes)
    for s in tl.scenes:
        assert f'id="{s.scene_id}"' in html
    assert "__seek" in html
    assert 'class="progress"' in html
    assert 'class="captions"' in html


def test_html_escapes_text_so_scripts_cannot_be_injected():
    """脚本文案来自 LLM，必须转义，不能让 <script> 直接进 DOM。"""
    scenes = [_scene(text_overlay={
        "main": "<script>alert(1)</script>", "sub": "a & b", "animation": "pop_in",
    })]
    html, _ = _build(scenes)
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "a &amp; b" in html


def test_html_has_no_unsubstituted_placeholders():
    """base.css 的占位符必须全部被替换掉。"""
    html, _ = _build([_scene()])
    assert "${" not in html


def test_image_material_gets_ken_burns():
    scenes = [_scene()]
    html, _ = build_composition_html(
        scenes, media_lookup=lambda sid: SceneMedia("pic.jpg", "image"),
    )
    assert "m-kenburns" in html
    assert "pic.jpg" in html


def test_video_material_is_muted_and_preloaded():
    """无头浏览器里不 muted 的 video 不会加载，preload 保证首帧就绪。"""
    html, _ = build_composition_html(
        [_scene()], media_lookup=lambda sid: SceneMedia("clip.mp4", "video"),
    )
    assert "<video" in html and "muted" in html and 'preload="auto"' in html


def test_composition_does_not_autoplay_without_preview_hash():
    """渲染器用不带 hash 的 file:// URL 打开，页面必须保持静止，
    否则墙钟驱动的 rAF 会和逐帧 seek 抢同一个 --t。"""
    html, _ = _build([_scene()])
    assert "location.hash === '#preview'" in html


# ── 确定性动画（静态检查）────────────────────────────────────────


def test_every_animated_rule_is_time_driven():
    """凡是自己声明 animation-name 的规则，都必须同时 paused + 由 --t 定位。

    回归：.backdrop 曾漏掉这两行，于是背景按截图速度漂移，
    同一时间点两次渲染的画面不一致。
    """
    css = CSS_PATH.read_text(encoding="utf-8")
    # 去掉注释，避免注释里的示例代码被当成规则
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    offenders = []
    for selector, body in re.findall(r"([^{}]*)\{([^{}]*)\}", css):
        selector = selector.strip()
        if selector.startswith("@") or "from" in selector or "%" in selector:
            continue
        if "animation-name" not in body:
            continue

        # 只声明 animation-name 的是「修饰类」（.m-pop-in / .x-fade 这些），
        # 自己不带时长和延迟，必须配 .m 一起用——由下面那个测试保证配对。
        self_contained = (
            "animation-duration" in body or "animation-delay" in body
        )
        if not self_contained:
            continue

        if "animation-play-state: paused" not in body or "--t" not in body:
            offenders.append(selector)

    assert not offenders, f"这些规则的动画没有被 --t 驱动，会导致渲染不可复现: {offenders}"


def test_every_animation_modifier_is_paired_with_the_m_base_class():
    """产物里凡是带 m-* / x-* 修饰类的元素，都必须同时带 .m。

    修饰类只提供 animation-name，时长和 --t 定位全靠 .m。
    漏了 .m 的元素会退回浏览器默认（墙钟驱动），渲染就不可复现了。
    """
    scenes = [
        _scene(scene_id="s01", order=1, transition="zoom_in",
               text_overlay={"main": "87%", "sub": "的人", "animation": "counter"}),
        _scene(scene_id="s02", order=2, transition="wipe",
               text_overlay={"main": "打字机", "sub": "副标题", "animation": "typewriter"}),
        _scene(scene_id="s03", order=3, transition="glitch",
               text_overlay={"main": "要点", "sub": "甲；乙；丙", "animation": "pop_in"}),
        _scene(scene_id="s04", order=4, transition="fade",
               text_overlay={"main": "收尾", "sub": "关注我", "animation": "slide_up"}),
    ]
    html, _ = build_composition_html(
        scenes, media_lookup=lambda sid: SceneMedia("pic.jpg", "image"),
    )

    bad = []
    for class_attr in re.findall(r'class="([^"]*)"', html):
        classes = class_attr.split()
        has_modifier = any(c.startswith(("m-", "x-")) for c in classes)
        if has_modifier and "m" not in classes:
            bad.append(class_attr)

    assert not bad, f"这些元素带了动画修饰类却没有 .m 基类: {bad}"


# ── 端到端渲染（有浏览器才跑）────────────────────────────────────


def _chrome() -> str:
    from xhs_manager.video_pipeline.integrations.renderer import detect_chrome_path

    return detect_chrome_path()


@pytest.mark.skipif(not _chrome(), reason="没有可用的 Chrome/Chromium")
def test_same_timestamp_renders_identically_regardless_of_wall_clock():
    """同一时间点重复渲染必须字节一致，且刻意让墙钟多走一段也不能变。

    这是整套 --t 驱动方案的核心保证，也是唯一能抓到
    「某条规则忘了 paused」的测试。
    """
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    scenes = [
        _scene(scene_id="s01", order=1, duration=4,
               text_overlay={"main": "开场", "sub": "副标题", "animation": "slide_up"}),
        _scene(scene_id="s02", order=2, duration=4, transition="zoom_in",
               text_overlay={"main": "87%", "sub": "的人", "animation": "counter"}),
    ]
    html, _ = _build(scenes)

    tmp = Path(shutil.get_archive_formats() and "/tmp") / "xhs_determinism_test.html"
    tmp.write_text(html, encoding="utf-8")

    def shoot(settle_ms: int) -> list[bytes]:
        frames = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, executable_path=_chrome())
            try:
                page = browser.new_page(
                    viewport={"width": 1080, "height": 1920}, device_scale_factor=1,
                )
                page.goto(tmp.resolve().as_uri(), wait_until="load", timeout=60000)
                page.wait_for_timeout(500)
                for t in (0.5, 2.0, 5.5):
                    page.evaluate("(t) => window.__seek(t)", t)
                    if settle_ms:
                        page.wait_for_timeout(settle_ms)
                    frames.append(page.screenshot())
            finally:
                browser.close()
        return frames

    try:
        a = shoot(0)
        b = shoot(300)
    finally:
        tmp.unlink(missing_ok=True)

    assert a == b, "同一时间点两次渲染不一致 —— 有动画仍在被墙钟驱动"
    assert len(set(a)) == len(a), "不同时间点画面相同 —— 动画根本没随 --t 推进"


# ── Stage4 胶水层 ─────────────────────────────────────────────────


class _FakeMaterial:
    def __init__(self, scene_id, local_path, material_type="video_clip"):
        self.scene_id = scene_id
        self.local_path = local_path
        self.material_type = material_type


class _FakeScript:
    def __init__(self, scenes):
        self.scenes = scenes


def _settings():
    from xhs_manager.video_pipeline.config import VideoPipelineSettings

    return VideoPipelineSettings()


def test_stage4_picks_the_first_visual_material_and_skips_audio():
    """每个场景取第一个非音频素材当背景；旁白音轨不能被当成画面。"""
    from xhs_manager.video_pipeline.stages.stage4_compose import _build_composition_html

    script = _FakeScript([_scene(scene_id="s01"), _scene(scene_id="s02", order=2)])
    material_map = {
        "s01": [
            _FakeMaterial("s01", "/m/narration.mp3", "audio"),
            _FakeMaterial("s01", "/m/clip.mp4"),
        ],
        "s02": [_FakeMaterial("s02", "/m/pic.jpg", "stock_photo")],
    }

    html = _build_composition_html(script, material_map, _settings())
    assert "assets/clip.mp4" in html
    assert "assets/pic.jpg" in html
    assert "narration.mp3" not in html


def test_stage4_scene_without_material_still_renders():
    """没拿到素材的场景不能整个消失，退回渐变底就行。"""
    from xhs_manager.video_pipeline.stages.stage4_compose import _build_composition_html

    script = _FakeScript([_scene(scene_id="s01")])
    html = _build_composition_html(script, {}, _settings())
    assert 'id="s01"' in html
    assert "<video" not in html


def test_stage4_honours_resolution_setting():
    from xhs_manager.video_pipeline.stages.stage4_compose import _build_composition_html

    settings = _settings()
    settings.render_resolution = "720x1280"
    html = _build_composition_html(_FakeScript([_scene()]), {}, settings)
    assert "720px" in html and "1280px" in html


# ── 手写脚本入口 ──────────────────────────────────────────────────


def _write(tmp_path, obj):
    import json

    f = tmp_path / "script.json"
    f.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    return f


def test_load_script_file_accepts_bare_scene_array(tmp_path):
    from xhs_manager.video_pipeline.seed import load_script_file

    data = load_script_file(_write(tmp_path, [_scene()]))
    assert len(data["scenes"]) == 1


def test_load_script_file_fills_optional_defaults(tmp_path):
    """bgm_mood / material_hints 可以不写，但后续阶段一定要读得到。"""
    from xhs_manager.video_pipeline.seed import load_script_file

    scene = _scene()
    scene.pop("bgm_mood")
    scene.pop("material_hints")
    data = load_script_file(_write(tmp_path, {"scenes": [scene]}))
    assert data["scenes"][0]["bgm_mood"] == "explain"
    assert data["scenes"][0]["material_hints"] == []


def test_load_script_file_rejects_missing_fields_early(tmp_path):
    """缺字段要在入口就报，而不是等流水线跑到一半才崩。"""
    from xhs_manager.video_pipeline.seed import load_script_file

    scene = _scene()
    del scene["narration"]
    with pytest.raises(ValueError, match="narration"):
        load_script_file(_write(tmp_path, {"scenes": [scene]}))


def test_load_script_file_rejects_bad_duration(tmp_path):
    from xhs_manager.video_pipeline.seed import load_script_file

    with pytest.raises(ValueError, match="duration"):
        load_script_file(_write(tmp_path, {"scenes": [_scene(duration=0)]}))


def test_load_script_file_rejects_empty_scenes(tmp_path):
    from xhs_manager.video_pipeline.seed import load_script_file

    with pytest.raises(ValueError, match="scenes"):
        load_script_file(_write(tmp_path, {"scenes": []}))


# ── 渲染器工具探测 ────────────────────────────────────────────────


def test_detect_ffmpeg_rejects_builds_without_libx264(monkeypatch, tmp_path):
    """裁剪版 ffmpeg（比如 Playwright 自带那份）存在但编不了 H.264。

    回归：只判断文件存在，导致 available() 报 True，
    结果截了 700 多帧才在最后一步 ffmpeg 报 Unknown encoder。
    """
    import subprocess

    from xhs_manager.video_pipeline.integrations import renderer

    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\n")

    monkeypatch.delenv("XHS_FFMPEG_PATH", raising=False)
    monkeypatch.setattr(renderer.shutil, "which", lambda _: str(fake))
    monkeypatch.setattr(renderer, "FFMPEG_CANDIDATES", ())
    renderer.ffmpeg_supports_h264.cache_clear()

    def fake_run(cmd, **kw):
        # 模拟只有 vp8 的裁剪版
        return subprocess.CompletedProcess(cmd, 0, stdout="V..... libvpx_vp8", stderr="")

    monkeypatch.setattr(renderer.subprocess, "run", fake_run)
    assert renderer.detect_ffmpeg() == ""

    def full_run(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout="V..... libx264", stderr="")

    monkeypatch.setattr(renderer.subprocess, "run", full_run)
    renderer.ffmpeg_supports_h264.cache_clear()
    assert renderer.detect_ffmpeg() == str(fake)

    renderer.ffmpeg_supports_h264.cache_clear()


def test_explicit_ffmpeg_path_is_trusted_without_probing(monkeypatch):
    """显式指定就按用户说的来，不做能力检查。"""
    from xhs_manager.video_pipeline.integrations import renderer

    monkeypatch.setenv("XHS_FFMPEG_PATH", "/custom/ffmpeg")
    assert renderer.detect_ffmpeg() == "/custom/ffmpeg"


def test_detect_chrome_prefers_env_override(monkeypatch):
    from xhs_manager.video_pipeline.integrations import renderer

    monkeypatch.setenv("XHS_CHROME_PATH", "/custom/chrome")
    assert renderer.detect_chrome_path() == "/custom/chrome"


# ── 截图特写版面 ──────────────────────────────────────────────────


def _png(path: Path, width: int, height: int) -> Path:
    """写一个只有头部合法的最小 PNG——probe 只读前 24 字节，够用。"""
    import struct
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13) + b"IHDR" + ihdr + b"\x00\x00\x00\x00"
    )
    return path


def test_probe_image_size_reads_png_header(tmp_path):
    from xhs_manager.video_pipeline.composition.media import probe_image_size

    assert probe_image_size(_png(tmp_path / "a.png", 1533, 618)) == (1533, 618)


def test_probe_image_size_returns_none_for_non_image(tmp_path):
    from xhs_manager.video_pipeline.composition.media import probe_image_size

    junk = tmp_path / "a.png"
    junk.write_bytes(b"not an image at all, but long enough to read 32 bytes ok")
    assert probe_image_size(junk) is None
    assert probe_image_size(tmp_path / "missing.png") is None


def test_classify_media_carries_image_size(tmp_path):
    media = classify_media(_png(tmp_path / "shot.png", 800, 400))
    assert (media.width, media.height) == (800, 400)


def test_focus_makes_it_a_screenshot_scene():
    """作者量好了要框哪块，就是明确要截图特写——比"它排第几个"更能说明意图。"""
    scene = _scene(focus=[{"at": 1, "rect": [0, 0, 10, 10]}])
    assert infer_layout(scene, 0, 3) == "screenshot"     # 首个场景也不再当 hook
    assert infer_layout(scene, 2, 3) == "screenshot"     # 末个场景也不再当 outro


def test_focus_holds_until_the_next_one():
    """只给 at 不给 hold 时，停留到下一个特写出现；最后一个停到场景结束前。"""
    tl = plan_timeline([_scene(
        duration=10, layout="screenshot",
        focus=[{"at": 1, "rect": [0, 0, 10, 10]}, {"at": 4, "rect": [0, 0, 10, 10]}],
    )])
    f1, f2 = tl.scenes[0].focus
    assert (f1.at, f1.duration) == (1.0, 3.0)
    assert f2.at == 4.0 and f2.duration == pytest.approx(5.7)


def test_focus_entries_are_sorted_and_bad_ones_dropped():
    tl = plan_timeline([_scene(
        duration=9, layout="screenshot",
        focus=[
            {"at": 5, "rect": [0, 0, 10, 10]},
            {"at": 1, "rect": [0, 0, 10, 10]},
            {"at": 2, "rect": [0, 0, 0, 10]},     # 宽度 0
            {"at": 3, "rect": [1, 2, 3]},         # 少一个数
            {"at": 4},                            # 没有 rect
            "不是字典",
        ],
    )])
    assert [f.at for f in tl.scenes[0].focus] == [1.0, 5.0]


def test_screenshot_layout_draws_rings_and_zoom_cards(tmp_path):
    media = classify_media(_png(tmp_path / "ui.png", 1000, 500))
    scenes = [_scene(
        duration=8, layout="screenshot",
        focus=[{"at": 1, "rect": [100, 50, 200, 25], "label": "设备", "note": "下拉切"}],
    )]
    html, _ = build_composition_html(scenes, media_lookup=lambda sid: media)

    # 高亮框按比例贴在图上：100/1000=10%，50/500=10%，200/1000=20%，25/500=5%
    assert 'left:10.000%;top:10.000%;width:20.000%;height:5.000%' in html
    # 放大倍数由框宽决定（1/0.2=5 倍），框心平移到卡片正中
    assert "width:500.000%" in html
    assert "translate(-20.000%,-12.500%)" in html
    assert "设备" in html and "下拉切" in html
    # 同一张图已经在前景完整出现，背景只做糊底，不再 Ken Burns。
    # 断言 class 属性而不是裸类名：内联 CSS 里有同名选择器，裸类名永远命中。
    assert 'class="still is-blurred"' in html
    assert 'class="still m m-kenburns' not in html


def test_screenshot_layout_accepts_fractions_without_image_size():
    """探不出源图尺寸时，比例写法仍然成立——这是像素写法失效后的退路。"""
    scenes = [_scene(
        duration=6, layout="screenshot",
        focus=[{"at": 1, "rect": [0.1, 0.2, 0.3, 0.05], "label": "框"}],
    )]
    html, _ = build_composition_html(
        scenes, media_lookup=lambda sid: SceneMedia("ui.png", "image"),
    )
    assert 'left:10.000%;top:20.000%;width:30.000%;height:5.000%' in html


def test_screenshot_layout_drops_pixel_focus_without_image_size():
    """像素框 + 尺寸未知 = 没有参照系，只能丢掉，不能瞎画。"""
    scenes = [_scene(
        duration=6, layout="screenshot",
        focus=[{"at": 1, "rect": [100, 50, 200, 25], "label": "框"}],
    )]
    html, _ = build_composition_html(
        scenes, media_lookup=lambda sid: SceneMedia("ui.png", "image"),
    )
    assert 'class="shot-ring' not in html
    assert 'class="shot-frame' in html      # 整图还是要出


def test_screenshot_layout_without_media_falls_back_to_statement():
    scenes = [_scene(layout="screenshot", focus=[{"at": 1, "rect": [0, 0, 1, 1]}])]
    html, _ = build_composition_html(scenes, media_lookup=lambda sid: None)
    assert 'class="shot-frame' not in html
    assert "主标题" in html


# ── 长截图滚动 ────────────────────────────────────────────────────


def test_scroll_key_makes_it_a_scroll_scene():
    assert infer_layout(_scene(scroll={"end": 0.4}), 1, 3) == "scroll"


def test_scroll_travel_stops_at_the_bottom_edge():
    """行程要按可见比例夹住，不能滚出底边——滚过头是一整屏空白。"""
    from xhs_manager.video_pipeline.composition.layouts import scroll_travel
    from xhs_manager.video_pipeline.composition.timeline import ScrollSpec

    # 视窗 aspect=2，图 1000x4000：可见 = 1000/(2*4000) = 12.5%，最多走 87.5%
    media = SceneMedia("a.png", "image", width=1000, height=4000)
    assert scroll_travel(media, ScrollSpec(aspect=2.0)) == pytest.approx(0.875)
    assert scroll_travel(media, ScrollSpec(end=0.4, aspect=2.0)) == pytest.approx(0.4)
    assert scroll_travel(media, ScrollSpec(end=0.99, aspect=2.0)) == pytest.approx(0.875)


def test_scroll_travel_falls_back_without_image_size():
    from xhs_manager.video_pipeline.composition.layouts import (
        SCROLL_FALLBACK_END,
        scroll_travel,
    )
    from xhs_manager.video_pipeline.composition.timeline import ScrollSpec

    blind = SceneMedia("a.png", "image")
    assert scroll_travel(blind, ScrollSpec()) == SCROLL_FALLBACK_END
    assert scroll_travel(blind, ScrollSpec(end=0.3)) == pytest.approx(0.3)


def test_scroll_layout_emits_travel_and_window_chrome(tmp_path):
    media = classify_media(_png(tmp_path / "page.png", 1000, 4000))
    scenes = [_scene(
        duration=9, layout="scroll", window="github.com/lsm1103/quick_logcat",
        scroll={"end": 0.4, "aspect": 2.0},
    )]
    html, _ = build_composition_html(scenes, media_lookup=lambda sid: media)

    assert "--travel:-40.000%" in html
    assert 'aspect-ratio:2.0000' in html
    assert "github.com/lsm1103/quick_logcat" in html
    assert 'class="win-bar"' in html
    # 画面一直在动，高亮框跟不上，所以滚动版面不出框
    assert 'class="shot-ring' not in html


def test_scroll_layout_shows_zoom_cards_but_no_rings(tmp_path):
    media = classify_media(_png(tmp_path / "page.png", 1000, 4000))
    scenes = [_scene(
        duration=9, layout="scroll", scroll={"end": 0.4},
        focus=[{"at": 1.5, "rect": [100, 200, 200, 50], "label": "点个 Star"}],
    )]
    html, _ = build_composition_html(scenes, media_lookup=lambda sid: media)
    assert 'class="shot-inset' in html and "点个 Star" in html
    assert 'class="shot-ring' not in html


def test_scroll_layout_without_media_falls_back_to_statement():
    scenes = [_scene(layout="scroll", scroll={"end": 0.4})]
    html, _ = build_composition_html(scenes, media_lookup=lambda sid: None)
    assert 'class="shot-frame' not in html and "主标题" in html


# ── 终端 ──────────────────────────────────────────────────────────


def test_terminal_key_makes_it_a_terminal_scene():
    assert infer_layout(_scene(terminal={"output": ["ok"]}), 1, 3) == "terminal"


def test_terminal_command_defaults_to_the_main_text():
    tl = plan_timeline([_scene(
        layout="terminal", text_overlay={"main": "npx quick-logcat", "sub": ""},
    )])
    assert tl.scenes[0].terminal.command == "npx quick-logcat"
    assert tl.scenes[0].terminal.prompt == "$"


def test_terminal_layout_types_the_command_then_prints_output():
    scenes = [_scene(
        duration=8, layout="terminal",
        text_overlay={"main": "npx quick-logcat", "sub": "本地起个 Node 服务。"},
        terminal={"output": ["[server] http://localhost:5174"]},
    )]
    html, tl = build_composition_html(scenes, media_lookup=lambda sid: None)

    assert "m-typewriter" in html
    assert "--steps:16" in html            # 命令 16 个字符，按字数走 steps
    assert 'class="term-caret"' in html
    assert "[server] http://localhost:5174" in html
    # 输出必须等命令敲完才出现，否则像是命令还没打完就有了回显
    out_at = float(re.search(r'class="term-out[^"]*"\s+style="--s:([\d.]+)', html).group(1))
    cmd_at = float(re.search(r'class="m m-typewriter"\s+style="--s:([\d.]+)', html).group(1))
    assert out_at > cmd_at


def test_terminal_layout_without_a_command_falls_back_to_statement():
    scenes = [_scene(
        layout="terminal", text_overlay={"main": "", "sub": "只有副标题"},
        terminal={"output": ["ok"]},
    )]
    html, _ = build_composition_html(scenes, media_lookup=lambda sid: None)
    assert 'class="term-win' not in html


def test_blurred_backdrop_covers_all_card_layouts(tmp_path):
    """这几个版面前景都有主体卡片，背景里的图只当底纹，不能再 Ken Burns。"""
    media = classify_media(_png(tmp_path / "a.png", 900, 500))
    for layout, extra in [
        ("screenshot", {"focus": [{"at": 1, "rect": [0, 0, 10, 10]}]}),
        ("scroll", {"scroll": {"end": 0.3}}),
        ("terminal", {"terminal": {"command": "ls"}}),
    ]:
        html, _ = build_composition_html(
            [_scene(layout=layout, **extra)], media_lookup=lambda sid: media,
        )
        assert 'class="still is-blurred"' in html, layout
        assert 'class="still m m-kenburns' not in html, layout
