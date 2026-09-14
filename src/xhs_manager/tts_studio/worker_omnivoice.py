"""OmniVoice 常驻 worker，隔离 PyTorch/transformers 依赖。"""

import contextlib
import json
import os
import sys
import time


def emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


@contextlib.contextmanager
def quiet():
    with contextlib.redirect_stdout(sys.stderr):
        yield


def main():
    model_dir = os.environ.get("OMNIVOICE_MODEL", "")
    device = os.environ.get("OMNIVOICE_DEVICE", "mps")
    dtype_name = os.environ.get("OMNIVOICE_DTYPE", "float16")
    model = None
    if not model_dir:
        emit({"ok": False, "error": "OMNIVOICE_MODEL 为空，请配置已下载的模型目录"})
        return
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            cmd = req.get("cmd")
            if cmd == "ping":
                emit({"ok": True, "loaded": model is not None})
            elif cmd == "load":
                if model is None:
                    import torch
                    from omnivoice import OmniVoice
                    dtype = getattr(torch, dtype_name)
                    t0 = time.time()
                    with quiet():
                        model = OmniVoice.from_pretrained(
                            model_dir, device_map=device, dtype=dtype,
                        )
                    emit({"ok": True, "loaded": True,
                          "elapsed": round(time.time() - t0, 2)})
                else:
                    emit({"ok": True, "loaded": True, "elapsed": 0})
            elif cmd == "unload":
                model = None
                import gc
                gc.collect()
                emit({"ok": True, "loaded": False})
            elif cmd == "generate":
                if model is None:
                    emit({"ok": False, "error": "模型未加载"})
                    continue
                import soundfile as sf
                t0 = time.time()
                kwargs = {k: req[k] for k in ("ref_audio", "ref_text", "instruct", "speed")
                          if req.get(k) not in (None, "")}
                with quiet():
                    audio = model.generate(text=req["text"], **kwargs)
                sf.write(req["output"], audio[0], 24000)
                emit({"ok": True, "elapsed": round(time.time() - t0, 2),
                      "output": req["output"]})
            else:
                emit({"ok": False, "error": f"未知命令 {cmd}"})
        except Exception as e:
            import traceback
            emit({"ok": False, "error": f"{type(e).__name__}: {e}",
                  "trace": traceback.format_exc()[-800:]})


if __name__ == "__main__":
    main()
