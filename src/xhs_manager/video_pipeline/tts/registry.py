"""TTS 提供方注册与逐级回落。

流水线只调 synthesize()，不关心底层用了哪个引擎。
本地模型（VoxCPM 等）有失败率，官方自己都建议"生成 1~3 次"，
所以按优先级依次尝试，任一成功即返回，全失败才报错。
"""

import logging
from pathlib import Path
from typing import Optional

from xhs_manager.video_pipeline.tts.base import TtsProvider, TtsRequest, TtsResult

logger = logging.getLogger(__name__)


def build_providers(settings) -> list[TtsProvider]:
    """按配置构造提供方链，顺序即优先级。"""
    from xhs_manager.video_pipeline.tts.edge import EdgeTtsProvider

    chain: list[TtsProvider] = []
    preferred = (getattr(settings, "tts_provider", "") or "edge").lower()

    if preferred == "voxcpm":
        try:
            from xhs_manager.video_pipeline.tts.voxcpm import VoxCpmProvider
            chain.append(VoxCpmProvider(
                cli_path=getattr(settings, "voxcpm_cli", ""),
                base_model=getattr(settings, "voxcpm_base_model", ""),
                acoustic_model=getattr(settings, "voxcpm_acoustic_model", ""),
                ref_audio=getattr(settings, "voxcpm_ref_audio", ""),
                ref_text=getattr(settings, "voxcpm_ref_text", ""),
            ))
        except ImportError as e:
            logger.warning("VoxCPM 提供方不可用: %s", e)

    # edge 永远兜底：它免费、稳定、无外部依赖
    chain.append(EdgeTtsProvider(voice=getattr(settings, "tts_voice", "zh-CN-XiaoxiaoNeural")))
    return chain


def get_provider(name: str, settings) -> Optional[TtsProvider]:
    for p in build_providers(settings):
        if p.name == name:
            return p
    return None


def synthesize(
    text: str,
    output_path: Path,
    settings,
    *,
    mood: Optional[str] = None,
    speed: float = 1.0,
) -> TtsResult:
    """按优先级尝试各提供方，返回第一个成功的结果。"""
    req = TtsRequest(
        text=text, output_path=Path(output_path), mood=mood, speed=speed,
    )
    errors: list[str] = []
    for provider in build_providers(settings):
        ok, why = provider.available()
        if not ok:
            errors.append(f"{provider.name}: {why}")
            continue
        res = provider.synthesize(req)
        if res.success:
            if res.rtf is not None:
                logger.info(
                    "TTS[%s] %.1fs 音频 / %.1fs 耗时 (RTF %.2f)",
                    provider.name, res.duration or 0, res.elapsed or 0, res.rtf,
                )
            return res
        errors.append(f"{provider.name}: {res.error[:120]}")
        logger.warning("TTS[%s] 失败，尝试下一个: %s", provider.name, res.error[:120])

    return TtsResult(False, error="所有提供方均失败 | " + " | ".join(errors))
