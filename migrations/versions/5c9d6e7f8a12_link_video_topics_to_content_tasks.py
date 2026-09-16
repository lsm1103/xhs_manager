"""把视频选题挂到内容任务上

两条内容线此前同库不同树：video_pipeline 不引用 content_tasks，
主系统也不认识 video。这里加上唯一缺的那条边。

列可空是刻意的：存量的 10 个视频选题没有对应任务，
硬造占位任务只会让数据更难解释——界面上如实标成「游离任务」。

Revision ID: 5c9d6e7f8a12
Revises: 4b8c5d6e7f01
"""

import sqlalchemy as sa
from alembic import op

revision = "5c9d6e7f8a12"
down_revision = "4b8c5d6e7f01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_topics") as batch:
        batch.add_column(sa.Column("task_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_video_topic_task", "content_tasks", ["task_id"], ["id"],
        )
    op.create_index("ix_video_topic_task", "video_topics", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_video_topic_task", table_name="video_topics")
    with op.batch_alter_table("video_topics") as batch:
        batch.drop_constraint("fk_video_topic_task", type_="foreignkey")
        batch.drop_column("task_id")
