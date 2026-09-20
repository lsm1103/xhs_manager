"""合成 → MP4。复用 video_pipeline 的 HtmlVideoRenderer（Playwright 逐帧 + ffmpeg）。"""
import json, logging, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from xhs_manager.video_pipeline.integrations.renderer import HtmlVideoRenderer

logging.basicConfig(level=logging.INFO, format="%(message)s")

HERE = Path(__file__).parent
tl = json.loads((HERE / "timeline.json").read_text())
out = HERE / "quick-logcat.mp4"

r = HtmlVideoRenderer(width=1080, height=1920, fps=30)
assert r.available(), "Chrome 或 ffmpeg 不可用"
p = r.render(HERE / "index.html", out, duration=tl["total"],
             audio_path=HERE / "audio" / "narration.m4a")
print("OK" if p else "FAILED", p)
