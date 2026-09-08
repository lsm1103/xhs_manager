"""TTS Studio 数据存储。

用独立 SQLite 文件，不和视频流水线的库混在一起 ——
这是个调试/对比工具，数据生命周期与业务无关。
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import JSON, Float, Integer, String, Text, DateTime, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from xhs_manager.domain import new_id, utcnow


class Base(DeclarativeBase):
    pass


class Generation(Base):
    """一次生成记录。"""

    __tablename__ = "generations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    model_id: Mapped[str] = mapped_column(String(40), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    voice: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)      # 预置音色
    ref_audio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)        # 参考音色路径
    params: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    audio_path: Mapped[str] = mapped_column(Text, nullable=False)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    elapsed: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    rtf: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sample_rate: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # 波形峰值（下采样后的 JSON 数组），前端直接画，避免重复解码音频
    waveform: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ok")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "model_id": self.model_id,
            "text": self.text,
            "voice": self.voice,
            "ref_audio": Path(self.ref_audio).name if self.ref_audio else None,
            "params": self.params or {},
            "audio_url": f"/audio/{Path(self.audio_path).name}",
            "duration": self.duration,
            "elapsed": self.elapsed,
            "rtf": self.rtf,
            "sample_rate": self.sample_rate,
            "file_size": self.file_size,
            "waveform": json.loads(self.waveform) if self.waveform else [],
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


_engine = None
_Session = None


def init_db(db_path: str):
    global _engine, _Session
    p = Path(db_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(f"sqlite:///{p}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(_engine)
    _Session = sessionmaker(bind=_engine, expire_on_commit=False)
    return _Session


def get_session():
    if _Session is None:
        raise RuntimeError("数据库未初始化")
    return _Session()
