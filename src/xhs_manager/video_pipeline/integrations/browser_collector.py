"""站内浏览器采集通道 —— 用项目自带的 Playwright + 常驻 profile 抓登录态平台。

为什么需要它:
    小红书/知乎/微博/抖音的搜索接口都要登录态。原来这些平台只有 opencli
    一条路，而 opencli 依赖第三方 Chrome 扩展；扩展一断，这些平台整体归零。
    但项目本来就有 Playwright 和一套常驻 Chrome profile（xhs-login 用的那套），
    完全可以自己开搜索页取结果，不必依赖任何外部扩展。

代价要说清楚:
    - 需要用户**一次性**扫码登录：`cli site-login <platform>`，凭证落在
      每个平台各自的 profile 目录里。
    - 无头模式会被部分平台判风险（小红书会直接跳"IP 存在风险"），
      所以支持 headless=False 兜底。
    - 抓的是页面渲染结果，平台改版会失效——因此每个站点的抽取规则
      都写成一段独立 JS，坏了只需要改那一段。
"""

import logging
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

CHROME_PATH = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE_ROOT = Path.home() / ".xhs_pipeline_chrome"


@dataclass
class SiteSpec:
    """一个站点的搜索地址、登录入口、未登录特征与抽取脚本。"""

    name: str
    search_url: str          # 用 {q} 占位（已 URL 编码）
    login_url: str
    login_marks: tuple[str, ...]   # 出现在 URL 或正文里就说明没登录
    extract_js: str
    profile: str = ""        # 空=用 PROFILE_ROOT 根目录（沿用 xhs-login 的登录态）

    @property
    def profile_dir(self) -> Path:
        return PROFILE_ROOT / self.profile if self.profile else PROFILE_ROOT


_XHS_JS = """() => [...document.querySelectorAll('section.note-item')].map(el => ({
  title: el.querySelector('a.title')?.innerText?.trim()
      || el.querySelector('.footer .title')?.innerText?.trim() || '',
  url: (el.querySelector('a.cover') || el.querySelector('a'))?.href || '',
  author: el.querySelector('.author .name')?.innerText?.trim() || '',
  likes: el.querySelector('.like-wrapper .count')?.innerText?.trim() || '0',
})).filter(x => x.title && x.url)"""

_ZHIHU_JS = """() => [...document.querySelectorAll('.SearchResult-Card, .List-item')].map(el => ({
  title: el.querySelector('.ContentItem-title')?.innerText?.trim() || '',
  url: el.querySelector('.ContentItem-title a')?.href
      || el.querySelector('a')?.href || '',
  summary: el.querySelector('.RichText, .Highlight')?.innerText?.trim() || '',
  author: el.querySelector('.AuthorInfo-name')?.innerText?.trim() || '',
  likes: el.querySelector('.VoteButton--up')?.innerText?.replace(/[^0-9]/g, '') || '0',
  comments: el.querySelector('.ContentItem-actions button')?.innerText
      ?.replace(/[^0-9]/g, '') || '0',
})).filter(x => x.title)"""

_WEIBO_JS = """() => [...document.querySelectorAll('#pl_feedlist_index .card-wrap')].map(el => {
  const acts = [...el.querySelectorAll('.card-act li')].map(li => li.innerText.trim());
  const num = s => (s || '').replace(/[^0-9]/g, '') || '0';
  return {
    title: (el.querySelector('.content .txt')?.innerText || '').trim().slice(0, 100),
    summary: (el.querySelector('.content .txt')?.innerText || '').trim(),
    url: el.querySelector('.from a')?.href || '',
    author: el.querySelector('.content .name')?.innerText?.trim() || '',
    shares: num(acts[0]), comments: num(acts[1]), likes: num(acts[2]),
  };
}).filter(x => x.title)"""

SITE_SPECS: dict[str, SiteSpec] = {
    "xiaohongshu": SiteSpec(
        name="小红书",
        search_url="https://www.xiaohongshu.com/search_result?keyword={q}&type=51",
        login_url="https://www.xiaohongshu.com/explore",
        login_marks=("website-login", "IP存在风险", "登录后查看"),
        extract_js=_XHS_JS,
    ),
    "zhihu": SiteSpec(
        name="知乎",
        search_url="https://www.zhihu.com/search?q={q}&type=content",
        login_url="https://www.zhihu.com/signin",
        login_marks=("account/unhuman", "请您登录后查看", "signin"),
        extract_js=_ZHIHU_JS,
        profile="zhihu",
    ),
    "weibo": SiteSpec(
        name="微博",
        search_url="https://s.weibo.com/weibo?q={q}",
        login_url="https://weibo.com/login.php",
        login_marks=("扫描二维码登录", "passport.weibo.com"),
        extract_js=_WEIBO_JS,
        profile="weibo",
    ),
}


def _playwright_ready() -> bool:
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return Path(CHROME_PATH).exists()


class BrowserSession:
    """持久化 Chrome 上下文的薄封装，登录与采集共用。"""

    def __init__(self, spec: SiteSpec, headless: bool = True) -> None:
        self.spec = spec
        self.headless = headless

    def _context(self, pw, headless: Optional[bool] = None):
        self.spec.profile_dir.mkdir(parents=True, exist_ok=True)
        return pw.chromium.launch_persistent_context(
            str(self.spec.profile_dir),
            executable_path=CHROME_PATH,
            headless=self.headless if headless is None else headless,
            viewport={"width": 1440, "height": 1200},
            args=["--no-first-run", "--no-default-browser-check"],
        )

    def interactive_login(self, timeout_s: int = 300) -> bool:
        """打开有头窗口让用户扫码，登录态落到该平台自己的 profile。"""
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            ctx = self._context(pw, headless=False)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(self.spec.login_url, wait_until="domcontentloaded", timeout=60000)
                logger.info("请在浏览器窗口里完成 %s 登录（最多 %ds）…", self.spec.name, timeout_s)
                deadline = timeout_s * 1000
                step = 3000
                waited = 0
                while waited < deadline:
                    page.wait_for_timeout(step)
                    waited += step
                    if not self._looks_logged_out(page):
                        logger.info(
                            "%s 登录成功，凭证保存在 %s",
                            self.spec.name, self.spec.profile_dir,
                        )
                        return True
                logger.error("%s 登录等待超时", self.spec.name)
                return False
            finally:
                ctx.close()

    def _looks_logged_out(self, page) -> bool:
        blob = (page.url or "") + "\n" + (page.inner_text("body")[:3000] if page else "")
        return any(mark in blob for mark in self.spec.login_marks)

    def search(self, keyword: str, limit: int) -> tuple[list[dict[str, Any]], str]:
        """返回 (结果列表, 诊断信息)。"""
        from playwright.sync_api import sync_playwright

        url = self.spec.search_url.format(q=urllib.parse.quote(keyword))
        with sync_playwright() as pw:
            ctx = self._context(pw)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(5000)
                page.mouse.wheel(0, 3000)
                page.wait_for_timeout(2500)
                if self._looks_logged_out(page):
                    return [], (
                        f"{self.spec.name} 未登录或被判风险，"
                        f"请运行 site-login {self.spec.name}"
                    )
                rows = page.evaluate(self.spec.extract_js) or []
                return rows[:limit], ""
            except Exception as e:
                return [], f"{self.spec.name} 页面采集失败: {e}"
            finally:
                ctx.close()


class BrowserSearchCollector:
    """把 BrowserSession 包成采集器后端，接进平台后端链。"""

    def __init__(self, platform: str, headless: bool = True) -> None:
        self.platform = platform
        self.spec = SITE_SPECS[platform]
        self.session = BrowserSession(self.spec, headless=headless)
        self._note = ""

    @property
    def name(self) -> str:
        return f"浏览器站内({self.spec.name})"

    def available(self) -> bool:
        """只检查环境与 profile 是否存在，不真开浏览器（开一次要好几秒）。"""
        return _playwright_ready() and self.spec.profile_dir.exists()

    def collect_search(self, keyword: str, limit: int = 20):
        from xhs_manager.video_pipeline.integrations.collectors import TrendItem, _as_int

        rows, note = self.session.search(keyword, limit)
        if note:
            logger.warning(note)
            self._note = note
        return [
            TrendItem(
                platform=self.platform,
                title=(row.get("title") or "")[:200],
                url=row.get("url", ""),
                summary=row.get("summary") or row.get("title", ""),
                author=row.get("author") or None,
                likes=_as_int(row.get("likes")),
                comments=_as_int(row.get("comments")),
                shares=_as_int(row.get("shares")),
                tags=["站内"],
                raw=row,
            )
            for row in rows
        ]
