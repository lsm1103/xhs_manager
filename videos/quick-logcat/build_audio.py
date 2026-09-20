"""生成逐句配音 + 拼接总音轨，并输出每句的起止时间（给字幕时间轴用）。"""
import asyncio, json, subprocess, sys
from pathlib import Path

import edge_tts

HERE = Path(__file__).parent
AUDIO = HERE / "audio"
FFMPEG = "/opt/homebrew/bin/ffmpeg"
FFPROBE = "/opt/homebrew/bin/ffprobe"


def duration(p: Path) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True)
    return float(r.stdout.strip())


async def main():
    cfg = json.loads((HERE / "script.json").read_text())
    AUDIO.mkdir(exist_ok=True)
    gap = cfg["gap"]

    timeline, t = [], 0.0
    parts = []
    for line in cfg["lines"]:
        mp3 = AUDIO / f"{line['id']}.mp3"
        await edge_tts.Communicate(line["text"], cfg["voice"], rate=cfg["rate"]).save(str(mp3))
        d = duration(mp3)
        timeline.append({"id": line["id"], "text": line["text"],
                         "start": round(t, 3), "end": round(t + d, 3), "dur": round(d, 3)})
        parts.append((mp3, d))
        t += d + gap

    # 用 adelay 把每句放到绝对位置，避免 concat 时 gap 精度漂移
    inputs, filters, labels = [], [], []
    for i, (mp3, _) in enumerate(parts):
        inputs += ["-i", str(mp3)]
        ms = int(timeline[i]["start"] * 1000)
        filters.append(f"[{i}:a]adelay={ms}|{ms}[a{i}]")
        labels.append(f"[a{i}]")
    total = round(t - gap + 1.2, 3)   # 末尾留 1.2s 收尾
    fc = ";".join(filters) + ";" + "".join(labels) + f"amix=inputs={len(parts)}:normalize=0,apad[out]"
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", *inputs,
                    "-filter_complex", fc, "-map", "[out]",
                    "-t", str(total), "-c:a", "aac", "-b:a", "192k",
                    str(AUDIO / "narration.m4a")], check=True)

    data = {"total": total, "lines": timeline}
    (HERE / "timeline.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
    # 合成页面直接读这个文件，字幕时间轴永远跟配音一致，不用手抄数字
    (HERE / "timeline.js").write_text(
        "window.TIMELINE = " + json.dumps(data, ensure_ascii=False) + ";\n")
    for x in timeline:
        print(f"{x['id']}  {x['start']:6.2f} → {x['end']:6.2f}  ({x['dur']:.2f}s)  {x['text'][:28]}")
    print(f"\n总时长 {total:.2f}s")


asyncio.run(main())
