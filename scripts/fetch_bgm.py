#!/usr/bin/env python
"""从 Incompetech 抓一批现代都市风 BGM 到 data/bgm/，并落盘授权信息。

为什么换曲库：MoneyPrinterTurbo 自带的 29 首谱心全在 430-597Hz、energy 0.53-0.65，
是同一个低沉氛围风格包。情绪分类（按库内百分位）能把它们摊开成 6 格，
但摊的是同一种风格——听感上永远不是科技风。曲风问题只能换曲源解决。

为什么是 Incompetech：
  - 有机器可读的全量目录 `pieces.json`（1442 首，带 genre / bpm / feel / instruments）
  - 直链下载，不需要 API key、不过 Cloudflare（Pixabay Music 走 curl 直接 403）
  - 授权明确：CC BY 4.0，可商用，但**必须署名**（见 data/bgm/CREDITS.md）

授权是有代价的：CC BY 要求在作品里注明出处。发小红书时得把 CREDITS.md 里的
署名行放进正文。要免署名得去 filmmusic.io 买断，那是另一条路。
"""

import argparse
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

CATALOG = "https://incompetech.com/music/royalty-free/pieces.json"
DOWNLOAD_BASE = "https://incompetech.com/music/royalty-free/mp3-royaltyfree/"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# Incompetech 的 genre 是数字编码，目录里没有对照表，是逐个采样标题/feel 认出来的。
# 只收「现代都市」这一挂：电子、放克、慵懒爵士、驱动感摇滚。
# 刻意不收 22(史诗管弦)、20(拉格泰姆)、9(节日)、24(华尔兹)——
# 不是它们不好，是段与段之间切过去会有风格断裂感，一条 60 秒的片子撑不住。
#
# 只用 Electronica 一个流派曾经是对的（当时要解决的是曲库全是低沉氛围包），
# 但 141 首里按 bpm 桶去重后只能挑出 20 首，情绪格子里每格就两三首，
# 选曲又是纯 argmax，结果就是每条片子都在放同样那几首。广度必须靠多流派补。
GENRES: dict[str, tuple[str, int]] = {
    "7": ("Electronica", 28),
    "11": ("Lounge / Jazz", 12),
    "8": ("Funk / Soul", 10),
    "19": ("Rock / Driving", 10),
}

# 曲库要在 tempo/energy/brightness 上摊得够开，自动打标才分得出 6 个情绪。
# 所以按 bpm 分桶取，而不是按热度取前 N 首——否则会全挤在一个速度区间。
BPM_BUCKETS = [(0, 85), (85, 105), (105, 125), (125, 150), (150, 999)]
MIN_SECONDS = 100      # 乐段最长可能接近 50 秒，曲子太短不够裁


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _seconds(length: str) -> int:
    parts = [int(p) for p in length.split(":")] if length else [0]
    s = 0
    for p in parts:
        s = s * 60 + p
    return s


def select(pieces: list[dict], scale: float = 1.0) -> list[dict]:
    """逐流派、按 bpm 分桶挑选，桶内按 feel 去重，保证风格不扎堆。

    scale 放大各流派配额，用来在已有曲库基础上继续扩容。
    """
    picked: list[dict] = []

    for genre, (label, quota) in GENRES.items():
        quota = max(1, int(round(quota * scale)))
        pool = [
            p for p in pieces
            if p.get("genre") == genre
            and _seconds(p.get("length", "")) >= MIN_SECONDS
            and (p.get("bpm") or "0").isdigit() and int(p["bpm"]) > 0
        ]
        per_bucket = max(1, -(-quota // len(BPM_BUCKETS)))   # 向上取整
        got: list[dict] = []

        for lo, hi in BPM_BUCKETS:
            bucket = sorted(
                (p for p in pool if lo <= int(p["bpm"]) < hi),
                key=lambda p: -_seconds(p["length"]),
            )
            seen_feel: set[str] = set()
            taken = 0
            for p in bucket:
                # 同一组 feel 只取一首，避免 "Raving Energy" 和 "Raving Energy (faster)"
                # 这种同曲变体占满一个桶
                key = (p.get("feel", "")
                       + re.sub(r"\s*\(.*\)$", "", p["title"]).strip()[:6])
                if key in seen_feel:
                    continue
                seen_feel.add(key)
                got.append(p)
                taken += 1
                if taken >= per_bucket:
                    break

        print(f"  {label}: 候选 {len(pool)} 首 → 选中 {len(got)} 首")
        picked.extend(got)

    return picked


def download(picked: list[dict], dest: Path) -> list[dict]:
    dest.mkdir(parents=True, exist_ok=True)
    ok: list[dict] = []
    for p in picked:
        fn = p["filename"]
        out = dest / fn
        if out.exists() and out.stat().st_size > 10000:
            print(f"  已存在 {fn}")
            ok.append(p)
            continue
        url = DOWNLOAD_BASE + urllib.parse.quote(fn)
        try:
            data = _get(url)
        except Exception as e:
            print(f"  下载失败 {fn}: {e}")
            continue
        if len(data) < 10000:
            print(f"  内容异常 {fn} ({len(data)} bytes)")
            continue
        out.write_bytes(data)
        print(f"  {fn}  {len(data) / 1024 / 1024:.1f}MB  bpm={p['bpm']}  {p.get('feel', '')}")
        ok.append(p)
    return ok


def write_credits(tracks: list[dict], dest: Path) -> None:
    """生成署名清单。CC BY 4.0 要求注明出处，这个文件就是发布时要复制的内容。"""
    lines = [
        "# BGM 授权与署名",
        "",
        "全部来自 Incompetech（Kevin MacLeod），授权为 **CC BY 4.0**：",
        "可商用、可改编、可用于变现的短视频，**但必须署名**。",
        "",
        "发布时把用到的曲子对应的署名行复制进正文/简介。",
        "官方要求的格式见 <https://incompetech.com/music/royalty-free/faq.html>。",
        "",
        "## 署名行（按曲名）",
        "",
    ]
    for t in sorted(tracks, key=lambda x: x["title"]):
        title = t["title"].strip()
        lines.append(
            f'- `{t["filename"]}` — "{title}" Kevin MacLeod (incompetech.com), '
            f"Licensed under Creative Commons: By Attribution 4.0 "
            f"https://creativecommons.org/licenses/by/4.0/"
        )
    lines += [
        "",
        "## 元信息",
        "",
        "| 文件 | 曲名 | BPM | 时长 | Feel | ISRC |",
        "|---|---|---|---|---|---|",
    ]
    for t in sorted(tracks, key=lambda x: int(x["bpm"])):
        lines.append(
            f'| {t["filename"]} | {t["title"].strip()} | {t["bpm"]} | '
            f'{t["length"]} | {t.get("feel", "")} | {t.get("isrc", "")} |'
        )
    lines += [
        "",
        "> 不想署名的话，可以去 filmmusic.io 为单曲购买免署名授权，",
        "> 那属于另一条授权路径，与本文件无关。",
        "",
    ]
    (dest / "CREDITS.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default="data/bgm")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="放大各流派配额，用于在已有曲库上继续扩容")
    args = ap.parse_args()

    print("拉取目录…")
    pieces = json.loads(_get(CATALOG))
    print(f"目录共 {len(pieces)} 首")
    print("按流派选曲：")

    picked = select(pieces, args.scale)
    print(f"选中 {len(picked)} 首，开始下载：")
    got = download(picked, Path(args.dest))

    write_credits(got, Path(args.dest))
    print(f"完成：{len(got)} 首 → {args.dest}/，署名清单 {args.dest}/CREDITS.md")
    return 0 if got else 1


if __name__ == "__main__":
    raise SystemExit(main())
