"""场景素材的识别与度量。

单独成一个模块是因为 builder 和 layouts 都要用它，而 builder 本身要 import
layouts —— 把 SceneMedia 留在 builder 里，两边就成环了。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

VIDEO_SUFFIXES = (".mp4", ".webm", ".mov", ".m4v")
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".avif")

# 转场时长（秒）。glitch 短促才有冲击力，其余统一 0.6。
TRANSITION_DURATION = {"glitch": 0.4, "none": 0.0}
DEFAULT_TRANSITION_DURATION = 0.6


@dataclass(frozen=True)
class SceneMedia:
    """一个场景的背景素材。filename 是 assets/ 下的文件名。

    width/height 是**源文件的像素尺寸**，只对图片有值，探不出来就是 None。
    screenshot 版面靠它把作者按截图量出来的像素框换算成比例框——
    有了比例，同一份脚本换任何画布尺寸都不用改坐标。
    """

    filename: str
    kind: str  # "video" | "image"
    width: int | None = None
    height: int | None = None


def probe_image_size(path: str | Path) -> tuple[int, int] | None:
    """只读文件头取图片尺寸。认不出来返回 None。

    故意不用 Pillow：composition 目前零三方依赖，为了读两个整数
    拉进一个图像库不划算，而且 .venv 里本来就没装。
    ffprobe 能干这活，但那是每张图起一个子进程。
    """
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if len(head) < 24:
                return None

            # PNG: IHDR 紧跟在 8 字节签名之后
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return (
                    int.from_bytes(head[16:20], "big"),
                    int.from_bytes(head[20:24], "big"),
                )

            # GIF
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return (
                    int.from_bytes(head[6:8], "little"),
                    int.from_bytes(head[8:10], "little"),
                )

            # WebP：三种子格式的尺寸位置各不相同
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                fourcc = head[12:16]
                if fourcc == b"VP8X":
                    return (
                        int.from_bytes(head[24:27], "little") + 1,
                        int.from_bytes(head[27:30], "little") + 1,
                    )
                if fourcc == b"VP8 ":
                    return (
                        int.from_bytes(head[26:28], "little") & 0x3FFF,
                        int.from_bytes(head[28:30], "little") & 0x3FFF,
                    )
                if fourcc == b"VP8L":
                    bits = int.from_bytes(head[21:25], "little")
                    return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)

            # JPEG：尺寸在 SOF 段里，只能顺着段链往下走
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    kind = marker[1]
                    size = f.read(2)
                    if len(size) < 2:
                        return None
                    seg_len = int.from_bytes(size, "big")
                    # SOF0-SOF15，跳过 DHT(C4)/JPG(C8)/DAC(CC) 这三个不是 SOF 的
                    if 0xC0 <= kind <= 0xCF and kind not in (0xC4, 0xC8, 0xCC):
                        body = f.read(5)
                        if len(body) < 5:
                            return None
                        return (
                            int.from_bytes(body[3:5], "big"),
                            int.from_bytes(body[1:3], "big"),
                        )
                    f.seek(seg_len - 2, 1)
    except OSError:
        return None
    return None


def classify_media(path: str | Path) -> SceneMedia | None:
    """按扩展名判断素材类型。不认识的扩展名返回 None（回落到纯色背景）。"""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return SceneMedia(filename=p.name, kind="video")
    if suffix in IMAGE_SUFFIXES:
        size = probe_image_size(p)
        return SceneMedia(
            filename=p.name,
            kind="image",
            width=size[0] if size else None,
            height=size[1] if size else None,
        )
    return None
