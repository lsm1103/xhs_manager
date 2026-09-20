"""按给定时间点截静帧，用来肉眼检查画面。"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
OUT = HERE / "stills"; OUT.mkdir(exist_ok=True)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
times = [float(x) for x in sys.argv[1:]] or [2, 7, 14, 19, 25, 28, 36, 42, 47, 50]

with sync_playwright() as p:
    b = p.chromium.launch(executable_path=CHROME, headless=True,
                          args=["--disable-web-security", "--allow-file-access-from-files"])
    page = b.new_page(viewport={"width": 1080, "height": 1920}, device_scale_factor=1)
    page.goto((HERE / "index.html").resolve().as_uri(), wait_until="load", timeout=60000)
    page.wait_for_timeout(1200)
    for t in times:
        page.evaluate("t => window.__seekToTime(t)", t)
        page.wait_for_timeout(120)
        page.screenshot(path=str(OUT / f"t{t:05.1f}.png"))
        print("ok", t)
    print("console errors:", page.evaluate("() => window.__err || 'none'"))
    b.close()
