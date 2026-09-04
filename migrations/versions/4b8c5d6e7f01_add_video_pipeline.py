"""add video pipeline tables

Revision ID: 4b8c5d6e7f01
Revises: 3a7d2b4e5f90
Create Date: 2026-09-03

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "4b8c5d6e7f01"
down_revision: Union[str, None] = "3a7d2b4e5f90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── video_pipeline_runs ──
    op.create_table(
        "video_pipeline_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("trigger_type", sa.String(32), nullable=False, server_default="scheduled"),
        sa.Column("run_date", sa.Date, nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="collecting"),
        sa.Column("trend_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("topic_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("video_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("published_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("config_snapshot", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_date", name="uq_video_pipeline_run_date"),
    )

    # ── video_trend_signals ──
    op.create_table(
        "video_trend_signals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(36), sa.ForeignKey("video_pipeline_runs.id"), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("heat_score", sa.Float, nullable=True),
        sa.Column("engagement", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("author", sa.String(240), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_digest", sa.String(128), nullable=False),
        sa.Column("tags", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("pipeline_run_id", "content_digest", name="uq_video_trend_digest"),
    )
    op.create_index("ix_video_trend_platform", "video_trend_signals", ["pipeline_run_id", "platform"])

    # ── video_topics ──
    op.create_table(
        "video_topics",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("pipeline_run_id", sa.String(36), sa.ForeignKey("video_pipeline_runs.id"), nullable=False),
        sa.Column("rank", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("angle", sa.Text, nullable=False),
        sa.Column("why_now", sa.Text, nullable=False),
        sa.Column("target_audience", sa.Text, nullable=False),
        sa.Column("video_type", sa.String(32), nullable=False),
        sa.Column("estimated_duration", sa.Integer, nullable=False),
        sa.Column("scores", sa.JSON, nullable=False),
        sa.Column("total_score", sa.Float, nullable=False),
        sa.Column("source_signal_ids", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_video_topic_run_rank", "video_topics", ["pipeline_run_id", "rank"])

    # ── video_scripts ──
    op.create_table(
        "video_scripts",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("topic_id", sa.String(36), sa.ForeignKey("video_topics.id"), nullable=False),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("total_duration", sa.Integer, nullable=False),
        sa.Column("scenes", sa.JSON, nullable=False),
        sa.Column("bgm_style", sa.String(120), nullable=True),
        sa.Column("platform_metadata", sa.JSON, nullable=False),
        sa.Column("generation_model", sa.String(80), nullable=False),
        sa.Column("generation_prompt_hash", sa.String(128), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_video_script_topic", "video_scripts", ["topic_id", "version"])

    # ── video_materials ──
    op.create_table(
        "video_materials",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("script_id", sa.String(36), sa.ForeignKey("video_scripts.id"), nullable=False),
        sa.Column("scene_id", sa.String(32), nullable=False),
        sa.Column("material_type", sa.String(32), nullable=False),
        sa.Column("source_tool", sa.String(32), nullable=False),
        sa.Column("source_url", sa.Text, nullable=True),
        sa.Column("local_path", sa.Text, nullable=False),
        sa.Column("license_type", sa.String(32), nullable=False, server_default="unknown"),
        sa.Column("duration", sa.Float, nullable=True),
        sa.Column("width", sa.Integer, nullable=True),
        sa.Column("height", sa.Integer, nullable=True),
        sa.Column("file_size", sa.Integer, nullable=True),
        sa.Column("selected", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("metadata", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_video_material_script_scene", "video_materials", ["script_id", "scene_id"])

    # ── video_compositions ──
    op.create_table(
        "video_compositions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("script_id", sa.String(36), sa.ForeignKey("video_scripts.id"), nullable=False),
        sa.Column("composition_dir", sa.Text, nullable=False),
        sa.Column("html_path", sa.Text, nullable=False),
        sa.Column("total_duration", sa.Float, nullable=False),
        sa.Column("resolution", sa.String(16), nullable=False, server_default="1080x1920"),
        sa.Column("transition_effects", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("has_narration", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("has_bgm", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("template_id", sa.String(64), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("validation_errors", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── video_renders ──
    op.create_table(
        "video_renders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("composition_id", sa.String(36), sa.ForeignKey("video_compositions.id"), nullable=False),
        sa.Column("output_path", sa.Text, nullable=True),
        sa.Column("cover_path", sa.Text, nullable=True),
        sa.Column("covers", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("format", sa.String(16), nullable=False, server_default="mp4"),
        sa.Column("codec", sa.String(16), nullable=False, server_default="h264"),
        sa.Column("fps", sa.Integer, nullable=False, server_default="30"),
        sa.Column("file_size", sa.Integer, nullable=True),
        sa.Column("duration", sa.Float, nullable=True),
        sa.Column("render_time", sa.Float, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # ── video_publications ──
    op.create_table(
        "video_publications",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("render_id", sa.String(36), sa.ForeignKey("video_renders.id"), nullable=False),
        sa.Column("topic_id", sa.String(36), sa.ForeignKey("video_topics.id"), nullable=False),
        sa.Column("platform", sa.String(32), nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("tags", sa.JSON, nullable=False, server_default="[]"),
        sa.Column("cover_path", sa.Text, nullable=True),
        sa.Column("publish_method", sa.String(32), nullable=False),
        sa.Column("external_id", sa.String(240), nullable=True),
        sa.Column("external_url", sa.Text, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("error_detail", sa.Text, nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("render_id", "platform", name="uq_video_publication_render_platform"),
    )


def downgrade() -> None:
    op.drop_table("video_publications")
    op.drop_table("video_renders")
    op.drop_table("video_compositions")
    op.drop_table("video_materials")
    op.drop_table("video_scripts")
    op.drop_table("video_topics")
    op.drop_table("video_trend_signals")
    op.drop_table("video_pipeline_runs")
