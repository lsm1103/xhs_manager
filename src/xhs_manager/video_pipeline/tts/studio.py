"""走 TTS Studio HTTP 服务的配音提供方。

为什么是 HTTP 而不是直接 import：
  IndexTTS-2 (MLX) 装在自己的 venv 里（mlx-indextts），本进程 import 不到。
  Studio 已经用常驻 worker 把它托住了——加载一次 54s，之后每次调用免加载。
  流水线要是自己起进程，等于每条片子重付一次加载成本；实测单次 CLI 调用
  117s，常驻后 1.7s。所以这里只做一件事：把文本丢给已经热着的那个服务。

代价是流水线多了一个外部依赖。服务没起来时 available() 返回 False，
registry 会回落到 edge——宁可音色差，不能整条流水线卡住。
"""

import logging
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from xhs_manager.video_pipeline.tts.base import TtsProvider, TtsRequest, TtsResult

logger = logging.getLogger(__name__)

# BgmMood → IndexTTS-2 的 8 维情感。脚本里标一次情绪，配乐和配音同时吃到。
# 不求精确对应，只求别把"揭示"念得像"铺垫"。
MOOD_TO_EMOTION = {
    "hook": "surprised",
    "explain": "calm",
    "tension": "melancholic",
    "reveal": "surprised",
    "uplift": "happy",
    "closing": "happy",
}


class StudioTtsProvider(TtsProvider):
    name = "studio"
    supports_cloning = True
    supports_mood = True

    def __init__(
        self,
        base_url: str,
        model_id: str,
        ref_audio: str = "",
        emotion: str = "",
        emo_alpha: Optional[float] = None,
        ref_text: str = "",
        instruct: str = "",
        timeout: int = 600,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model_id = model_id
        self.ref_audio = ref_audio
        self.emotion = emotion
        self.emo_alpha = emo_alpha
        self.ref_text = ref_text
        self.instruct = instruct
        self.timeout = timeout

    # ── 可用性 ────────────────────────────────────────────────

    def available(self) -> tuple[bool, str]:
        """服务在跑、且目标模型已经 ready 才算可用。

        模型 status 不是 ready 时不主动去 load：加载要 54s，
        在流水线中途静默阻塞一分钟，比直接回落更难排查。
        """
        try:
            data = self._get_json("/api/models")
        except Exception as e:
            return False, f"TTS Studio 不可达 ({self.base_url}): {e}"

        for m in data.get("models", []):
            if m["id"] != self.model_id:
                continue
            if m["status"] != "ready":
                return False, f"{m['name']} 未加载（当前 {m['status']}），请先在 Studio 里加载"
            if self.model_id == "indextts2" and not self.ref_audio:
                return False, f"{m['name']} 需要参考音频，但未配置 tts_studio_ref"
            return True, ""
        return False, f"Studio 没有模型 {self.model_id}"

    # ── 合成 ──────────────────────────────────────────────────

    def synthesize(self, req: TtsRequest) -> TtsResult:
        emotion = self.emotion or MOOD_TO_EMOTION.get(req.mood or "", "")
        payload = {
            "model_id": self.model_id,
            "text": req.text,
            "ref_audio": (
                Path(req.ref_audio).name if req.ref_audio else self.ref_audio
            ) or None,
            "emotion": emotion or None,
            "emo_alpha": self.emo_alpha,
            "ref_text": self.ref_text or None,
            "instruct": self.instruct or None,
            "speed": req.speed,
        }

        started = time.monotonic()
        try:
            rec = self._post_json("/api/generate", payload)
        except urllib.error.HTTPError as e:
            return TtsResult(False, provider=self.name,
                             error=f"HTTP {e.code}: {e.read().decode(errors='replace')[:300]}")
        except Exception as e:
            return TtsResult(False, provider=self.name, error=str(e)[:300])

        if rec.get("status") != "ok":
            return TtsResult(False, provider=self.name,
                             error=rec.get("error") or "生成失败")

        # Studio 和流水线跑在同一台机器上，但产物落在 Studio 自己的目录，
        # 仍然走 HTTP 取回：这样将来 Studio 挪到别的机器也不用改这里。
        try:
            self._download(rec["audio_url"], req.output_path)
        except Exception as e:
            return TtsResult(False, provider=self.name, error=f"下载音频失败: {e}")

        return TtsResult(
            True,
            path=req.output_path,
            provider=self.name,
            duration=rec.get("duration"),
            elapsed=rec.get("elapsed") or round(time.monotonic() - started, 2),
            meta={
                "model_id": self.model_id,
                "ref_audio": rec.get("ref_audio"),
                "emotion": emotion,
                "generation_id": rec.get("id"),
            },
        )

    # ── HTTP 细节 ─────────────────────────────────────────────

    def _get_json(self, path: str) -> dict:
        import json

        with urllib.request.urlopen(self.base_url + path, timeout=10) as r:
            return json.loads(r.read())

    def _post_json(self, path: str, payload: dict) -> dict:
        import json

        body = json.dumps(payload).encode()
        r = urllib.request.Request(
            self.base_url + path, data=body,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(r, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def _download(self, url: str, dst: Path) -> None:
        """取回音频。IndexTTS 出的是 wav，调用方通常要 .mp3——
        扩展名和内容不一致会让后续 concat 的 ffmpeg 报莫名其妙的错，所以按需转码。
        """
        import subprocess
        import tempfile

        dst.parent.mkdir(parents=True, exist_ok=True)
        src_ext = Path(url).suffix or ".wav"

        with tempfile.NamedTemporaryFile(suffix=src_ext, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            with urllib.request.urlopen(self.base_url + url, timeout=120) as r:
                shutil.copyfileobj(r, tmp)

        try:
            if src_ext.lower() == dst.suffix.lower():
                shutil.move(str(tmp_path), str(dst))
                return
            cmd = ["ffmpeg", "-y", "-i", str(tmp_path),
                   "-c:a", "libmp3lame", "-b:a", "192k", str(dst)]
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
            if p.returncode != 0 or not dst.exists():
                raise RuntimeError(f"转码失败: {p.stderr[-300:]}")
        finally:
            tmp_path.unlink(missing_ok=True)
