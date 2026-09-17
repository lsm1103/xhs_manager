"""配音模型的配置：各模型的路径、产物目录、开机自动加载哪些。

服务本身的配置（端口、数据库）不在这里——配音已经并入主服务。
"""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]
AI_ROOT = PROJECT_ROOT.parent


class StudioSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="TTS_STUDIO_", extra="ignore")

    # 不再有 host/port/db_path：配音已经并进主服务（/tts），
    # 历史记录也搬进了主库的 tts_generations 表。
    # 留下的只是模型路径和产物目录。
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
    omnivoice_repo: str = str(Path.home() / "models/omnivoice-runtime")
    omnivoice_model: str = str(Path.home() / "models/OmniVoice")
    omnivoice_device: str = "mps"
    omnivoice_dtype: str = "float16"

    @property
    def indextts_python(self) -> str:
        return str(Path(self.indextts_repo) / ".venv/bin/python")

    @property
    def omnivoice_python(self) -> str:
        return str(Path(self.omnivoice_repo) / ".venv/bin/python")


def get_settings() -> StudioSettings:
    return StudioSettings()
