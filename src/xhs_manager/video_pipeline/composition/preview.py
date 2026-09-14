"""版面预览工具：不跑流水线也能看组合长什么样。

    # 用内置样例脚本生成预览（三个主题各一份）
    python -m xhs_manager.video_pipeline.composition.preview

    # 用真实脚本 JSON
    python -m xhs_manager.video_pipeline.composition.preview --script script.json

    # 顺带截几张图（需要 Chrome/Chromium）
    python -m xhs_manager.video_pipeline.composition.preview --shots 1,6,12

生成的 HTML 用浏览器打开时**加上 #preview** 才会自动播放；
不加 hash 是静止的，和渲染器看到的完全一致。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from xhs_manager.video_pipeline.composition.builder import build_composition_html
from xhs_manager.video_pipeline.composition.theme import THEMES

# 覆盖全部 7 种版面的样例脚本，改 CSS 时对着它看回归
SAMPLE_SCENES: list[dict] = [
    {
        "scene_id": "s01", "order": 1, "duration": 5,
        "visual_desc": "abstract data flow",
        "text_overlay": {"main": "AI 写代码，人做什么？", "sub": "一个被吵翻的问题",
                          "animation": "slide_up"},
        "transition": "fade", "bgm_mood": "hook", "material_hints": [],
        "narration": "最近有个帖子吵翻了，说AI已经能写大部分代码。",
    },
    {
        "scene_id": "s02", "order": 2, "duration": 6,
        "visual_desc": "survey chart",
        "text_overlay": {"main": "87% 的开发者", "sub": "每天都在用 AI 编码工具",
                          "animation": "counter"},
        "transition": "zoom_in", "bgm_mood": "reveal", "material_hints": [],
        "narration": "调查显示，八成七的开发者每天都在用AI编码工具。",
    },
    {
        "scene_id": "s03", "order": 3, "duration": 7,
        "visual_desc": "split opinions",
        "text_overlay": {"main": "两种声音", "sub": "效率暴涨；代码质量下滑；维护成本转移",
                          "animation": "pop_in"},
        "transition": "slide_left", "bgm_mood": "tension", "material_hints": [],
        "narration": "但评论区分成两派，有人说效率暴涨，有人说质量在下滑。",
    },
    {
        "scene_id": "s04", "order": 4, "duration": 6,
        "visual_desc": "quote card",
        "text_overlay": {"main": "「代码是写给人看的，顺便给机器执行」", "sub": "SICP",
                          "animation": "slide_up"},
        "transition": "blur", "bgm_mood": "explain", "material_hints": [],
        "narration": "有人翻出了那句老话，代码是写给人看的。",
    },
    {
        "scene_id": "s05", "order": 5, "duration": 6,
        "visual_desc": "comparison",
        "text_overlay": {"main": "手写 vs AI 生成", "sub": "", "animation": "pop_in"},
        "transition": "wipe", "bgm_mood": "tension", "material_hints": [],
        "narration": "到底该怎么选？其实关键不在工具。",
    },
    {
        "scene_id": "s06", "order": 6, "duration": 5,
        "visual_desc": "typing",
        "text_overlay": {"main": "会提问的人赢", "sub": "把需求讲清楚才是硬本事",
                          "animation": "typewriter"},
        "transition": "glitch", "bgm_mood": "uplift", "material_hints": [],
        "narration": "真正拉开差距的，是能不能把需求讲清楚。",
    },
    {
        "scene_id": "s07", "order": 7, "duration": 5,
        "visual_desc": "outro",
        "text_overlay": {"main": "关注我", "sub": "每天一个 AI 工作流", "animation": "pop_in"},
        "transition": "fade", "bgm_mood": "closing", "material_hints": [],
        "narration": "关注我，每天拆一个能跑的AI工作流。",
    },
]


def _load_scenes(path: str | None) -> list[dict]:
    if not path:
        return SAMPLE_SCENES
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # 兼容整份脚本 JSON 和裸场景数组
    return data["scenes"] if isinstance(data, dict) else data


def _shoot(html_path: Path, times: list[float], out_dir: Path) -> None:
    from xhs_manager.video_pipeline.integrations.renderer import detect_chrome_path

    chrome = detect_chrome_path()
    if not chrome:
        print("! 没找到 Chrome/Chromium，跳过截图", file=sys.stderr)
        return

    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=chrome)
        try:
            page = browser.new_page(
                viewport={"width": 1080, "height": 1920}, device_scale_factor=1,
            )
            page.goto(html_path.resolve().as_uri(), wait_until="load", timeout=60000)
            page.wait_for_timeout(600)
            for t in times:
                page.evaluate("(t) => window.__seek(t)", t)
                shot = out_dir / f"t{t:06.2f}.png"
                page.screenshot(path=str(shot))
                print(f"  → {shot}")
        finally:
            browser.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="预览 HTML 视频组合版面")
    ap.add_argument("--script", help="脚本 JSON（整份脚本或裸 scenes 数组）")
    ap.add_argument("--out", default="data/video_pipeline/_preview", help="输出目录")
    ap.add_argument("--theme", help="只生成指定主题，默认三个主题都生成")
    ap.add_argument("--shots", help="额外截图的时间点，逗号分隔的秒数")
    args = ap.parse_args(argv)

    scenes = _load_scenes(args.script)
    themes = [args.theme] if args.theme else list(THEMES)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    for theme in themes:
        html, tl = build_composition_html(
            scenes, media_lookup=lambda sid: None, theme=theme,
        )
        html_path = out_root / f"{theme}.html"
        html_path.write_text(html, encoding="utf-8")
        layouts = ", ".join(f"{s.scene_id}:{s.layout}" for s in tl.scenes)
        print(f"[{theme}] {html_path}  {tl.total_duration:.0f}s  {layouts}")

        if args.shots:
            times = [float(x) for x in args.shots.split(",")]
            _shoot(html_path, times, out_root / f"{theme}_shots")

    print(f"\n浏览器打开时加 #preview 才自动播放，例如 file://{out_root.resolve()}/"
          f"{themes[0]}.html#preview")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
