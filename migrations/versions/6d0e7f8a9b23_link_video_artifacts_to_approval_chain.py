"""把视频产物挂到审批链上

P2 时只加了 video_topics.task_id，另外三个外键刻意没加——当时还没有写入方，
加了就是三个没人写的死列。P4 让视频走审批和排期，它们才有了真正的写入方：

  video_topics.topic_proposal_id      选题提案（可审批、带版本）
  video_scripts.content_version_id    内容版本（scenes → slide_scripts）
  video_publications.publication_plan_id  发布计划（排期 + 幂等键）

仍然全部可空：视频线可以脱离审批链独立跑（cli run 那条路），
只有走 produce 的才会一路挂上去。

Revision ID: 6d0e7f8a9b23
Revises: 5c9d6e7f8a12
"""

import sqlalchemy as sa
from alembic import op

revision = "6d0e7f8a9b23"
down_revision = "5c9d6e7f8a12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_topics") as batch:
        batch.add_column(sa.Column("topic_proposal_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_video_topic_proposal", "topic_proposals", ["topic_proposal_id"], ["id"],
        )

    with op.batch_alter_table("video_scripts") as batch:
        batch.add_column(sa.Column("content_version_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_video_script_content_version", "content_versions",
            ["content_version_id"], ["id"],
        )

    with op.batch_alter_table("video_publications") as batch:
        batch.add_column(sa.Column("publication_plan_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_video_publication_plan", "publication_plans",
            ["publication_plan_id"], ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("video_publications") as batch:
        batch.drop_constraint("fk_video_publication_plan", type_="foreignkey")
        batch.drop_column("publication_plan_id")

    with op.batch_alter_table("video_scripts") as batch:
        batch.drop_constraint("fk_video_script_content_version", type_="foreignkey")
        batch.drop_column("content_version_id")

    with op.batch_alter_table("video_topics") as batch:
        batch.drop_constraint("fk_video_topic_proposal", type_="foreignkey")
        batch.drop_column("topic_proposal_id")
