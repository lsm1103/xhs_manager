"""HTML → MP4 渲染器（Playwright 逐帧截图 + ffmpeg 合成）。

相比拼接 Node.js 脚本字符串的做法，直接用 Python playwright:
  - 无需 Node 依赖
  - 异常可捕获、可调试
  - 帧时序由 Python 精确控制
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# 注入页面的时间控制函数：按经过秒数激活对应场景
SEEK_JS = """
(elapsed) => {
  const clips = Array.from(document.querySelectorAll('.clip'));

  // 定位当前时间落在哪个场景，并算出场景内的局部时间
  let cumulative = 0;
  let activeIndex = -1;
  let localTime = 0;
  for (let i = 0; i < clips.length; i++) {
    const dur = parseFloat(clips[i].dataset.duration || 5);
    if (elapsed >= cumulative && elapsed < cumulative + dur) {
      activeIndex = i;
      localTime = elapsed - cumulative;
      break;
    }
    cumulative += dur;
  }
  if (activeIndex === -1 && clips.length) {
    activeIndex = clips.length - 1;
    localTime = parseFloat(clips[activeIndex].dataset.duration || 5);
  }

  clips.forEach((clip, i) => {
    const on = i === activeIndex;
    clip.classList.toggle('active', on);
    clip.classList.remove('exiting');
    clip.style.opacity = on ? '1' : '0';
    clip.style.zIndex = on ? '10' : '0';

    // 背景视频必须手动 seek —— 逐帧截图时视频不会自然播放
    const v = clip.querySelector('video.bg-video');
    if (v) {
      if (on) {
        // 素材可能比场景短，用取模循环播放填满整个场景
        const vd = v.duration;
        if (vd && isFinite(vd) && vd > 0) {
          v.currentTime = localTime % vd;
        }
      }
      v.pause();
    }
  });
  return activeIndex;
}
"""


class HtmlVideoRenderer:
    """把 HyperFrames 风格的 HTML 组合渲染成 MP4。"""

    def __init__(
        self,
        width: int = 1080,
        height: int = 1920,
        fps: int = 30,
        chrome_path: str = CHROME_PATH,
    ) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.chrome_path = chrome_path

    def available(self) -> bool:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        return Path(self.chrome_path).exists() and shutil.which("ffmpeg") is not None

    # ── 逐帧截图 ──────────────────────────────────────────────

    def capture_frames(
        self,
        html_path: Path,
        frames_dir: Path,
        duration: float,
        on_progress: Optional[Callable[[int, int], None]] = None,
    ) -> int:
        """按 fps 逐帧截图，返回实际截取的帧数。"""
        from playwright.sync_api import sync_playwright

        frames_dir.mkdir(parents=True, exist_ok=True)
        total_frames = max(1, int(duration * self.fps))

        with sync_playwright() as p:
            browser = p.chromium.launch(
                executable_path=self.chrome_path,
                headless=True,
                args=["--disable-web-security", "--allow-file-access-from-files"],
            )
            try:
                page = browser.new_page(
                    viewport={"width": self.width, "height": self.height},
                    device_scale_factor=1,
                )
                # 用 "load" 而非 "networkidle"：多个 <video preload="auto">
                # 会让网络一直不空闲，networkidle 必然超时。
                # 真正需要等的是视频元数据，下面单独处理。
                page.goto(
                    html_path.resolve().as_uri(),
                    wait_until="load",
                    timeout=60000,
                )
                page.wait_for_timeout(800)  # 字体

                # 停掉页面自带的 requestAnimationFrame 播放循环，改为手动 seek
                page.evaluate("() => { window.__frameMode = true; }")

                # 等所有背景视频的元数据就绪：没有 duration 就无法 seek，
                # 会导致整段画面停在第一帧或黑屏
                page.evaluate("""
                  () => Promise.all(
                    Array.from(document.querySelectorAll('video.bg-video')).map(v =>
                      v.readyState >= 1
                        ? Promise.resolve()
                        : new Promise(res => {
                            v.addEventListener('loadedmetadata', res, { once: true });
                            setTimeout(res, 5000);
                          })
                    )
                  )
                """)

                for i in range(total_frames):
                    elapsed = i / self.fps
                    page.evaluate(SEEK_JS, elapsed)
                    page.screenshot(
                        path=str(frames_dir / f"frame_{i:05d}.png"),
                        type="png",
                    )
                    if on_progress and i and i % (self.fps * 5) == 0:
                        on_progress(i, total_frames)

            finally:
                browser.close()

        captured = len(list(frames_dir.glob("frame_*.png")))
        logger.info("截帧完成: %d/%d 帧", captured, total_frames)
        return captured

    # ── ffmpeg 合成 ───────────────────────────────────────────

    def frames_to_video(
        self,
        frames_dir: Path,
        output_path: Path,
        duration: float,
        audio_path: Optional[Path] = None,
    ) -> bool:
        """把帧序列合成 MP4，可选混入音轨。"""
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(self.fps),
            "-i", str(frames_dir / "frame_%05d.png"),
        ]
        if audio_path and audio_path.exists():
            cmd += ["-i", str(audio_path), "-c:a", "aac", "-b:a", "192k", "-shortest"]

        cmd += [
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            "-preset", "medium",
            "-crf", "23",
            "-t", str(duration),
            str(output_path),
        ]

        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode == 0 and output_path.exists():
            logger.info(
                "ffmpeg 合成成功: %s (%.1f MB)",
                output_path.name, output_path.stat().st_size / 1024 / 1024,
            )
            return True

        logger.error("ffmpeg 失败: %s", r.stderr[-800:])
        return False

    # ── 一站式 ────────────────────────────────────────────────

    def render(
        self,
        html_path: Path,
        output_path: Path,
        duration: float,
        audio_path: Optional[Path] = None,
        keep_frames: bool = False,
    ) -> Optional[Path]:
        """HTML → MP4 全流程。失败返回 None。"""
        frames_dir = output_path.parent / "frames"

        try:
            captured = self.capture_frames(
                html_path, frames_dir, duration,
                on_progress=lambda i, t: logger.info("  截帧 %d/%d (%d%%)", i, t, i * 100 // t),
            )
            if captured == 0:
                logger.error("未截取到任何帧")
                return None

            ok = self.frames_to_video(frames_dir, output_path, duration, audio_path)
            return output_path if ok else None

        except Exception as e:
            logger.error("渲染失败: %s", e)
            return None
        finally:
            if not keep_frames:
                shutil.rmtree(frames_dir, ignore_errors=True)


# ── 封面图 ────────────────────────────────────────────────────


def extract_covers(
    video_path: Path,
    output_dir: Path,
    at_second: float = 1.0,
) -> dict[str, str]:
    """从视频抽帧生成各平台尺寸封面。"""
    covers: dict[str, str] = {}

    base = output_dir / "cover_default.jpg"
    r = subprocess.run(
        ["ffmpeg", "-y", "-ss", str(at_second), "-i", str(video_path),
         "-vframes", "1", "-q:v", "2", str(base)],
        capture_output=True, timeout=60,
    )
    if r.returncode != 0 or not base.exists():
        logger.warning("封面抽帧失败")
        return covers
    covers["default"] = str(base)

    sizes = {
        "xiaohongshu": (1080, 1440),   # 3:4
        "douyin": (1080, 1920),         # 9:16
        "bilibili": (1920, 1080),       # 16:9
        "twitter": (1920, 1080),        # 16:9
    }
    for platform, (w, h) in sizes.items():
        out = output_dir / f"cover_{platform}.jpg"
        rr = subprocess.run(
            ["ffmpeg", "-y", "-i", str(base),
             "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
             "-q:v", "2", str(out)],
            capture_output=True, timeout=60,
        )
        if rr.returncode == 0 and out.exists():
            covers[platform] = str(out)

    return covers
