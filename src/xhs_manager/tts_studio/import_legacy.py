"""把老的 data/tts_studio.db 里的配音历史搬进主库。

合并之前 TTS Studio 用独立 SQLite。这个命令把那边的记录按 id 搬过来，
可以重复跑：已经存在的 id 直接跳过。

    python -m xhs_manager.tts_studio.import_legacy [老库路径]

不删老库——搬完自己确认没问题再删。
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from xhs_manager.db import create_db_engine, create_session_factory
from xhs_manager.models import TtsGeneration

DEFAULT_LEGACY = "data/tts_studio.db"

# 老表和新表同名的列。老库多出来的、新库没有的一律不要。
COLUMNS = [
    "id", "model_id", "text", "voice", "ref_audio", "params", "audio_path",
    "duration", "elapsed", "rtf", "sample_rate", "file_size", "waveform",
    "status", "error", "created_at",
]


def _parse_dt(raw) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    try:
        return datetime.fromisoformat(str(raw))
    except ValueError:
        return None


def import_legacy(legacy_path: str = DEFAULT_LEGACY) -> dict[str, int]:
    src = Path(legacy_path)
    if not src.is_file():
        return {"found": 0, "imported": 0, "skipped": 0}

    conn = sqlite3.connect(src)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            f"select {', '.join(COLUMNS)} from generations"
        ).fetchall()
    except sqlite3.OperationalError:
        return {"found": 0, "imported": 0, "skipped": 0}
    finally:
        conn.close()

    from xhs_manager.config import get_settings

    factory = create_session_factory(create_db_engine(get_settings().database_url))
    imported = skipped = 0
    with factory() as session:
        for row in rows:
            if session.get(TtsGeneration, row["id"]) is not None:
                skipped += 1
                continue
            data = dict(row)
            # params 在老库里是 JSON 列，sqlite3 取出来是字符串
            params = data.get("params")
            if isinstance(params, str):
                import json
                try:
                    params = json.loads(params)
                except ValueError:
                    params = {}
            data["params"] = params or {}
            data["created_at"] = _parse_dt(data.get("created_at"))
            session.add(TtsGeneration(**data))
            imported += 1
        session.commit()
    return {"found": len(rows), "imported": imported, "skipped": skipped}


def main() -> None:
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LEGACY
    out = import_legacy(path)
    print(f"老库 {path}：{out['found']} 条，"
          f"搬入 {out['imported']} 条，已存在跳过 {out['skipped']} 条")


if __name__ == "__main__":
    main()
