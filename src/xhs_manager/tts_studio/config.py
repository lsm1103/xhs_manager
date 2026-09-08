"""TTS Studio 配置。"""

from pathlib import Path
from typing import Any

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
AI_ROOT = PROJECT_ROOT.parent


class StudioSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TTS_STUDIO_", extra="ignore")

    host: str = "127.0.0.1"
    port: int = 8420

    db_path: str = "data/tts_studio.db"
    audio_dir: str = "data/tts_studio/audio"
    upload_dir: str = "data/tts_studio/refs"

    # 启动时自动加载的模型 id（逗号分隔）。
    # IndexTTS 加载要 54 秒，放这里可以让服务起来就绪；
    # 但也会让启动变慢，按需取舍。
    autoload: list[str] = Field(default_factory=lambda: ["edge"])

    # 各模型路径
    voxcpm_cli: str = str(Path.home() / "models/llama.cpp-omni/build/bin/voxcpm2-cli")
    voxcpm_base: str = str(Path.home() / "models/voxcpm2-gguf/VoxCPM2-BaseLM-Q8_0.gguf")
    voxcpm_acoustic: str = str(Path.home() / "models/voxcpm2-gguf/VoxCPM2-Acoustic-F16.gguf")

    indextts_repo: str = str(AI_ROOT / "mlx-indextts")
    indextts_model: str = str(AI_ROOT / "mlx-indextts/models/IndexTTS-2-MLX")

    @property
    def indextts_python(self) -> str:
        return str(Path(self.indextts_repo) / ".venv/bin/python")


def get_settings() -> StudioSettings:
    return StudioSettings()
