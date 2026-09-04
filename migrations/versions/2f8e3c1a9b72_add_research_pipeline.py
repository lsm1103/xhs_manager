"""add research pipeline

Revision ID: 2f8e3c1a9b72
Revises: 8789cac1b5da
Create Date: 2026-07-27 00:00:00.000000
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "2f8e3c1a9b72"
down_revision: Union[str, None] = "8789cac1b5da"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "research_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=36), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_scopes", sa.JSON(), nullable=False),
        sa.Column("seed_queries", sa.JSON(), nullable=False),
        sa.Column("max_items_per_source", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_summary", sa.JSON(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["account_strategy_versions.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "research_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("research_run_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("raw_result_count", sa.Integer(), nullable=False),
        sa.Column("valid_result_count", sa.Integer(), nullable=False),
        sa.Column("rate_limit", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["research_run_id"], ["research_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_research_source_run",
        "research_sources",
        ["research_run_id", "source_type"],
    )
    op.create_table(
        "research_signals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("research_run_id", sa.String(length=36), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("author_or_org", sa.String(length=240), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("content_kind", sa.String(length=32), nullable=False),
        sa.Column("freshness", sa.String(length=32), nullable=False),
        sa.Column("credibility", sa.String(length=16), nullable=False),
        sa.Column("audience_relevance", sa.Float(), nullable=False),
        sa.Column("copyright_risk", sa.String(length=16), nullable=False),
        sa.Column("verification_state", sa.String(length=40), nullable=False),
        sa.Column("content_digest", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["research_run_id"], ["research_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "research_run_id", "canonical_url", name="uq_research_signal_canonical"
        ),
        sa.UniqueConstraint("research_run_id", "content_digest", name="uq_research_signal_digest"),
    )
    op.create_index(
        "ix_research_signal_run_verification",
        "research_signals",
        ["research_run_id", "verification_state"],
    )
    op.create_table(
        "research_signal_sources",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("research_signal_id", sa.String(length=36), nullable=False),
        sa.Column("research_source_id", sa.String(length=36), nullable=False),
        sa.Column("original_source_url", sa.Text(), nullable=False),
        sa.Column("original_pointer", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["research_signal_id"], ["research_signals.id"]),
        sa.ForeignKeyConstraint(["research_source_id"], ["research_sources.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "research_signal_id", "research_source_id", name="uq_signal_source_link"
        ),
    )
    op.create_table(
        "evidence_packages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("research_run_id", sa.String(length=36), nullable=False),
        sa.Column("supported_question", sa.Text(), nullable=False),
        sa.Column("supported_claim", sa.Text(), nullable=True),
        sa.Column("key_evidence", sa.JSON(), nullable=False),
        sa.Column("counterexamples_and_limits", sa.JSON(), nullable=False),
        sa.Column("source_independence", sa.JSON(), nullable=False),
        sa.Column("conclusion_strength", sa.String(length=32), nullable=False),
        sa.Column("unresolved_conflicts", sa.JSON(), nullable=False),
        sa.Column("gaps", sa.JSON(), nullable=False),
        sa.Column("applicable_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applicable_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("publishable", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["research_run_id"], ["research_runs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("evidence_packages")
    op.drop_table("research_signal_sources")
    op.drop_index("ix_research_signal_run_verification", table_name="research_signals")
    op.drop_table("research_signals")
    op.drop_index("ix_research_source_run", table_name="research_sources")
    op.drop_table("research_sources")
    op.drop_table("research_runs")
