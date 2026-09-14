from xhs_manager.video_pipeline.integrations.renderer import PAGE_SEEK_JS, SEEK_JS


def test_page_seek_prefers_composition_protocol() -> None:
    assert "typeof window.__seekToTime === 'function'" in PAGE_SEEK_JS
    assert "window.__seekToTime(elapsed)" in PAGE_SEEK_JS


def test_page_seek_keeps_legacy_clip_fallback() -> None:
    assert SEEK_JS in PAGE_SEEK_JS
    assert "legacySeek(elapsed)" in PAGE_SEEK_JS
