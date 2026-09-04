"""多平台热点采集后端。

设计原则:
  - 公开 API 优先（无需登录、稳定、快）
  - opencli 作为需登录平台的通道（依赖 Chrome 扩展）
  - 每个采集器独立，单个失败不影响其他平台
  - 统一输出 TrendItem 结构
"""

import json
import logging
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

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

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
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
# 采集器注册表
# ═══════════════════════════════════════════════════════════════


def get_collector(platform: str):
    """按平台名返回对应采集器实例。"""
    if platform == "bilibili":
        return BilibiliCollector()
    if platform == "v2ex":
        return V2exCollector()
    if platform in OpenCliCollector.SEARCH_COMMANDS:
        return OpenCliCollector(platform)
    return None


def collect_platform(
    platform: str, keywords: list[str], limit_per_keyword: int,
) -> list[TrendItem]:
    """采集单个平台的热点，自动选择可用后端。"""
    collector = get_collector(platform)
    if collector is None:
        logger.warning("未知平台: %s", platform)
        return []

    if not collector.available():
        logger.warning("平台 %s 后端不可用（跳过）", platform)
        return []

    items: list[TrendItem] = []

    # 优先取热榜（如果支持）
    if hasattr(collector, "collect_hot"):
        items.extend(collector.collect_hot(limit_per_keyword))

    # 再按关键词搜索
    if hasattr(collector, "collect_search"):
        for kw in keywords:
            items.extend(collector.collect_search(kw, limit_per_keyword))

    return items
