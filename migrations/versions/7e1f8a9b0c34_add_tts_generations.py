"""tts_generations：配音历史并入主库

Revision ID: 7e1f8a9b0c34
Revises: 6d0e7f8a9b23

原来 TTS Studio 用独立的 data/tts_studio.db。合并成一个系统之后，
配音历史和任务、成片在同一个库里，控制台一个连接就能查。

老库里的记录不由这条迁移搬运——迁移去读另一个 sqlite 文件太脆。
搬运走 `python -m xhs_manager.tts_studio.import_legacy`，它可以重复跑。
"""

from alembic import op
import sqlalchemy as sa

revision = "7e1f8a9b0c34"
down_revision = "6d0e7f8a9b23"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "tts_generations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("model_id", sa.String(40), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("voice", sa.String(80), nullable=True),
        sa.Column("ref_audio", sa.Text(), nullable=True),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("audio_path", sa.Text(), nullable=False),
        sa.Column("duration", sa.Float(), nullable=True),
        sa.Column("elapsed", sa.Float(), nullable=True),
        sa.Column("rtf", sa.Float(), nullable=True),
        sa.Column("sample_rate", sa.Integer(), nullable=True),
        sa.Column("file_size", sa.Integer(), nullable=True),
        sa.Column("waveform", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="ok"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_tts_generations_created", "tts_generations", ["created_at"])
    op.create_index("ix_tts_generations_model", "tts_generations", ["model_id"])


def downgrade() -> None:
    op.drop_index("ix_tts_generations_model", table_name="tts_generations")
    op.drop_index("ix_tts_generations_created", table_name="tts_generations")
    op.drop_table("tts_generations")
