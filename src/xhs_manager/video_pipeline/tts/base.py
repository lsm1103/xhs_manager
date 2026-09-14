"""TTS 提供方接口。

为什么要这层抽象：
  当前用 edge-tts（免费、稳定、但没有情感控制）。要换成 VoxCPM2 这类
  可克隆音色 + 带情绪指令的本地模型时，不该去改 Stage3/Stage5 的业务代码。
  同时本地模型有失败率（官方自己建议"生成 1~3 次"），必须能回落到 edge-tts，
  否则一次抖动就毁掉整条流水线。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class TtsRequest:
    """一段待合成的语音。"""

    text: str
    output_path: Path
    # 情绪提示。edge-tts 会忽略；VoxCPM2 之类支持自然语言指令的会用上。
    # 取值与 BgmMood 保持一致，这样脚本里标一次情绪，配乐和配音都能用。
    mood: Optional[str] = None
    # 音色克隆的参考音频（本地模型用）
    ref_audio: Optional[Path] = None
    ref_text: Optional[str] = None
    speed: float = 1.0


@dataclass
class SpeechMark:
    """一段语音在音频里的真实位置（秒）。

    字幕对齐的唯一可靠依据。没有它就只能按字数比例估算，
    而估算误差会在一个场景内累积，听感上就是"字幕追不上人声"。
    """

    start: float
    duration: float
    text: str

    @property
    def end(self) -> float:
        return self.start + self.duration


@dataclass
class TtsResult:
    success: bool
    path: Optional[Path] = None
    provider: str = ""
    duration: Optional[float] = None
    elapsed: Optional[float] = None      # 生成耗时，用于算 RTF
    error: str = ""
    #: 引擎回吐的句级时间标记（拿不到就是空列表，调用方需要能降级）
    marks: list["SpeechMark"] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def rtf(self) -> Optional[float]:
        """实时率：生成耗时 / 音频时长。<1 表示快于实时。"""
        if self.duration and self.elapsed and self.duration > 0:
            return round(self.elapsed / self.duration, 3)
        return None


class TtsProvider(ABC):
    """一个配音引擎。"""

    name: str = "base"
    #: 是否支持用参考音频克隆音色
    supports_cloning: bool = False
    #: 是否支持情绪/风格控制
    supports_mood: bool = False

    @abstractmethod
    def available(self) -> tuple[bool, str]:
        """能否使用。返回 (可用, 不可用原因)。"""

    @abstractmethod
    def synthesize(self, req: TtsRequest) -> TtsResult:
        """合成一段语音。实现方不应抛异常，失败要返回 success=False。"""
