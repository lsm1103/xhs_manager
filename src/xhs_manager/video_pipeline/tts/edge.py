"""Edge TTS —— 微软 Edge 内置朗读服务。

免费、无需 API key、稳定，但**不支持情感控制**，中文只有 8 个音色。
作为基准和 fallback 保留。
"""

import asyncio
import logging
import shutil
import subprocess
import time

from xhs_manager.video_pipeline.tts.base import (
    SpeechMark,
    TtsProvider,
    TtsRequest,
    TtsResult,
)

logger = logging.getLogger(__name__)

# 实测可用的中文音色（edge_tts.list_voices 查询所得）
ZH_VOICES = (
    "zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural",
    "zh-CN-YunjianNeural", "zh-CN-YunxiNeural",
    "zh-CN-YunxiaNeural", "zh-CN-YunyangNeural",
    "zh-CN-liaoning-XiaobeiNeural", "zh-CN-shaanxi-XiaoniNeural",
)


class EdgeTtsProvider(TtsProvider):
    name = "edge"
    supports_cloning = False
    supports_mood = False   # 这正是"没感情"的根因

    def __init__(self, voice: str = "zh-CN-XiaoxiaoNeural") -> None:
        self.voice = voice

    def available(self) -> tuple[bool, str]:
        """优先认 Python 包。

        注意不能只看 CLI 是否存在：pip 装了 edge-tts 库但没有可执行文件的
        情况很常见，那样 available() 会误报可用、真正调用时才失败，
        导致 fallback 形同虚设。
        """
        try:
            import edge_tts  # noqa: F401
            return True, ""
        except ImportError:
            pass
        if shutil.which("edge-tts"):
            return True, ""
        return False, "未安装 edge-tts（pip install edge-tts）"

    def synthesize(self, req: TtsRequest) -> TtsResult:
        req.output_path.parent.mkdir(parents=True, exist_ok=True)
        rate = f"{int((req.speed - 1) * 100):+d}%"

        t0 = time.monotonic()
        marks: list[SpeechMark] = []
        err = self._via_python(req, rate, marks)
        if err and shutil.which("edge-tts"):
            marks = []          # CLI 通道拿不到时间标记
            err = self._via_cli(req, rate)
        elapsed = time.monotonic() - t0

        if err or not req.output_path.exists():
            return TtsResult(False, provider=self.name, error=(err or "输出文件不存在")[:300])

        from xhs_manager.video_pipeline.integrations.renderer import probe_duration
        return TtsResult(
            True, path=req.output_path, provider=self.name,
            duration=probe_duration(req.output_path), elapsed=round(elapsed, 2),
            marks=marks,
            meta={"voice": self.voice},
        )

    def _via_python(
        self, req: TtsRequest, rate: str, marks: list[SpeechMark],
    ) -> str:
        """用 Python API 合成。返回空字符串表示成功。

        这里不用 Communicate.save()，而是自己消费 stream()：
        save() 会把边界事件丢掉，而中文语音的 SentenceBoundary
        （offset/duration，单位 100 纳秒）正是字幕精确对齐的依据。
        """
        try:
            import edge_tts
        except ImportError as e:
            return str(e)

        async def _run() -> None:
            comm = edge_tts.Communicate(req.text, self.voice, rate=rate)
            with open(req.output_path, "wb") as fh:
                async for chunk in comm.stream():
                    kind = chunk.get("type")
                    if kind == "audio":
                        fh.write(chunk["data"])
                    elif kind in ("SentenceBoundary", "WordBoundary"):
                        marks.append(SpeechMark(
                            start=chunk["offset"] / 1e7,
                            duration=chunk["duration"] / 1e7,
                            text=chunk.get("text", ""),
                        ))

        try:
            asyncio.run(_run())
            return ""
        except Exception as e:
            marks.clear()
            return f"{type(e).__name__}: {e}"

    def _via_cli(self, req: TtsRequest, rate: str) -> str:
        cmd = [
            "edge-tts", "--voice", self.voice, "--rate", rate,
            "--text", req.text, "--write-media", str(req.output_path),
        ]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except Exception as e:
            return str(e)
        return "" if r.returncode == 0 else (r.stderr or r.stdout)[-300:]
