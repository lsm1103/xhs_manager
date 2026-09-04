"""MoneyPrinterTurbo 集成 — 素材搜索、TTS、完整视频生成。

MoneyPrinterTurbo 支持两种使用模式:
  1. 素材模式: --stop-at materials，只搜索下载视频素材
  2. 完整模式: 从主题到最终 MP4 的一站式生成

安装路径: /Users/xm/Desktop/xm_project/code/ai_agent_project/MoneyPrinterTurbo
CLI: python cli.py --video-subject "主题"
API: POST http://127.0.0.1:8080/api/v1/videos (需要 x-api-key: xma)
"""

import json
import logging
import subprocess
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# MoneyPrinterTurbo 默认安装路径
DEFAULT_MPT_PATH = (
    "/Users/xm/Desktop/xm_project/code/ai_agent_project/MoneyPrinterTurbo"
)


class MoneyPrinterTurbo:
    """MoneyPrinterTurbo 封装，通过 CLI 或 Python 调用。"""

    def __init__(self, install_path: str = DEFAULT_MPT_PATH) -> None:
        self.install_path = Path(install_path)
        self.cli_path = self.install_path / "cli.py"
        self.storage_path = self.install_path / "storage"
        self.tasks_path = self.storage_path / "tasks"
        # 优先使用项目自带 venv 的 python
        venv_python = self.install_path / ".venv" / "bin" / "python"
        self.python = str(venv_python) if venv_python.exists() else "python"

    @property
    def available(self) -> bool:
        return self.cli_path.exists()

    # ── 素材搜索（只下载素材，不合成视频）──────────────────────

    def search_materials(
        self,
        search_terms: list[str],
        video_aspect: str = "9:16",
        source: str = "pexels",
        clip_duration: int = 5,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """搜索并下载视频素材，返回素材文件列表。

        使用 --stop-at materials 模式，只执行到素材下载阶段。
        """
        task_id = task_id or str(uuid.uuid4())
        terms_str = ",".join(search_terms)

        cmd = [
            self.python, str(self.cli_path),
            "--video-subject", terms_str,  # 作为搜索输入
            "--video-terms", terms_str,
            "--video-source", source,
            "--video-aspect", video_aspect,
            "--video-clip-duration", str(clip_duration),
            "--stop-at", "materials",
            "--task-id", task_id,
            "--no-subtitle-enabled",
        ]

        result = self._run_cli(cmd, timeout=120)
        if not result["success"]:
            return result

        # 收集下载的素材文件
        task_dir = self.tasks_path / task_id
        materials = self._collect_task_files(task_dir, extensions=[".mp4", ".webm"])

        return {
            "success": True,
            "task_id": task_id,
            "task_dir": str(task_dir),
            "materials": materials,
            "count": len(materials),
        }

    # ── TTS 语音生成 ──────────────────────────────────────────

    def generate_tts(
        self,
        script_text: str,
        voice_name: str = "zh-CN-XiaoxiaoNeural-Female",
        voice_rate: float = 1.0,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """生成 TTS 语音和字幕，返回音频文件路径。

        使用 --stop-at audio 模式。
        """
        task_id = task_id or str(uuid.uuid4())

        cmd = [
            self.python, str(self.cli_path),
            "--video-script", script_text,
            "--voice-name", voice_name,
            "--voice-rate", str(voice_rate),
            "--stop-at", "subtitle",  # 生成到字幕阶段（包含音频）
            "--task-id", task_id,
        ]

        result = self._run_cli(cmd, timeout=60)
        if not result["success"]:
            return result

        task_dir = self.tasks_path / task_id
        audio_file = task_dir / "audio.mp3"
        subtitle_file = task_dir / "subtitle.srt"

        return {
            "success": True,
            "task_id": task_id,
            "audio_path": str(audio_file) if audio_file.exists() else None,
            "subtitle_path": str(subtitle_file) if subtitle_file.exists() else None,
        }

    # ── 完整视频生成 ──────────────────────────────────────────

    def generate_video(
        self,
        subject: str | None = None,
        script: str | None = None,
        video_aspect: str = "9:16",
        source: str = "pexels",
        voice_name: str = "zh-CN-XiaoxiaoNeural-Female",
        voice_rate: float = 1.0,
        bgm_type: str = "random",
        bgm_volume: float = 0.2,
        paragraph_number: int = 1,
        clip_duration: int = 5,
        transition_mode: str = "shuffle",
        subtitle_enabled: bool = True,
        font_size: int = 60,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """一站式生成完整视频，返回最终视频文件路径。"""
        task_id = task_id or str(uuid.uuid4())

        cmd = [
            self.python, str(self.cli_path),
            "--video-source", source,
            "--video-aspect", video_aspect,
            "--voice-name", voice_name,
            "--voice-rate", str(voice_rate),
            "--bgm-type", bgm_type,
            "--bgm-volume", str(bgm_volume),
            "--paragraph-number", str(paragraph_number),
            "--video-clip-duration", str(clip_duration),
            "--video-transition-mode", transition_mode,
            "--font-size", str(font_size),
            "--task-id", task_id,
        ]

        if subject:
            cmd.extend(["--video-subject", subject])
        elif script:
            cmd.extend(["--video-script", script])
        else:
            return {"success": False, "error": "必须提供 subject 或 script"}

        if not subtitle_enabled:
            cmd.append("--no-subtitle-enabled")

        result = self._run_cli(cmd, timeout=300)
        if not result["success"]:
            return result

        task_dir = self.tasks_path / task_id
        final_videos = self._collect_task_files(task_dir, extensions=[".mp4"], prefix="final")

        if not final_videos:
            return {
                "success": False,
                "task_id": task_id,
                "error": "视频生成完成但未找到输出文件",
            }

        return {
            "success": True,
            "task_id": task_id,
            "task_dir": str(task_dir),
            "video_path": final_videos[0]["path"],
            "video_count": len(final_videos),
            "videos": final_videos,
        }

    # ── 从脚本直接生成视频（快速路径）────────────────────────

    def generate_from_script(
        self,
        script: str,
        search_terms: list[str] | None = None,
        video_aspect: str = "9:16",
        source: str = "pexels",
        voice_name: str = "zh-CN-XiaoxiaoNeural-Female",
        transition_mode: str = "shuffle",
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """从已有脚本生成视频，跳过 LLM 脚本生成阶段。"""
        task_id = task_id or str(uuid.uuid4())

        cmd = [
            self.python, str(self.cli_path),
            "--video-script", script,
            "--video-source", source,
            "--video-aspect", video_aspect,
            "--voice-name", voice_name,
            "--video-transition-mode", transition_mode,
            "--task-id", task_id,
        ]

        if search_terms:
            cmd.extend(["--video-terms", ",".join(search_terms)])

        result = self._run_cli(cmd, timeout=300)
        if not result["success"]:
            return result

        task_dir = self.tasks_path / task_id
        final_videos = self._collect_task_files(task_dir, extensions=[".mp4"], prefix="final")

        return {
            "success": bool(final_videos),
            "task_id": task_id,
            "task_dir": str(task_dir),
            "video_path": final_videos[0]["path"] if final_videos else None,
            "videos": final_videos,
        }

    # ── 内部方法 ──────────────────────────────────────────────

    def _run_cli(self, cmd: list[str], timeout: int = 120) -> dict[str, Any]:
        """执行 MoneyPrinterTurbo CLI 命令。"""
        logger.info("执行 MoneyPrinterTurbo: %s", " ".join(cmd[:6]) + "...")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=str(self.install_path),
            )

            if result.returncode == 0:
                logger.info("MoneyPrinterTurbo 执行成功")
                return {"success": True, "stdout": result.stdout}
            else:
                error_msg = result.stderr[:1000] or result.stdout[:1000]
                logger.error("MoneyPrinterTurbo 失败: %s", error_msg[:200])
                return {"success": False, "error": error_msg}

        except subprocess.TimeoutExpired:
            logger.error("MoneyPrinterTurbo 超时 (%ds)", timeout)
            return {"success": False, "error": f"执行超时 ({timeout}s)"}
        except Exception as e:
            logger.error("MoneyPrinterTurbo 异常: %s", e)
            return {"success": False, "error": str(e)}

    def _collect_task_files(
        self,
        task_dir: Path,
        extensions: list[str],
        prefix: str | None = None,
    ) -> list[dict[str, Any]]:
        """收集任务目录中的文件。"""
        files = []
        if not task_dir.exists():
            return files

        for f in sorted(task_dir.iterdir()):
            if not f.is_file():
                continue
            if f.suffix.lower() not in extensions:
                continue
            if prefix and not f.stem.startswith(prefix):
                continue
            files.append({
                "path": str(f),
                "name": f.name,
                "size": f.stat().st_size,
                "size_mb": round(f.stat().st_size / 1024 / 1024, 2),
            })

        return files

    def get_task_script(self, task_id: str) -> dict[str, Any] | None:
        """读取任务的 script.json。"""
        script_file = self.tasks_path / task_id / "script.json"
        if script_file.exists():
            return json.loads(script_file.read_text(encoding="utf-8"))
        return None
