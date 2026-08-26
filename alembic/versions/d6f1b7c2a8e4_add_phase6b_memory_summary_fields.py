"""add phase 6b memory and rolling summary fields

Revision ID: d6f1b7c2a8e4
Revises: b4d2e9a6c1f0
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "d6f1b7c2a8e4"
down_revision = "b4d2e9a6c1f0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("task", sa.Column("rolling_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("task", sa.Column("rolling_summary_version", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("task", sa.Column("rolling_summary_updated_at", sa.DateTime(), nullable=True))
    op.add_column("task", sa.Column("rolling_summary_until", sa.DateTime(), nullable=True))
    op.add_column("memoryentry", sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()))
    op.add_column("memoryentry", sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default="{}"))
    op.alter_column("task", "rolling_summary_version", server_default=None)
    op.alter_column("memoryentry", "updated_at", server_default=None)
    op.alter_column("memoryentry", "meta", server_default=None)


def downgrade() -> None:
    op.drop_column("memoryentry", "meta")
    op.drop_column("memoryentry", "updated_at")
    op.drop_column("task", "rolling_summary_until")
    op.drop_column("task", "rolling_summary_updated_at")
    op.drop_column("task", "rolling_summary_version")
    op.drop_column("task", "rolling_summary")
