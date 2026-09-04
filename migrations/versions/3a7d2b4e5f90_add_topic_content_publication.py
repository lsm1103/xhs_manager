"""add topic, content, and publication workflow models

Revision ID: 3a7d2b4e5f90
Revises: 2f8e3c1a9b72
Create Date: 2026-07-30 00:00:00.000000
"""

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "3a7d2b4e5f90"
down_revision: Union[str, None] = "2f8e3c1a9b72"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "topic_proposals",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("user_problem", sa.Text(), nullable=False),
        sa.Column("working_title", sa.Text(), nullable=False),
        sa.Column("core_claim", sa.Text(), nullable=False),
        sa.Column("why_now", sa.Text(), nullable=False),
        sa.Column("content_pillar", sa.String(length=64), nullable=False),
        sa.Column("primary_goal", sa.String(length=64), nullable=False),
        sa.Column("evidence_package_ids", sa.JSON(), nullable=False),
        sa.Column("counterpoints", sa.JSON(), nullable=False),
        sa.Column("experiment_hypothesis", sa.Text(), nullable=False),
        sa.Column("primary_metric", sa.String(length=80), nullable=False),
        sa.Column("recommended_format", sa.String(length=32), nullable=False),
        sa.Column("scores", sa.JSON(), nullable=False),
        sa.Column("total_score", sa.Integer(), nullable=False),
        sa.Column("score_explanation", sa.Text(), nullable=False),
        sa.Column("risks", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["content_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topic_proposal_task_version", "topic_proposals", ["task_id", "version"])
    op.create_table(
        "content_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("topic_version_id", sa.String(length=36), nullable=False),
        sa.Column("strategy_version_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("title_candidates", sa.JSON(), nullable=False),
        sa.Column("selected_title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("interaction_question", sa.Text(), nullable=False),
        sa.Column("topics", sa.JSON(), nullable=False),
        sa.Column("cover_script", sa.JSON(), nullable=False),
        sa.Column("slide_scripts", sa.JSON(), nullable=False),
        sa.Column("asset_paths", sa.JSON(), nullable=False),
        sa.Column("source_notes", sa.JSON(), nullable=False),
        sa.Column("risk_notes", sa.JSON(), nullable=False),
        sa.Column("generation_manifest", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["strategy_version_id"], ["account_strategy_versions.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["content_tasks.id"]),
        sa.ForeignKeyConstraint(["topic_version_id"], ["topic_proposals.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_content_version_task_version", "content_versions", ["task_id", "version"])
    op.create_table(
        "publication_plans",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("content_version_id", sa.String(length=36), nullable=False),
        sa.Column("approval_id", sa.String(length=36), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("allowed_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("allowed_until", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=240), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"]),
        sa.ForeignKeyConstraint(["approval_id"], ["approval_requests.id"]),
        sa.ForeignKeyConstraint(["content_version_id"], ["content_versions.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["content_tasks.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_publication_plan_idempotency"),
    )


def downgrade() -> None:
    op.drop_table("publication_plans")
    op.drop_index("ix_content_version_task_version", table_name="content_versions")
    op.drop_table("content_versions")
    op.drop_index("ix_topic_proposal_task_version", table_name="topic_proposals")
    op.drop_table("topic_proposals")
