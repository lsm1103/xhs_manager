"""组装最终的 index.html。

产物的唯一外部输入是 :root 上的 --t（当前视频时间，秒）。
渲染器每帧写一次 --t，画面就是 --t 的纯函数——这是整套东西能被逐帧
可复现渲染的前提。
"""

from __future__ import annotations

from html import escape
from typing import Any, Callable

from xhs_manager.video_pipeline.composition.layouts import render_layout
from xhs_manager.video_pipeline.composition.media import (  # noqa: F401  (对外仍从 builder 导出)
    DEFAULT_TRANSITION_DURATION,
    IMAGE_SUFFIXES,
    TRANSITION_DURATION,
    VIDEO_SUFFIXES,
    SceneMedia,
    classify_media,
    probe_image_size,
)
from xhs_manager.video_pipeline.composition.styles import build_css
from xhs_manager.video_pipeline.composition.theme import Theme, resolve_theme
from xhs_manager.video_pipeline.composition.timeline import PlannedScene, Timeline, plan_timeline

# 这些版面的前景自带一张主体卡片（截图 / 长图 / 终端窗），
# 背景里的同一张图只当底纹：糊开、压暗、不推拉。
BLURRED_BACKDROP_LAYOUTS = frozenset({"screenshot", "scroll", "terminal"})


def transition_duration(name: str) -> float:
    return TRANSITION_DURATION.get(name, DEFAULT_TRANSITION_DURATION)


# ── 片段 ──────────────────────────────────────────────────────────


def _media_html(scene: PlannedScene, media: SceneMedia | None) -> str:
    """背景媒体层。图片必须给 Ken Burns，否则整段是张死图。"""
    if media is None:
        return '<div class="scene-media"></div>'

    if media.kind == "video":
        inner = (
            f'<video src="assets/{escape(media.filename)}" '
            f'muted playsinline preload="auto"></video>'
        )
    elif scene.layout in BLURRED_BACKDROP_LAYOUTS:
        # 这几个版面的主体是前景那张卡片（截图/长图/终端窗）。
        # 背景再来一次 Ken Burns，等于同一张图一动一静叠在一起，很晃。
        # 这里只把它糊开当底纹，负责填满画面、定住色调。
        inner = (
            f'<div class="still is-blurred" '
            f'style="background-image:url(\'assets/{escape(media.filename)}\')"></div>'
        )
    else:
        # 奇偶交替推进/拉远，相邻的图片场景不会看起来是同一个运镜
        kb = "m-kenburns" if scene.order % 2 else "m-kenburns-alt"
        inner = (
            f'<div class="still m {kb}" '
            f'style="--s:{scene.start:.3f};--d:{scene.duration:.3f};'
            f'background-image:url(\'assets/{escape(media.filename)}\')"></div>'
        )
    return f'<div class="scene-media">{inner}</div>'


def _scene_html(scene: PlannedScene, media: SceneMedia | None, z: int) -> str:
    xd = transition_duration(scene.transition)
    xcls = f"x-{scene.transition.replace('_', '-')}"

    return f"""\
  <section class="scene layout-{scene.layout} m {xcls}"
           id="{escape(scene.scene_id)}"
           data-start="{scene.start:.3f}"
           data-duration="{scene.duration:.3f}"
           style="z-index:{z};--s:{scene.start:.3f};--d:{max(xd, 0.001):.3f}">
{_media_html(scene, media)}
    <div class="scene-scrim"></div>
    <div class="scene-tint"></div>
    <div class="scene-content">
{render_layout(scene, media)}
    </div>
  </section>"""


def _progress_html(tl: Timeline) -> str:
    segs = []
    for s in tl.scenes:
        segs.append(
            f'<div class="progress-seg" style="--w:{s.duration:.3f}">'
            f'<span class="progress-fill" style="--s:{s.start:.3f};--d:{s.duration:.3f}"></span>'
            f"</div>"
        )
    return f'<div class="progress">{"".join(segs)}</div>'


def _captions_html(tl: Timeline) -> str:
    cues = []
    for c in tl.captions:
        cues.append(
            f'<div class="caption" style="--s:{c.start:.3f};--d:{c.duration:.3f}">'
            f"{escape(c.text)}</div>"
        )
    return f'<div class="captions">{"".join(cues)}</div>'


def _shell_html(tl: Timeline, brand: str, handle: str) -> str:
    return f"""\
  <div class="shell">
    <div class="shell-top">
      <div class="brand"><span class="brand-dot"></span>{escape(brand)}</div>
      <div class="chapter" id="chapter"></div>
    </div>
{_progress_html(tl)}
{_captions_html(tl)}
    <div class="watermark">{escape(handle)}</div>
  </div>
  <div class="grain"></div>
  <div class="vignette"></div>"""


# ── 播放/定位 JS ──────────────────────────────────────────────────

_SEEK_JS = """\
(function () {
  var root = document.documentElement;
  var scenes = Array.prototype.slice.call(document.querySelectorAll('.scene'));
  var chapter = document.getElementById('chapter');
  var stage = document.querySelector('.stage');
  var total = parseFloat(stage.dataset.duration || '0');

  var starts = scenes.map(function (el) { return parseFloat(el.dataset.start); });
  var durs = scenes.map(function (el) { return parseFloat(el.dataset.duration); });
  var xfade = scenes.map(function (el) {
    return parseFloat(getComputedStyle(el).getPropertyValue('--d')) || 0;
  });

  function activeIndex(t) {
    for (var i = 0; i < scenes.length; i++) {
      if (t < starts[i] + durs[i]) return i;
    }
    return scenes.length - 1;
  }

  // 唯一的对外入口：把整个画面定位到第 t 秒。
  // 渲染器逐帧调用它，人预览时由 rAF 调用它。
  window.__seek = function (t) {
    if (t < 0) t = 0;
    root.style.setProperty('--t', t);

    var ai = activeIndex(t);
    for (var i = 0; i < scenes.length; i++) {
      // 当前场景常驻；上一个场景在转场窗口内保留，让新场景叠着它淡入
      var on = (i === ai) || (i === ai - 1 && t < starts[ai] + xfade[ai]);
      scenes[i].classList.toggle('on', on);

      var v = scenes[i].querySelector('video');
      if (v) {
        if (i === ai) {
          var local = t - starts[i];
          var vd = v.duration;
          // 素材比场景短就取模循环，避免后半段定格在最后一帧
          if (vd && isFinite(vd) && vd > 0) v.currentTime = local % vd;
        }
        v.pause();
      }
    }
    if (chapter) chapter.textContent = (ai + 1) + ' / ' + scenes.length;
  };

  window.__totalDuration = total;
  window.__seek(0);

  // 只有带 #preview 打开时才自动播放。
  // 渲染器用的是不带 hash 的 file:// URL，所以渲染时画面永远是静止可控的
  // ——绝不能让墙钟驱动的 rAF 和逐帧 seek 抢同一个 --t。
  if (location.hash === '#preview') {
    var t0 = null;
    requestAnimationFrame(function loop(ts) {
      if (t0 === null) t0 = ts;
      var elapsed = (ts - t0) / 1000;
      window.__seek(elapsed % (total || 1));
      requestAnimationFrame(loop);
    });
  }
})();
"""


# ── 入口 ──────────────────────────────────────────────────────────


def build_composition_html(
    scenes: list[dict[str, Any]],
    *,
    media_lookup: Callable[[str], SceneMedia | None],
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
    theme: Theme | str | None = None,
    brand: str = "AI 工作流实验员",
    handle: str = "@ai-workflow-lab",
) -> tuple[str, Timeline]:
    """把 Stage2 的场景列表编译成完整 HTML。返回 (html, timeline)。"""
    th = theme if isinstance(theme, Theme) else resolve_theme(theme)
    tl = plan_timeline(scenes)

    scene_blocks = [
        _scene_html(s, media_lookup(s.scene_id), z=i + 1)
        for i, s in enumerate(tl.scenes)
    ]

    return (
        f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>Video Composition</title>
<style>
{build_css(th, width, height)}
</style>
</head>
<body>
<div class="stage" data-duration="{tl.total_duration:.3f}" data-fps="{fps}">
  <div class="backdrop"></div>
  <div class="scenes">
{chr(10).join(scene_blocks)}
  </div>
{_shell_html(tl, brand, handle)}
</div>
<script>
{_SEEK_JS}</script>
</body>
</html>
""",
        tl,
    )
