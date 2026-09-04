"""小红书视频发布 —— Playwright 驱动独立 Chrome profile。

为什么不用 opencli browser:
  opencli 的 `upload` 走「点击元素 → 等 Page.fileChooserOpened」流程，而小红书的
  file input 是隐藏的，Chrome 要求真实用户手势才会打开文件选择器，扩展的合成点击
  不满足该条件（强制 input 可见后仍失败）。
  Playwright 的 set_input_files 走 CDP DOM.setFileInputFiles，不需要 fileChooser
  也不需要用户手势，对隐藏 input 同样有效。

为什么用独立 profile:
  1) 不和用户的交互式 Chrome 抢同一个 user-data-dir
  2) 无人值守运行时不受用户开关浏览器影响
  3) Chrome 在 macOS 用 App-Bound Encryption 绑定 cookie，拷贝用户 profile 无效，
     必须让这个 profile 自己登录一次（之后长期复用）
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DEFAULT_PROFILE_DIR = Path.home() / ".xhs_pipeline_chrome"

PUBLISH_URL = (
    "https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch"
)
LOGIN_URL_MARK = "creator.xiaohongshu.com/login"

# 上传页的隐藏 file input；accept 里含 .mp4
FILE_INPUT = "input[type=file]"


@dataclass
class PublishResult:
    success: bool
    mode: str = "draft"
    error: str = ""
    screenshot: Optional[str] = None


class XhsPublisher:
    def __init__(
        self,
        profile_dir: Path = DEFAULT_PROFILE_DIR,
        chrome_path: str = CHROME_PATH,
        headless: bool = True,
    ) -> None:
        self.profile_dir = Path(profile_dir)
        self.chrome_path = chrome_path
        self.headless = headless

    # ── 环境 ────────────────────────────────────────────────────

    def available(self) -> tuple[bool, str]:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False, "未安装 playwright"
        if not Path(self.chrome_path).exists():
            return False, f"找不到 Chrome: {self.chrome_path}"
        if not self.profile_dir.exists():
            return False, (
                "尚未初始化登录态，请先运行一次："
                "python -m xhs_manager.video_pipeline.cli xhs-login"
            )
        return True, ""

    def _context(self, pw, headless: Optional[bool] = None):
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        return pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            executable_path=self.chrome_path,
            headless=self.headless if headless is None else headless,
            viewport={"width": 1440, "height": 900},
            args=["--no-first-run", "--no-default-browser-check"],
        )

    # ── 一次性登录 ──────────────────────────────────────────────

    def interactive_login(self, timeout_s: int = 300) -> bool:
        """打开有头浏览器让用户扫码登录，成功后 cookie 落在独立 profile 里。"""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            ctx = self._context(pw, headless=False)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
                logger.info("请在打开的浏览器窗口中完成登录（最多等待 %d 秒）…", timeout_s)
                try:
                    page.wait_for_selector(FILE_INPUT, timeout=timeout_s * 1000)
                except Exception:
                    logger.error("等待登录超时")
                    return False
                logger.info("登录成功，凭证已保存到 %s", self.profile_dir)
                return True
            finally:
                ctx.close()

    def logged_in(self) -> bool:
        """无头检查登录态是否仍有效。"""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            ctx = self._context(pw)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)
                return LOGIN_URL_MARK not in page.url
            except Exception as e:
                logger.warning("登录态检查失败: %s", e)
                return False
            finally:
                ctx.close()

    # ── 发布 ────────────────────────────────────────────────────

    def publish_video(
        self,
        video_path: str,
        title: str,
        content: str,
        mode: str = "draft",
        upload_timeout_s: int = 600,
    ) -> PublishResult:
        """上传视频并存草稿（mode=draft）或直接发布（mode=publish）。"""
        from playwright.sync_api import sync_playwright

        ok, why = self.available()
        if not ok:
            return PublishResult(False, mode, why)

        video = Path(video_path).resolve()
        if not video.exists():
            return PublishResult(False, mode, f"视频文件不存在: {video}")

        shot = str(video.parent / "xhs_publish_fail.png")

        with sync_playwright() as pw:
            ctx = self._context(pw)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            try:
                page.goto(PUBLISH_URL, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(3000)

                if LOGIN_URL_MARK in page.url:
                    return PublishResult(
                        False, mode,
                        "登录态已失效，请重新运行 cli xhs-login",
                    )

                # 隐藏 input 也能挂文件：set_input_files 走 CDP，不需要用户手势
                page.wait_for_selector(FILE_INPUT, state="attached", timeout=30000)
                page.set_input_files(FILE_INPUT, str(video))
                logger.info("已挂载视频，等待上传与转码…")

                # 上传完成的标志：标题输入框出现
                title_box = "input[placeholder*='标题'], input[placeholder*='填写标题']"
                page.wait_for_selector(title_box, timeout=upload_timeout_s * 1000)

                page.fill(title_box, title[:20])

                # 正文是 contenteditable，不是 input
                body_sel = (
                    "div[contenteditable='true'], "
                    "textarea[placeholder*='描述'], textarea[placeholder*='正文']"
                )
                if page.locator(body_sel).count():
                    page.click(body_sel)
                    page.keyboard.insert_text(content)

                page.wait_for_timeout(1500)

                btn = "发布" if mode == "publish" else "暂存离线"
                target = page.get_by_role("button", name=btn)
                if not target.count():
                    page.screenshot(path=shot, full_page=True)
                    return PublishResult(
                        False, mode, f"找不到「{btn}」按钮", shot,
                    )
                target.first.click()
                page.wait_for_timeout(5000)

                logger.info("小红书视频已%s: %s", "发布" if mode == "publish" else "存草稿", title[:24])
                return PublishResult(True, mode)

            except Exception as e:
                try:
                    page.screenshot(path=shot, full_page=True)
                except Exception:
                    shot = None
                return PublishResult(False, mode, str(e)[:400], shot)
            finally:
                ctx.close()
