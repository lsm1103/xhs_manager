"""video_topics.previous_status：记住人工标记前的状态

Revision ID: 8f2a0b1c3d45
Revises: 7e1f8a9b0c34

控制台可以把选题标记为「放弃」「已过期」。撤销标记时要还原成
标记之前的状态，所以得有地方存它。
"""

from alembic import op
import sqlalchemy as sa

revision = "8f2a0b1c3d45"
down_revision = "7e1f8a9b0c34"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("video_topics", sa.Column("previous_status", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("video_topics", "previous_status")
