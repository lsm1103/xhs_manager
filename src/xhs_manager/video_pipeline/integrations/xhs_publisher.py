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

from xhs_manager.video_pipeline.integrations.renderer import detect_chrome_path
from typing import Optional

logger = logging.getLogger(__name__)

# 和渲染器共用一套探测逻辑：写死 macOS 路径会让 Linux/CI 上永远「找不到 Chrome」，
# 连「视频文件不存在」这种更靠前的错误都报不出来。
CHROME_PATH = detect_chrome_path()
DEFAULT_PROFILE_DIR = Path.home() / ".xhs_pipeline_chrome"

PUBLISH_URL = (
    "https://creator.xiaohongshu.com/publish/publish?source=official&from=tab_switch"
)
LOGIN_URL_MARK = "creator.xiaohongshu.com/login"

# 上传页的隐藏 file input；accept 里含 .mp4
FILE_INPUT = "input[type=file]"

# ── 以下选择器按创作者中心真实页面实测校准（2026-09-04）──
# 标题框 placeholder 实为「填写标题会有更多赞哦」
TITLE_INPUT = "input[placeholder*='填写标题']"
# 正文是 tiptap/ProseMirror 富文本，不是 textarea
BODY_EDITOR = "div.tiptap[contenteditable='true'], div.ProseMirror[contenteditable='true']"
# 底部按钮实为「暂存离开」，不是「暂存离线」
BTN_DRAFT = "暂存离开"
BTN_PUBLISH = "发布"

# 上传进行中的标志：媒体区会显示「取消上传」
UPLOADING_MARK = "取消上传"

# 提交按钮是自定义元素 <xhs-publish-btn>，内部用 **closed shadow root** 封装：
#   - textContent / innerText 为空，querySelectorAll 和 Playwright 的 text= 都找不到
#   - 但宿主元素本身在 DOM 里，属性上带着文案：
#     save-text="暂存离开" submit-text="发布" submit-disabled="false"
# 因此只能定位宿主元素，再按其内部两个按钮的横向位置点击。
PUBLISH_BTN_HOST = "xhs-publish-btn"

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
        cdp_url: str = "",
    ) -> None:
        """
        Args:
            cdp_url: 非空时连接已运行的 Chrome（如 http://127.0.0.1:9222），
                在**用户自己的 profile** 里操作 —— 小红书草稿存浏览器本地，
                只有这样存出来的草稿用户才看得到。
                为空则用独立 profile 启动（草稿仅在该 profile 内可见）。
        """
        self.profile_dir = Path(profile_dir)
        self.chrome_path = chrome_path
        self.headless = headless
        self.cdp_url = cdp_url

    # ── 环境 ────────────────────────────────────────────────────

    def available(self) -> tuple[bool, str]:
        try:
            import playwright  # noqa: F401
        except ImportError:
            return False, "未安装 playwright"
        if self.cdp_url:
            import urllib.error
            import urllib.request
            try:
                urllib.request.urlopen(f"{self.cdp_url}/json/version", timeout=3).read()
                return True, ""
            except (urllib.error.URLError, OSError) as e:
                return False, (
                    f"连不上 {self.cdp_url}（{e}）。请用调试端口启动 Chrome：\n"
                    f"  '{CHROME_PATH}' --remote-debugging-port=9222"
                )
        if not Path(self.chrome_path).exists():
            return False, f"找不到 Chrome: {self.chrome_path}"
        if not self.profile_dir.exists():
            return False, (
                "尚未初始化登录态，请先运行一次："
                "python -m xhs_manager.video_pipeline.cli xhs-login"
            )
        return True, ""

    def _context(self, pw, headless: Optional[bool] = None):
        if self.cdp_url:
            browser = pw.chromium.connect_over_cdp(self.cdp_url)
            self._cdp_browser = browser
            return browser.contexts[0] if browser.contexts else browser.new_context()
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        return pw.chromium.launch_persistent_context(
            str(self.profile_dir),
            executable_path=self.chrome_path,
            headless=self.headless if headless is None else headless,
            viewport={"width": 1440, "height": 900},
            args=["--no-first-run", "--no-default-browser-check"],
        )

    def _release(self, ctx) -> None:
        """CDP 模式下不能关掉用户的浏览器，只断开连接。"""
        if self.cdp_url:
            br = getattr(self, "_cdp_browser", None)
            if br:
                try:
                    br.close()   # 仅断开 CDP 连接，不结束浏览器进程
                except Exception:
                    pass
        else:
            ctx.close()

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
                self._release(ctx)

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
                self._release(ctx)

    @staticmethod
    def _wait_submit_ready(page, timeout_s: int) -> bool:
        """等待上传完成：<xhs-publish-btn> 出现且 submit-disabled 变为 false。"""
        import time as _t

        deadline = _t.monotonic() + timeout_s
        last_log = 0.0
        while _t.monotonic() < deadline:
            st = page.evaluate(
                """() => {
                  const el = document.querySelector('xhs-publish-btn');
                  if (!el) return null;
                  const b = el.getBoundingClientRect();
                  return {disabled: el.getAttribute('submit-disabled'),
                          loading: el.getAttribute('submit-loading'),
                          x: b.left, y: b.top, w: b.width, h: b.height};
                }"""
            )
            if st and st["disabled"] == "false" and st["loading"] != "true" and st["w"] > 0:
                logger.info("上传完成，提交按钮已就绪")
                return True
            elapsed = timeout_s - (deadline - _t.monotonic())
            if elapsed - last_log >= 30:
                last_log = elapsed
                logger.info("上传中… %.0fs (btn=%s)", elapsed, st and st["disabled"])
            page.wait_for_timeout(3000)
        return False

    @staticmethod
    def _click_submit(page, mode: str) -> bool:
        """点击 shadow 内的提交按钮。

        closed shadow root 无法用选择器进入，但鼠标事件按坐标可以命中。
        宿主元素内部横向排布：左「暂存离开」右「发布」，
        按实测比例取各自中心点（左 ~34%，右 ~62%）。
        """
        box = page.evaluate(
            """() => {
              const el = document.querySelector('xhs-publish-btn');
              if (!el) return null;
              const b = el.getBoundingClientRect();
              return {x: b.left, y: b.top, w: b.width, h: b.height};
            }"""
        )
        if not box or box["w"] <= 0:
            return False
        ratio = 0.62 if mode == "publish" else 0.34
        page.mouse.click(box["x"] + box["w"] * ratio, box["y"] + box["h"] / 2)
        return True

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
            # CDP 模式下新开标签，不抢用户正在看的页面
            page = ctx.new_page() if self.cdp_url else (
                ctx.pages[0] if ctx.pages else ctx.new_page()
            )
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

                # 编辑器挂载（标题框出现）—— 此时上传仍在后台进行
                page.wait_for_selector(TITLE_INPUT, timeout=120000)
                page.fill(TITLE_INPUT, title[:20])

                # 正文是 tiptap 富文本，fill() 对 contenteditable 不可靠，
                # 用 click + insert_text 走真实输入路径
                if page.locator(BODY_EDITOR).count():
                    page.click(BODY_EDITOR)
                    page.keyboard.insert_text(content)
                else:
                    logger.warning("未找到正文编辑器，仅设置了标题")

                # 等上传真正完成（标题框出现只代表编辑器挂载，不代表可提交）
                btn_name = BTN_PUBLISH if mode == "publish" else BTN_DRAFT
                if not self._wait_submit_ready(page, upload_timeout_s):
                    page.screenshot(path=shot, full_page=True)
                    return PublishResult(
                        False, mode,
                        f"等待上传完成超时（{upload_timeout_s}s），提交按钮未就绪", shot,
                    )

                if not self._click_submit(page, mode):
                    page.screenshot(path=shot, full_page=True)
                    return PublishResult(False, mode, "定位提交按钮失败", shot)
                page.wait_for_timeout(6000)

                # 校验：点完应离开发布页，或出现相应提示
                body_text = page.inner_text("body")[:800]
                left_page = "/publish/publish" not in page.url
                ok = left_page or (
                    "草稿" in body_text if mode == "draft" else "发布成功" in body_text
                )
                if not ok:
                    page.screenshot(path=shot, full_page=True)
                    return PublishResult(
                        False, mode,
                        f"点击「{btn_name}」后未确认预期结果 URL={page.url[:80]}", shot,
                    )

                logger.info("小红书视频已%s: %s", "发布" if mode == "publish" else "存草稿", title[:24])
                return PublishResult(True, mode)

            except Exception as e:
                try:
                    page.screenshot(path=shot, full_page=True)
                except Exception:
                    shot = None
                return PublishResult(False, mode, str(e)[:400], shot)
            finally:
                self._release(ctx)
