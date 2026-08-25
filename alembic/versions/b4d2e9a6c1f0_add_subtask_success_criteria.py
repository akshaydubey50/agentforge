"""add subtask success criteria

Phase 4. Stores one compact, optional postcondition on the existing Subtask
row so verification can survive retries and resumes without adding Plan or
PlanStep tables.
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "b4d2e9a6c1f0"
down_revision = "a7c3f81e5d24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("subtask", sa.Column("success_criteria", sqlmodel.sql.sqltypes.AutoString(), nullable=True))


def downgrade() -> None:
    op.drop_column("subtask", "success_criteria")
