"""HTML → MP4 渲染器（Playwright 逐帧截图 + ffmpeg 合成）。

相比拼接 Node.js 脚本字符串的做法，直接用 Python playwright:
  - 无需 Node 依赖
  - 异常可捕获、可调试
  - 帧时序由 Python 精确控制
"""

import logging
import os
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)

# 按优先级探测 Chrome/Chromium。写死 macOS 路径会让 Linux/CI 上直接 available()=False。
CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/opt/pw-browsers/chromium",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
)


def detect_chrome_path() -> str:
    """返回第一个存在的 Chrome 可执行文件。

    XHS_CHROME_PATH 优先，方便在容器/CI 里显式指定。
    都找不到就返回空串，交给 available() 判定为不可用。
    """
    env = os.environ.get("XHS_CHROME_PATH", "").strip()
    if env:
        return env
    for candidate in CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return ""


CHROME_PATH = detect_chrome_path()


# ffmpeg 同理：PATH 里没有不代表机器上没有。
# 但「存在」不等于「能用」——Playwright 自带的那份是裁剪版，
# 只有 vp8/webm，没有 libx264 也没有 mp4 muxer，拿它出片必然失败。
# 所以候选必须过一遍能力检查。
FFMPEG_CANDIDATES = (
    "/opt/pw-browsers/ffmpeg-1011/ffmpeg-linux",
)


@lru_cache(maxsize=8)
def ffmpeg_supports_h264(path: str) -> bool:
    """检查这个 ffmpeg 能不能编 H.264。

    只看文件在不在会误判：裁剪版 ffmpeg 照样存在、照样能 -version，
    但一跑 libx264 就报 Unknown encoder，而那时已经白截了几百帧。
    """
    try:
        r = subprocess.run(
            [path, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and "libx264" in r.stdout


def detect_ffmpeg() -> str:
    """返回**能编 H.264** 的 ffmpeg 路径，找不到返回空串。

    XHS_FFMPEG_PATH 显式指定时直接采信，不做能力检查——
    用户明确指了就按用户说的来，出错也该在那里报。
    """
    env = os.environ.get("XHS_FFMPEG_PATH", "").strip()
    if env:
        return env

    candidates = []
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    candidates.extend(c for c in FFMPEG_CANDIDATES if Path(c).exists())

    for candidate in candidates:
        if ffmpeg_supports_h264(candidate):
            return candidate
        logger.debug("%s 不支持 libx264，跳过", candidate)
    return ""


def detect_ffprobe() -> str:
    """ffprobe 通常和 ffmpeg 同目录；Playwright 自带的那份没有 ffprobe。"""
    env = os.environ.get("XHS_FFPROBE_PATH", "").strip()
    if env:
        return env
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = detect_ffmpeg()
    if ffmpeg:
        sibling = Path(ffmpeg).with_name("ffprobe")
        if sibling.exists():
            return str(sibling)
    return ""

# 注入页面的时间控制函数：按经过秒数把画面定位到该时刻。
#
# 新版组合（composition/builder.py 生成）自带 window.__seek，
# 所有动画都由 :root 的 --t 驱动，画面是 --t 的纯函数。
# 旧版组合没有 __seek，回落到下面按 .clip 切换 class 的逻辑。
SEEK_JS = """
(elapsed) => {
  if (typeof window.__seek === 'function') {
    window.__seek(elapsed);
    return -1;
  }

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
        frame_format: str = "jpeg",
        frame_quality: int = 95,
    ) -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.chrome_path = chrome_path
        # 中间帧默认走 JPEG。实测 1080x1920 下 PNG 截图 ~947ms/帧，
        # JPEG q95 ~176ms/帧 —— 5 倍多的差距，瓶颈是 PNG 编码而不是页面渲染。
        # 反正这些帧最后都要被 H.264 (CRF 23) 重编一遍，q95 的损失可以忽略。
        # 需要无损中间产物（比如调试逐帧差异）时传 frame_format="png"。
        self.frame_format = frame_format
        self.frame_quality = frame_quality

    @property
    def frame_suffix(self) -> str:
        return "jpg" if self.frame_format == "jpeg" else self.frame_format

    def available(self) -> bool:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False
        return bool(self.chrome_path) and Path(self.chrome_path).exists() and bool(detect_ffmpeg())

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

                # 停掉页面自带的 requestAnimationFrame 播放循环，改为手动 seek。
                # 新版组合只在 URL 带 #preview 时才自播，这里不带 hash，天然是静止的。
                page.evaluate("() => { window.__frameMode = true; }")

                # 等所有背景视频的元数据就绪：没有 duration 就无法 seek，
                # 会导致整段画面停在第一帧或黑屏
                page.evaluate("""
                  () => Promise.all(
                    Array.from(
                      document.querySelectorAll('video.bg-video, .scene-media video')
                    ).map(v =>
                      v.readyState >= 1
                        ? Promise.resolve()
                        : new Promise(res => {
                            v.addEventListener('loadedmetadata', res, { once: true });
                            setTimeout(res, 5000);
                          })
                    )
                  )
                """)

                shot_kwargs = {"type": self.frame_format}
                if self.frame_format == "jpeg":
                    shot_kwargs["quality"] = self.frame_quality

                for i in range(total_frames):
                    elapsed = i / self.fps
                    page.evaluate(SEEK_JS, elapsed)
                    page.screenshot(
                        path=str(frames_dir / f"frame_{i:05d}.{self.frame_suffix}"),
                        **shot_kwargs,
                    )
                    if on_progress and i and i % (self.fps * 5) == 0:
                        on_progress(i, total_frames)

            finally:
                browser.close()

        captured = len(list(frames_dir.glob(f"frame_*.{self.frame_suffix}")))
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
        ffmpeg = detect_ffmpeg()
        if not ffmpeg:
            logger.error("找不到 ffmpeg")
            return False

        cmd = [
            ffmpeg, "-y",
            "-framerate", str(self.fps),
            "-i", str(frames_dir / f"frame_%05d.{self.frame_suffix}"),
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


# ── HTML → 静帧 ───────────────────────────────────────────────


def render_html_images(
    jobs: "list[tuple[Path, Path, int, int]]",
    chrome_path: str = "",
    quality: int = 92,
    seek_second: float = 1.0,
) -> "dict[Path, Path]":
    """把若干 HTML 各自截成一张 JPEG。

    jobs 是 (html_path, out_path, width, height) 的列表，共用一个浏览器实例——
    每张封面单独起一次 Chromium 的话，光启动开销就比渲染本身贵。

    返回 {out_path: out_path}，失败的那张不会出现在结果里。
    """
    chrome = chrome_path or detect_chrome_path()
    if not chrome:
        logger.warning("找不到 Chrome，跳过静帧渲染")
        return {}

    from playwright.sync_api import sync_playwright

    done: dict[Path, Path] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(
            executable_path=chrome,
            headless=True,
            args=["--disable-web-security", "--allow-file-access-from-files"],
        )
        try:
            for html_path, out_path, width, height in jobs:
                try:
                    page = browser.new_page(
                        viewport={"width": width, "height": height},
                        device_scale_factor=1,
                    )
                    page.goto(
                        html_path.resolve().as_uri(), wait_until="load", timeout=60000,
                    )
                    page.wait_for_timeout(600)  # 字体

                    # 背景若是视频，必须手动 seek 到某一帧，否则截到黑屏
                    page.evaluate(
                        """
                        (t) => Promise.all(
                          Array.from(document.querySelectorAll('video')).map(v =>
                            new Promise(res => {
                              const seek = () => {
                                const d = v.duration;
                                if (d && isFinite(d) && d > 0) {
                                  v.currentTime = Math.min(t, d - 0.05);
                                }
                                v.pause();
                                setTimeout(res, 220);
                              };
                              if (v.readyState >= 1) seek();
                              else {
                                v.addEventListener('loadedmetadata', seek, {once: true});
                                setTimeout(res, 4000);
                              }
                            })
                          )
                        )
                        """,
                        seek_second,
                    )

                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(out_path), type="jpeg", quality=quality)
                    page.close()
                    done[out_path] = out_path
                except Exception as e:  # noqa: BLE001 - 一张失败不该拖垮其余的
                    logger.warning("静帧渲染失败 %s: %s", out_path.name, e)
        finally:
            browser.close()

    return done


# ── 封面图 ────────────────────────────────────────────────────


def extract_covers(
    video_path: Path,
    output_dir: Path,
    at_second: float = 1.0,
) -> dict[str, str]:
    """从视频抽帧生成各平台尺寸封面。"""
    covers: dict[str, str] = {}

    ffmpeg = detect_ffmpeg()
    if not ffmpeg:
        logger.warning("找不到 ffmpeg，跳过封面生成")
        return covers

    # ffmpeg 不会自己建目录，输出目录不存在就静默失败。
    # 流水线里传的是成片所在目录（必然存在），但别的调用方不一定。
    output_dir.mkdir(parents=True, exist_ok=True)

    base = output_dir / "cover_default.jpg"
    r = subprocess.run(
        [ffmpeg, "-y", "-ss", str(at_second), "-i", str(video_path),
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
            [ffmpeg, "-y", "-i", str(base),
             "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
             "-q:v", "2", str(out)],
            capture_output=True, timeout=60,
        )
        if rr.returncode == 0 and out.exists():
            covers[platform] = str(out)

    return covers


def probe_duration(video_path: Path) -> Optional[float]:
    """用 ffprobe 读真实时长（秒）。有旁白时 ffmpeg -shortest 会按音轨截断，
    成片时长可能短于脚本时长，必须以此为准回写数据库。"""
    ffprobe = detect_ffprobe()
    if not ffprobe:
        return None
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(video_path)],
            capture_output=True, text=True, timeout=30,
        )
        return round(float(r.stdout.strip()), 3) if r.returncode == 0 and r.stdout.strip() else None
    except (subprocess.TimeoutExpired, ValueError):
        return None
