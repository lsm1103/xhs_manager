"""TTS 提供方抽象 —— 让配音引擎可替换，且失败能逐级回落。"""

from xhs_manager.video_pipeline.tts.base import TtsProvider, TtsRequest, TtsResult
from xhs_manager.video_pipeline.tts.registry import get_provider, synthesize

__all__ = ["TtsProvider", "TtsRequest", "TtsResult", "get_provider", "synthesize"]
