"""视频 Pipeline 数据模型 — 与现有图文模型共存于同一数据库。"""

from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from xhs_manager.domain import new_id, utcnow
from xhs_manager.models import Base


# ── 每日流水线运行 ────────────────────────────────────────────────


class VideoPipelineRun(Base):
    __tablename__ = "video_pipeline_runs"
    __table_args__ = (UniqueConstraint("run_date", name="uq_video_pipeline_run_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    trigger_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="scheduled"
    )
    run_date: Mapped[datetime] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="collecting"
    )
    trend_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topic_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    video_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    config_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


# ── 热点信号 ──────────────────────────────────────────────────────


class VideoTrendSignal(Base):
    __tablename__ = "video_trend_signals"
    __table_args__ = (
        UniqueConstraint(
            "pipeline_run_id", "content_digest", name="uq_video_trend_digest"
        ),
        Index("ix_video_trend_platform", "pipeline_run_id", "platform"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pipeline_run_id: Mapped[str] = mapped_column(
        ForeignKey("video_pipeline_runs.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    heat_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    engagement: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    author: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    content_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


# ── 视频选题 ──────────────────────────────────────────────────────


class VideoTopic(Base):
    __tablename__ = "video_topics"
    __table_args__ = (Index("ix_video_topic_task", "task_id"),)
    __table_args__ = (
        Index("ix_video_topic_run_rank", "pipeline_run_id", "rank"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    pipeline_run_id: Mapped[str] = mapped_column(
        ForeignKey("video_pipeline_runs.id"), nullable=False
    )
    # 所属内容任务。可空是刻意的：视频线目前可以脱离任务独立跑，
    # 存量运行也没有对应任务——界面上如实标成「游离任务」，
    # 比硬造一个占位任务诚实。
    task_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("content_tasks.id"), nullable=True
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    angle: Mapped[str] = mapped_column(Text, nullable=False)
    why_now: Mapped[str] = mapped_column(Text, nullable=False)
    target_audience: Mapped[str] = mapped_column(Text, nullable=False)
    video_type: Mapped[str] = mapped_column(String(32), nullable=False)
    estimated_duration: Mapped[int] = mapped_column(Integer, nullable=False)
    scores: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    source_signal_ids: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


# ── 视频脚本 ──────────────────────────────────────────────────────


class VideoScript(Base):
    __tablename__ = "video_scripts"
    __table_args__ = (
        Index("ix_video_script_topic", "topic_id", "version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    topic_id: Mapped[str] = mapped_column(
        ForeignKey("video_topics.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    total_duration: Mapped[int] = mapped_column(Integer, nullable=False)
    scenes: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    # scenes 结构:
    # [{
    #   "scene_id": "s01",
    #   "order": 1,
    #   "duration": 5,
    #   "visual_desc": "...",
    #   "text_overlay": {"main": "...", "sub": "...", "animation": "typewriter"},
    #   "transition": "fade",
    #   "narration": "...",
    #   "material_hints": ["search:keyword1", "gen:prompt"]
    # }]
    bgm_style: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    platform_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    # platform_metadata 结构:
    # {
    #   "xiaohongshu": {"title": "...", "desc": "...", "tags": [...]},
    #   "douyin": {"title": "...", "desc": "...", "tags": [...]},
    #   "bilibili": {"title": "...", "desc": "...", "tags": [...]},
    #   "twitter": {"text": "...", "hashtags": [...]}
    # }
    generation_model: Mapped[str] = mapped_column(String(80), nullable=False)
    generation_prompt_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


# ── 视频素材 ──────────────────────────────────────────────────────


class VideoMaterial(Base):
    __tablename__ = "video_materials"
    __table_args__ = (
        Index("ix_video_material_script_scene", "script_id", "scene_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    script_id: Mapped[str] = mapped_column(
        ForeignKey("video_scripts.id"), nullable=False
    )
    scene_id: Mapped[str] = mapped_column(String(32), nullable=False)
    material_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_tool: Mapped[str] = mapped_column(String(32), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    local_path: Mapped[str] = mapped_column(Text, nullable=False)
    license_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unknown"
    )
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    width: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    height: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extra_meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSON, nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


# ── HTML 组合 ─────────────────────────────────────────────────────


class VideoComposition(Base):
    __tablename__ = "video_compositions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    script_id: Mapped[str] = mapped_column(
        ForeignKey("video_scripts.id"), nullable=False
    )
    composition_dir: Mapped[str] = mapped_column(Text, nullable=False)
    html_path: Mapped[str] = mapped_column(Text, nullable=False)
    total_duration: Mapped[float] = mapped_column(Float, nullable=False)
    resolution: Mapped[str] = mapped_column(
        String(16), nullable=False, default="1080x1920"
    )
    transition_effects: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    has_narration: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    has_bgm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    template_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="draft"
    )
    validation_errors: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


# ── 渲染任务 ──────────────────────────────────────────────────────


class VideoRender(Base):
    __tablename__ = "video_renders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    composition_id: Mapped[str] = mapped_column(
        ForeignKey("video_compositions.id"), nullable=False
    )
    output_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cover_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    covers: Mapped[dict[str, str]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    format: Mapped[str] = mapped_column(
        String(16), nullable=False, default="mp4"
    )
    codec: Mapped[str] = mapped_column(String(16), nullable=False, default="h264")
    fps: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    file_size: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    render_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


# ── 发布记录 ──────────────────────────────────────────────────────


class VideoPublication(Base):
    __tablename__ = "video_publications"
    __table_args__ = (
        UniqueConstraint(
            "render_id", "platform", name="uq_video_publication_render_platform"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    render_id: Mapped[str] = mapped_column(
        ForeignKey("video_renders.id"), nullable=False
    )
    topic_id: Mapped[str] = mapped_column(
        ForeignKey("video_topics.id"), nullable=False
    )
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    cover_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    publish_method: Mapped[str] = mapped_column(String(32), nullable=False)
    external_id: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    external_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending"
    )
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    published_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
