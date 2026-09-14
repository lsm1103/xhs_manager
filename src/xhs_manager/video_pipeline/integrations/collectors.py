"""多平台热点采集后端。

设计原则:
  - 每个平台是一条**后端链**，不是单一后端。前一级打不通就自动降级，
    而不是整个平台归零——这是本模块最重要的一条约束。
  - 链的排序按数据质量：站内 API > 登录态通道（opencli / cookie）> 站外索引。
  - 站外索引（DuckDuckGo + 360）是保证「任何平台都有话可说」的兜底：
    拿不到互动数据，但拿得到标题、链接和摘要。
  - 单个平台失败不影响其他平台；采集结果会带上「谁采的、为什么降级」。

后端矩阵（截至当前）:
  bilibili   站内公开 API              → 索引
  v2ex       站内公开 API              → 索引
  toutiao    热榜公开 API（搜索走索引）
  baidu      热搜公开 API
  wechat     搜狗微信搜索（免登录）
  zhihu      ZHIHU_COOKIE 站内 API     → 索引
  weibo      WEIBO_COOKIE 站内 API     → 索引
  xiaohongshu / douyin / twitter
             opencli 浏览器通道        → 索引
  juejin / 36kr  仅索引
"""

import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"


@dataclass
class TrendItem:
    """统一的热点条目结构。"""

    platform: str
    title: str
    url: str
    summary: str = ""
    author: Optional[str] = None
    likes: int = 0
    comments: int = 0
    shares: int = 0
    views: int = 0
    tags: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def engagement(self) -> dict[str, int]:
        d = {}
        if self.likes:
            d["likes"] = self.likes
        if self.comments:
            d["comments"] = self.comments
        if self.shares:
            d["shares"] = self.shares
        if self.views:
            d["views"] = self.views
        return d


def _strip_html(text: str) -> str:
    """移除 HTML 标签（B站搜索结果的标题带 <em> 高亮标记）。"""
    return re.sub(r"<[^>]+>", "", text or "").strip()


# ═══════════════════════════════════════════════════════════════
# B站 — 公开 API，无需登录
# ═══════════════════════════════════════════════════════════════


class BilibiliCollector:
    """B站采集器，使用公开 HTTP API。"""

    platform = "bilibili"
    BASE_HEADERS = {"User-Agent": UA, "Referer": "https://www.bilibili.com"}

    def available(self) -> bool:
        try:
            with httpx.Client(timeout=10) as c:
                r = c.get(
                    "https://api.bilibili.com/x/web-interface/popular",
                    params={"ps": 1, "pn": 1},
                    headers=self.BASE_HEADERS,
                )
                return r.json().get("code") == 0
        except Exception:
            return False

    def collect_hot(self, limit: int = 20) -> list[TrendItem]:
        """获取热门视频榜。"""
        items: list[TrendItem] = []
        try:
            with httpx.Client(timeout=20) as c:
                r = c.get(
                    "https://api.bilibili.com/x/web-interface/popular",
                    params={"ps": min(limit, 50), "pn": 1},
                    headers=self.BASE_HEADERS,
                )
                data = r.json()
                if data.get("code") != 0:
                    logger.warning("B站热门 API 返回 code=%s", data.get("code"))
                    return items

                for v in data.get("data", {}).get("list", [])[:limit]:
                    stat = v.get("stat", {})
                    items.append(TrendItem(
                        platform=self.platform,
                        title=v.get("title", ""),
                        url=f"https://www.bilibili.com/video/{v.get('bvid','')}",
                        summary=v.get("desc", "") or v.get("title", ""),
                        author=v.get("owner", {}).get("name"),
                        likes=stat.get("like", 0),
                        comments=stat.get("reply", 0),
                        shares=stat.get("share", 0),
                        views=stat.get("view", 0),
                        tags=[v.get("tname", "")] if v.get("tname") else [],
                        raw=v,
                    ))
        except Exception as e:
            logger.warning("B站热门采集失败: %s", e)
        return items

    def collect_search(self, keyword: str, limit: int = 20) -> list[TrendItem]:
        """按关键词搜索视频。"""
        items: list[TrendItem] = []
        try:
            with httpx.Client(timeout=20) as c:
                r = c.get(
                    "https://api.bilibili.com/x/web-interface/wbi/search/type",
                    params={"search_type": "video", "keyword": keyword, "page": 1},
                    headers=self.BASE_HEADERS,
                )
                data = r.json()
                if data.get("code") != 0:
                    return items

                for v in data.get("data", {}).get("result", [])[:limit]:
                    items.append(TrendItem(
                        platform=self.platform,
                        title=_strip_html(v.get("title", "")),
                        url=v.get("arcurl", ""),
                        summary=v.get("description", "") or _strip_html(v.get("title", "")),
                        author=v.get("author"),
                        likes=v.get("like", 0),
                        comments=v.get("review", 0),
                        views=v.get("play", 0),
                        tags=[t for t in (v.get("tag") or "").split(",") if t][:5],
                        raw=v,
                    ))
        except Exception as e:
            logger.warning("B站搜索「%s」失败: %s", keyword, e)
        return items


# ═══════════════════════════════════════════════════════════════
# V2EX — 公开 API，无需登录
# ═══════════════════════════════════════════════════════════════


class V2exCollector:
    """V2EX 采集器，技术圈讨论热点。"""

    platform = "v2ex"

    def available(self) -> bool:
        try:
            with httpx.Client(timeout=10) as c:
                return c.get("https://www.v2ex.com/api/topics/hot.json").status_code == 200
        except Exception:
            return False

    def collect_hot(self, limit: int = 20) -> list[TrendItem]:
        items: list[TrendItem] = []
        try:
            with httpx.Client(timeout=20) as c:
                r = c.get(
                    "https://www.v2ex.com/api/topics/hot.json",
                    headers={"User-Agent": UA},
                )
                for t in r.json()[:limit]:
                    items.append(TrendItem(
                        platform=self.platform,
                        title=t.get("title", ""),
                        url=t.get("url", ""),
                        summary=t.get("content", "")[:500] or t.get("title", ""),
                        author=(t.get("member") or {}).get("username"),
                        comments=t.get("replies", 0),
                        tags=[(t.get("node") or {}).get("title", "")],
                        raw=t,
                    ))
        except Exception as e:
            logger.warning("V2EX 采集失败: %s", e)
        return items

    def collect_search(self, keyword: str, limit: int = 20) -> list[TrendItem]:
        """按关键词全文检索 V2EX。

        V2EX 官方 API 没有搜索，站外索引又基本没收录它的正文；
        sov2ex 是社区维护的 V2EX 全文索引，公开免登录，正好补上这个洞。
        """
        items: list[TrendItem] = []
        try:
            with httpx.Client(timeout=25) as c:
                r = c.get(
                    "https://www.sov2ex.com/api/search",
                    params={"q": keyword, "size": limit, "sort": "created"},
                    headers={"User-Agent": UA},
                )
            for hit in r.json().get("hits", [])[:limit]:
                src = hit.get("_source", {})
                items.append(TrendItem(
                    platform=self.platform,
                    title=src.get("title", ""),
                    url=f"https://www.v2ex.com/t/{src.get('id','')}",
                    summary=(src.get("content") or src.get("title", ""))[:800],
                    author=src.get("member"),
                    comments=_as_int(src.get("replies")),
                    tags=["sov2ex", src.get("created", "")[:10]],
                    raw=src,
                ))
        except Exception as e:
            logger.warning("sov2ex 搜索「%s」失败: %s", keyword, e)
        return items


# ═══════════════════════════════════════════════════════════════
# opencli 通道 — 需要 Chrome 扩展
# ═══════════════════════════════════════════════════════════════


class OpenCliCollector:
    """通过 opencli 采集需登录的平台（小红书/抖音/X）。

    真实命令格式（T01 验证）:
        opencli <platform> search "<query>" --limit N -f json
        opencli twitter trending -f json
    """

    # 平台 → (子命令, 是否需要 query 参数)
    SEARCH_COMMANDS = {
        "xiaohongshu": ("search", True),
        "douyin": ("search", True),
        "twitter": ("search", True),
    }

    def __init__(self, platform: str) -> None:
        self.platform = platform

    def available(self) -> bool:
        """检查 opencli 扩展是否连接。"""
        try:
            r = subprocess.run(
                ["opencli", "doctor"],
                capture_output=True, text=True, timeout=30,
            )
            return "Extension: connected" in r.stdout or "[OK] Extension" in r.stdout
        except Exception:
            return False

    def collect_search(self, keyword: str, limit: int = 20) -> list[TrendItem]:
        sub, _ = self.SEARCH_COMMANDS.get(self.platform, ("search", True))
        cmd = ["opencli", self.platform, sub, keyword, "--limit", str(limit), "-f", "json"]

        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0:
                logger.warning(
                    "opencli %s 搜索失败: %s", self.platform, r.stderr[:200] or r.stdout[:200],
                )
                return []
            return self._parse(r.stdout)
        except subprocess.TimeoutExpired:
            logger.warning("opencli %s 搜索超时", self.platform)
            return []
        except Exception as e:
            logger.warning("opencli %s 异常: %s", self.platform, e)
            return []

    def _parse(self, stdout: str) -> list[TrendItem]:
        """解析 opencli JSON 输出。"""
        items: list[TrendItem] = []
        text = stdout.strip()
        if not text:
            return items

        data = _parse_leading_json(text)
        if data is None:
            logger.debug("opencli 输出非 JSON: %s", text[:200])
            return items

        # opencli 输出可能是 {ok, data:[...]} 或直接数组
        rows = data
        if isinstance(data, dict):
            if data.get("ok") is False:
                logger.warning("opencli 返回错误: %s", data.get("error", {}))
                return items
            rows = data.get("data") or data.get("results") or data.get("items") or []

        if not isinstance(rows, list):
            return items

        for row in rows:
            if not isinstance(row, dict):
                continue
            items.append(TrendItem(
                platform=self.platform,
                title=row.get("title") or row.get("text", "")[:100],
                url=row.get("url") or row.get("link", ""),
                summary=(row.get("desc") or row.get("content")
                         or row.get("text") or row.get("title", "")),
                author=row.get("author") or row.get("user"),
                likes=_as_int(row.get("likes") or row.get("like_count")),
                comments=_as_int(row.get("comments") or row.get("comment_count")),
                shares=_as_int(row.get("shares") or row.get("retweets")),
                views=_as_int(row.get("views") or row.get("play_count")),
                raw=row,
            ))
        return items



def _parse_leading_json(text: str):
    """解析 stdout 开头的 JSON，忽略其后追加的非 JSON 文本。

    opencli 会把 "Update available: v1.8.6 → v1.8.7 / Run: npm install ..."
    提示直接追加在 JSON 之后输出到 stdout，整段 json.loads 必然失败。
    """
    text = text.lstrip()
    if not text or text[0] not in "[{":
        return None
    try:
        obj, _ = json.JSONDecoder().raw_decode(text)
        return obj
    except json.JSONDecodeError:
        return None

def _as_int(v: Any) -> int:
    """把 '1.2万' / '1234' / 1234 统一转成 int。"""
    if isinstance(v, (int, float)):
        return int(v)
    if not isinstance(v, str):
        return 0
    v = v.strip()
    try:
        if v.endswith("万"):
            return int(float(v[:-1]) * 10000)
        if v.endswith("亿"):
            return int(float(v[:-1]) * 100000000)
        return int(float(re.sub(r"[^\d.]", "", v) or 0))
    except ValueError:
        return 0


# ═══════════════════════════════════════════════════════════════
# 站外索引通道 — 免登录，覆盖所有「站内 API 打不通」的平台
# ═══════════════════════════════════════════════════════════════


class _Throttle:
    """进程内最小请求间隔，避免把免费搜索引擎打到限流。"""

    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self._last = 0.0

    def wait(self) -> None:
        gap = time.monotonic() - self._last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        self._last = time.monotonic()


_DDG_THROTTLE = _Throttle(6.0)
_SO360_THROTTLE = _Throttle(1.5)

# DDG 是唯一一个真正支持 site: 限定的免费索引源，所以必须当稀缺资源用：
# 连续空结果说明已经被限流，此时继续打只会延长封禁，直接进入冷却。
_DDG_COOLDOWN_S = 180.0
_DDG_STATE = {"cool_until": 0.0, "misses": 0}
_SEARCH_CACHE: dict[str, list[tuple[str, str, str]]] = {}
# 被风控的引擎在本次进程内不再重试，并把原因暴露给诊断信息
_SEARCH_STATE = {"ddg_blocked": False, "so360_blocked": False}

SEARCH_HEADERS = {"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9"}


def _ddg_fetch(query: str, limit: int) -> list[tuple[str, str, str]]:
    """单次 DDG HTML 查询。"""
    _DDG_THROTTLE.wait()
    try:
        with httpx.Client(timeout=40, follow_redirects=True) as c:
            r = c.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
                headers=SEARCH_HEADERS,
            )
        if _looks_blocked(r.text):
            _SEARCH_STATE["ddg_blocked"] = True
        soup = BeautifulSoup(r.text, "html.parser")
        rows: list[tuple[str, str, str]] = []
        for res in soup.select("div.result")[:limit]:
            a = res.select_one("a.result__a")
            if not a:
                continue
            url = a.get("href", "")
            if not url.startswith("http"):
                continue
            snippet = res.select_one(".result__snippet")
            rows.append((
                a.get_text(" ", strip=True),
                url,
                snippet.get_text(" ", strip=True) if snippet else "",
            ))
        return rows
    except Exception as e:
        logger.debug("DDG 查询「%s」失败: %s", query, e)
        return []


def _ddg_search(query: str, limit: int) -> list[tuple[str, str, str]]:
    """DuckDuckGo HTML 端点，带节流、重试与冷却。返回 (title, url, snippet)。

    DDG 限流时返回 202 + 空结果页而不是报错，所以「解析到 0 条」就是限流信号。
    连续两次空结果即进入冷却，冷却期内直接放弃 DDG 让调用方走 360 兜底，
    避免一次采集把几十个查询全打在已经限流的端点上。
    """
    if query in _SEARCH_CACHE:
        return _SEARCH_CACHE[query]

    if time.monotonic() < _DDG_STATE["cool_until"]:
        return []

    rows = _ddg_fetch(query, limit)
    if not rows:
        time.sleep(12.0)
        rows = _ddg_fetch(query, limit)

    if rows:
        _DDG_STATE["misses"] = 0
        _SEARCH_CACHE[query] = rows
    else:
        _DDG_STATE["misses"] += 1
        if _DDG_STATE["misses"] >= 2:
            _DDG_STATE["cool_until"] = time.monotonic() + _DDG_COOLDOWN_S
            _DDG_STATE["misses"] = 0
            logger.warning("DDG 疑似限流，冷却 %.0fs，本轮改走 360 兜底", _DDG_COOLDOWN_S)
    return rows


# 搜索引擎的风控页返回 200，内容却是验证码。识别出来才能如实说明
# 「不是没搜到，是被风控了」——否则诊断信息会误导人去改查询词。
_CAPTCHA_MARKS = ("请输入验证码", "访问异常页面", "unusual traffic", "Just a moment")


def _looks_blocked(text: str) -> bool:
    return any(mark in text for mark in _CAPTCHA_MARKS)


def _so360_search(query: str, limit: int) -> list[tuple[str, str, str]]:
    """360 搜索（中文语料覆盖好）。真实 URL 在 data-mdurl 上。"""
    _SO360_THROTTLE.wait()
    try:
        with httpx.Client(timeout=40, follow_redirects=True) as c:
            r = c.get(
                "https://www.so.com/s",
                params={"q": query},
                headers=SEARCH_HEADERS,
            )
        if _looks_blocked(r.text):
            logger.warning("360 搜索触发风控验证码，本轮索引通道不可用")
            _SEARCH_STATE["so360_blocked"] = True
            return []
        soup = BeautifulSoup(r.text, "html.parser")
        rows: list[tuple[str, str, str]] = []
        for li in soup.select("li.res-list")[:limit * 2]:
            a = li.select_one("h3 a")
            if not a:
                continue
            url = a.get("data-mdurl") or ""
            if not url.startswith("http"):
                continue  # 跳过 so.com/link 跳板与站内 AI 卡片
            desc = li.select_one("p.res-desc") or li.select_one("p")
            rows.append((
                a.get_text(" ", strip=True),
                url,
                desc.get_text(" ", strip=True) if desc else "",
            ))
            if len(rows) >= limit:
                break
        return rows
    except Exception as e:
        logger.debug("360 查询「%s」失败: %s", query, e)
        return []


# 平台 → 站外索引用的域名与中文站名
PLATFORM_SITES = {
    "zhihu": ("zhihu.com", "知乎"),
    "weibo": ("weibo.com", "微博"),
    "xiaohongshu": ("xiaohongshu.com", "小红书"),
    "douyin": ("douyin.com", "抖音"),
    "twitter": ("x.com", "推特"),
    "bilibili": ("bilibili.com", "B站"),
    "toutiao": ("toutiao.com", "今日头条"),
    "juejin": ("juejin.cn", "掘金"),
    "36kr": ("36kr.com", "36氪"),
    "v2ex": ("v2ex.com", "V2EX"),
    "web": ("", "全网"),          # 不限站点的通用检索
}


class SearchIndexCollector:
    """站外索引通道：用公共搜索引擎按站点限定抓某平台的公开讨论。

    存在的理由：小红书/微博/知乎/抖音/X 的站内接口都要登录态，
    一旦 opencli 浏览器通道断了，这些平台就整体归零。
    索引通道拿不到互动数据，但能拿到标题、链接和摘要——
    对「这个话题各平台在说什么」这个问题，这已经够用了。
    """

    def __init__(self, platform: str) -> None:
        self.platform = platform
        self.site, self.site_cn = PLATFORM_SITES.get(platform, (platform, platform))

    @property
    def name(self) -> str:
        return f"索引(ddg/360):{self.site}"

    def available(self) -> bool:
        """两个引擎都被风控时才算不可用，否则让实际查询去判断。"""
        return not (_SEARCH_STATE["ddg_blocked"] and _SEARCH_STATE["so360_blocked"])

    def _matches_site(self, url: str) -> bool:
        """严格域名校验。

        索引通道最大的失真风险是：搜「小红书 AI 创业」，返回的全是
        知乎和公众号**讨论小红书**的文章，然后被当成小红书样本入库。
        平台标签错了，后面所有结论都是脏的，所以这里宁可返回空，
        也不把跨站结果冒充成该平台的内容。
        """
        if not self.site:
            return True
        host = urllib.parse.urlsplit(url).hostname or ""
        return host == self.site or host.endswith("." + self.site)

    def collect_search(self, keyword: str, limit: int = 20) -> list[TrendItem]:
        query = f"site:{self.site} {keyword}" if self.site else keyword
        rows = [(t, u, s, "ddg") for t, u, s in _ddg_search(query, limit)]
        if not rows:
            fallback = f"{keyword} {self.site_cn}" if self.site else keyword
            rows = [(t, u, s, "360") for t, u, s in _so360_search(fallback, limit)]
        rows = [r for r in rows if self._matches_site(r[1])]

        return [
            TrendItem(
                platform=self.platform,
                title=title,
                url=url,
                summary=snippet or title,
                tags=["索引通道"],
                raw={"engine": engine, "query": keyword, "via": "search-index"},
            )
            for title, url, snippet, engine in rows
        ]


# ═══════════════════════════════════════════════════════════════
# 免登录热榜 — 今日头条 / 百度
# ═══════════════════════════════════════════════════════════════


class ToutiaoCollector:
    """今日头条热榜。公开接口，免登录。"""

    platform = "toutiao"
    name = "头条热榜 API"

    def available(self) -> bool:
        try:
            with httpx.Client(timeout=10) as c:
                r = c.get(
                    "https://www.toutiao.com/hot-event/hot-board/",
                    params={"origin": "toutiao_pc"},
                    headers={"User-Agent": UA},
                )
                return bool(r.json().get("data"))
        except Exception:
            return False

    def collect_hot(self, limit: int = 20) -> list[TrendItem]:
        items: list[TrendItem] = []
        try:
            with httpx.Client(timeout=20) as c:
                r = c.get(
                    "https://www.toutiao.com/hot-event/hot-board/",
                    params={"origin": "toutiao_pc"},
                    headers={"User-Agent": UA},
                )
            for row in r.json().get("data", [])[:limit]:
                items.append(TrendItem(
                    platform=self.platform,
                    title=row.get("Title", ""),
                    url=row.get("Url", ""),
                    summary=row.get("Title", ""),
                    views=_as_int(row.get("HotValue")),
                    tags=["热榜"],
                    raw=row,
                ))
        except Exception as e:
            logger.warning("头条热榜采集失败: %s", e)
        return items

    def collect_search(self, keyword: str, limit: int = 20) -> list[TrendItem]:
        # 头条搜索接口要 ms_token，免登录拿不到；改走索引通道
        return SearchIndexCollector("toutiao").collect_search(keyword, limit)


class BaiduHotCollector:
    """百度热搜榜。公开接口，免登录。"""

    platform = "baidu"
    name = "百度热搜 API"
    BOARDS = ("realtime", "novel", "teleplay")

    def available(self) -> bool:
        try:
            with httpx.Client(timeout=10) as c:
                r = c.get(
                    "https://top.baidu.com/api/board",
                    params={"platform": "wise", "tab": "realtime"},
                    headers={"User-Agent": UA},
                )
                return r.json().get("success") is True
        except Exception:
            return False

    def collect_hot(self, limit: int = 20) -> list[TrendItem]:
        items: list[TrendItem] = []
        try:
            with httpx.Client(timeout=20) as c:
                r = c.get(
                    "https://top.baidu.com/api/board",
                    params={"platform": "wise", "tab": "realtime"},
                    headers={"User-Agent": UA},
                )
            for card in r.json().get("data", {}).get("cards", []):
                for row in card.get("content", [])[:limit]:
                    items.append(TrendItem(
                        platform=self.platform,
                        title=row.get("word") or row.get("query", ""),
                        url=row.get("url") or row.get("rawUrl", ""),
                        summary=row.get("desc") or row.get("word", ""),
                        views=_as_int(row.get("hotScore")),
                        tags=["热搜"],
                        raw=row,
                    ))
        except Exception as e:
            logger.warning("百度热搜采集失败: %s", e)
        return items[:limit]


class WeixinCollector:
    """微信公众号文章 — 走搜狗微信搜索，免登录。"""

    platform = "wechat"
    name = "搜狗微信搜索"

    def available(self) -> bool:
        try:
            with httpx.Client(timeout=15, follow_redirects=True) as c:
                r = c.get(
                    "https://weixin.sogou.com/weixin",
                    params={"type": 2, "query": "AI"},
                    headers=SEARCH_HEADERS,
                )
            return bool(BeautifulSoup(r.text, "html.parser").select("ul.news-list li"))
        except Exception:
            return False

    def collect_search(self, keyword: str, limit: int = 20) -> list[TrendItem]:
        items: list[TrendItem] = []
        try:
            with httpx.Client(timeout=30, follow_redirects=True) as c:
                r = c.get(
                    "https://weixin.sogou.com/weixin",
                    params={"type": 2, "query": keyword},
                    headers=SEARCH_HEADERS,
                )
            soup = BeautifulSoup(r.text, "html.parser")
            for li in soup.select("ul.news-list li")[:limit]:
                a = li.select_one("h3 a")
                if not a:
                    continue
                href = a.get("href", "")
                if href.startswith("/link"):
                    href = "https://weixin.sogou.com" + href
                account = li.select_one(".account")
                desc = li.select_one("p.txt-info")
                items.append(TrendItem(
                    platform=self.platform,
                    title=a.get_text(" ", strip=True),
                    url=href,
                    summary=desc.get_text(" ", strip=True) if desc else "",
                    author=account.get_text(" ", strip=True) if account else None,
                    tags=["公众号"],
                    raw={"query": keyword, "via": "sogou-weixin"},
                ))
        except Exception as e:
            logger.warning("搜狗微信搜索「%s」失败: %s", keyword, e)
        return items


# ═══════════════════════════════════════════════════════════════
# Cookie 通道 — 有登录态时质量最高，没有就跳过
# ═══════════════════════════════════════════════════════════════


class ZhihuCookieCollector:
    """知乎站内 API。需要环境变量 ZHIHU_COOKIE（浏览器里复制整条 Cookie）。"""

    platform = "zhihu"
    name = "知乎 API(cookie)"

    def __init__(self) -> None:
        self.cookie = os.environ.get("ZHIHU_COOKIE", "").strip()

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": UA, "Cookie": self.cookie, "Referer": "https://www.zhihu.com/"}

    def available(self) -> bool:
        if not self.cookie:
            return False
        try:
            with httpx.Client(timeout=15) as c:
                r = c.get(
                    "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total",
                    params={"limit": 5},
                    headers=self._headers(),
                )
            return r.status_code == 200
        except Exception:
            return False

    def collect_hot(self, limit: int = 20) -> list[TrendItem]:
        items: list[TrendItem] = []
        try:
            with httpx.Client(timeout=25) as c:
                r = c.get(
                    "https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total",
                    params={"limit": min(limit, 50)},
                    headers=self._headers(),
                )
            for row in r.json().get("data", [])[:limit]:
                target = row.get("target", {})
                items.append(TrendItem(
                    platform=self.platform,
                    title=target.get("title", ""),
                    url=target.get("url", "") or row.get("link", {}).get("url", ""),
                    summary=target.get("excerpt", "") or target.get("title", ""),
                    views=_as_int((row.get("detail_text") or "").replace("万热度", "万")),
                    tags=["热榜"],
                    raw=row,
                ))
        except Exception as e:
            logger.warning("知乎热榜采集失败: %s", e)
        return items

    def collect_search(self, keyword: str, limit: int = 20) -> list[TrendItem]:
        items: list[TrendItem] = []
        try:
            with httpx.Client(timeout=25) as c:
                r = c.get(
                    "https://www.zhihu.com/api/v4/search_v3",
                    params={"t": "general", "q": keyword, "offset": 0, "limit": limit},
                    headers=self._headers(),
                )
            for row in r.json().get("data", [])[:limit]:
                obj = row.get("object") or {}
                title = _strip_html(obj.get("title") or obj.get("question", {}).get("name", ""))
                if not title:
                    continue
                items.append(TrendItem(
                    platform=self.platform,
                    title=title,
                    url=obj.get("url", ""),
                    summary=_strip_html(obj.get("excerpt", "")) or title,
                    author=(obj.get("author") or {}).get("name"),
                    likes=_as_int(obj.get("voteup_count")),
                    comments=_as_int(obj.get("comment_count")),
                    raw=row,
                ))
        except Exception as e:
            logger.warning("知乎搜索「%s」失败: %s", keyword, e)
        return items


class WeiboCookieCollector:
    """微博站内 API（m 站）。需要环境变量 WEIBO_COOKIE。"""

    platform = "weibo"
    name = "微博 API(cookie)"

    def __init__(self) -> None:
        self.cookie = os.environ.get("WEIBO_COOKIE", "").strip()

    def _headers(self) -> dict[str, str]:
        return {"User-Agent": UA, "Cookie": self.cookie, "Referer": "https://m.weibo.cn/"}

    def available(self) -> bool:
        if not self.cookie:
            return False
        try:
            with httpx.Client(timeout=15) as c:
                r = c.get(
                    "https://m.weibo.cn/api/container/getIndex",
                    params={"containerid": "102803"},
                    headers=self._headers(),
                )
            return r.status_code == 200 and "cards" in r.text
        except Exception:
            return False

    def collect_search(self, keyword: str, limit: int = 20) -> list[TrendItem]:
        items: list[TrendItem] = []
        try:
            with httpx.Client(timeout=30) as c:
                r = c.get(
                    "https://m.weibo.cn/api/container/getIndex",
                    params={
                        "containerid": f"100103type=1&q={keyword}",
                        "page_type": "searchall",
                    },
                    headers=self._headers(),
                )
            for card in r.json().get("data", {}).get("cards", []):
                blog = card.get("mblog")
                if not blog:
                    continue
                text = _strip_html(blog.get("text", ""))
                items.append(TrendItem(
                    platform=self.platform,
                    title=text[:100],
                    url=f"https://m.weibo.cn/detail/{blog.get('id','')}",
                    summary=text,
                    author=(blog.get("user") or {}).get("screen_name"),
                    likes=_as_int(blog.get("attitudes_count")),
                    comments=_as_int(blog.get("comments_count")),
                    shares=_as_int(blog.get("reposts_count")),
                    raw=blog,
                ))
                if len(items) >= limit:
                    break
        except Exception as e:
            logger.warning("微博搜索「%s」失败: %s", keyword, e)
        return items


# ═══════════════════════════════════════════════════════════════
# 采集器注册表 — 每个平台一条后端链，前面的打不通就往后降级
# ═══════════════════════════════════════════════════════════════


def _browser(platform: str):
    """延迟构造浏览器采集器：playwright 导入慢，没用到就不要付这个成本。"""
    from xhs_manager.video_pipeline.integrations.browser_collector import (
        BrowserSearchCollector,
    )

    return BrowserSearchCollector(platform)


@dataclass
class CollectOutcome:
    """单平台的采集结果，带上「谁采到的、为什么降级」。"""

    platform: str
    items: list[TrendItem] = field(default_factory=list)
    backend: str = ""
    status: str = "unavailable"      # ok=首选后端 | degraded=降级后端 | unavailable
    notes: list[str] = field(default_factory=list)


# 后端链按「数据质量」排序：站内 API > 登录态通道 > 站外索引
PLATFORM_BACKENDS: dict[str, list] = {
    "bilibili": [BilibiliCollector, lambda: SearchIndexCollector("bilibili")],
    "v2ex": [V2exCollector, lambda: SearchIndexCollector("v2ex")],
    "toutiao": [ToutiaoCollector],
    "baidu": [BaiduHotCollector],
    "wechat": [WeixinCollector],
    "zhihu": [
        ZhihuCookieCollector,
        lambda: _browser("zhihu"),
        lambda: SearchIndexCollector("zhihu"),
    ],
    "weibo": [
        WeiboCookieCollector,
        lambda: _browser("weibo"),
        lambda: SearchIndexCollector("weibo"),
    ],
    "xiaohongshu": [
        lambda: OpenCliCollector("xiaohongshu"),
        lambda: _browser("xiaohongshu"),
        lambda: SearchIndexCollector("xiaohongshu"),
    ],
    "douyin": [
        lambda: OpenCliCollector("douyin"),
        lambda: SearchIndexCollector("douyin"),
    ],
    "twitter": [
        lambda: OpenCliCollector("twitter"),
        lambda: SearchIndexCollector("twitter"),
    ],
    "juejin": [lambda: SearchIndexCollector("juejin")],
    "36kr": [lambda: SearchIndexCollector("36kr")],
    # 不限站点的全网检索。跨站的高相关结果归到这里，
    # 而不是硬塞给某个平台去污染它的样本。
    "web": [lambda: SearchIndexCollector("web")],
}

SUPPORTED_PLATFORMS = tuple(PLATFORM_BACKENDS)


def _backend_name(backend) -> str:
    name = getattr(backend, "name", None)
    return name if isinstance(name, str) else type(backend).__name__


def get_collector(platform: str):
    """按平台名返回首选采集器实例（保留旧接口）。"""
    chain = PLATFORM_BACKENDS.get(platform)
    return chain[0]() if chain else None


def backend_chain(platform: str) -> list:
    """实例化某平台的后端链。"""
    return [factory() for factory in PLATFORM_BACKENDS.get(platform, [])]


def probe_platform(platform: str) -> list[tuple[str, bool]]:
    """诊断用：返回该平台每个后端的可用性。"""
    return [(_backend_name(b), b.available()) for b in backend_chain(platform)]


def collect_platform(
    platform: str,
    keywords: list[str],
    limit_per_keyword: int,
    include_hot: bool = True,
) -> CollectOutcome:
    """采集单个平台，沿后端链逐级降级，返回首个拿到数据的后端结果。

    include_hot=False 时只按关键词搜索、不取平台热榜。做**定向话题调研**
    时必须关掉热榜：今日热榜上是婚育观和男篮，和话题毫无关系，
    混进来只会稀释选题阶段的信号。
    """
    chain = backend_chain(platform)
    if not chain:
        return CollectOutcome(platform, notes=[f"未知平台: {platform}"])

    outcome = CollectOutcome(platform)
    for idx, backend in enumerate(chain):
        name = _backend_name(backend)
        if not backend.available():
            outcome.notes.append(f"{name} 不可用")
            continue

        items: list[TrendItem] = []
        if include_hot and hasattr(backend, "collect_hot"):
            items.extend(backend.collect_hot(limit_per_keyword))
        if hasattr(backend, "collect_search"):
            for kw in keywords:
                items.extend(backend.collect_search(kw, limit_per_keyword))

        if items:
            outcome.items = items
            outcome.backend = name
            outcome.status = "ok" if idx == 0 else "degraded"
            return outcome
        outcome.notes.append(f"{name} 无结果")

    return outcome
