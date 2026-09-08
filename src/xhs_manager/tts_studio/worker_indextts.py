"""IndexTTS-2 常驻 worker —— 在 mlx-indextts 的 venv 里独立进程运行。

为什么要独立进程：
  1. IndexTTS 依赖 mlx/torch/transformers，装进主项目 venv 会污染依赖
  2. 模型加载要 54 秒，常驻内存后每次生成省掉这段时间
  3. 崩溃不会拖垮 FastAPI 主进程

协议：stdin 读 JSON 行，stdout 写 JSON 行。
"""

import contextlib
import json
import os
import sys
import time
from pathlib import Path


def emit(obj):
    """只有协议消息能走 stdout。"""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


@contextlib.contextmanager
def quiet():
    """把第三方库的 print 重定向到 stderr。

    IndexTTSv2 加载和生成时会往 stdout 打印进度（"Loading GPT v2..."），
    这些会被父进程当成 JSON 协议响应读走，导致解析失败。
    """
    with contextlib.redirect_stdout(sys.stderr):
        yield


def main():
    repo = os.environ.get("INDEXTTS_REPO", "")
    model_dir = os.environ.get("INDEXTTS_MODEL", "")
    if repo:
        sys.path.insert(0, repo)
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    tts = None
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            emit({"ok": False, "error": "bad json"})
            continue

        cmd = req.get("cmd")
        try:
            if cmd == "ping":
                emit({"ok": True, "loaded": tts is not None})

            elif cmd == "load":
                if tts is None:
                    t0 = time.time()
                    with quiet():
                        from mlx_indextts.generate_v2 import IndexTTSv2
                        tts = IndexTTSv2(model_dir=model_dir)
                    emit({"ok": True, "loaded": True, "elapsed": round(time.time() - t0, 2)})
                else:
                    emit({"ok": True, "loaded": True, "elapsed": 0})

            elif cmd == "unload":
                tts = None
                import gc
                gc.collect()
                emit({"ok": True, "loaded": False})

            elif cmd == "generate":
                if tts is None:
                    emit({"ok": False, "error": "模型未加载"})
                    continue
                t0 = time.time()
                kwargs = {}
                if req.get("emotion"):
                    with quiet():
                        from mlx_indextts.generate_v2 import parse_emotion
                    kwargs["emotion"] = parse_emotion(req["emotion"])
                if req.get("emo_alpha") is not None:
                    kwargs["emo_alpha"] = float(req["emo_alpha"])
                if req.get("speed") is not None:
                    kwargs["speed"] = float(req["speed"])

                with quiet():
                    tts.generate(
                        text=req["text"],
                        reference_audio=req["ref_audio"],
                        output_path=req["output"],
                        **kwargs,
                    )
                emit({"ok": True, "elapsed": round(time.time() - t0, 2),
                      "output": req["output"]})
            else:
                emit({"ok": False, "error": f"未知命令 {cmd}"})

        except Exception as e:
            import traceback
            emit({"ok": False, "error": f"{type(e).__name__}: {e}",
                  "trace": traceback.format_exc()[-600:]})


if __name__ == "__main__":
    main()
