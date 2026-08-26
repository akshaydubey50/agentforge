"""add toolcall execution ledger (effect_key, nullable success)

Phase 3. Turns ToolCall from a post-hoc record into a durable execution
ledger -- see agentsys/execution.py and docs/PHASE3_EXECUTION_NOTE.md.

Two changes, both minimal by design; no new table.

effect_key: nullable, indexed, NOT unique. It is the stable identity of one
logical effect (task + tool + model-controlled args + acting user). Nullable
because reads never get one -- they are deliberately not deduplicated, since
re-reading is legitimate and sometimes required. Not unique because several
attempt rows can legitimately share a key, and counting them is how the
ledger stays useful.

success: bool NOT NULL -> bool NULL. NULL is a new, third state and it is the
whole point of the phase: a row is now written BEFORE the tool runs, so a
worker killed mid-call leaves a row saying "this was attempted and the
outcome is unknown". Existing rows are untouched and keep their true/false --
every one of them was written after its call returned, so none of them is
ambiguous, and there is nothing to backfill.

The downgrade sets any ambiguous row to false before restoring NOT NULL.
That is a lossy but honest choice, and it is stated rather than hidden: going
back to a two-valued column means "unknown" has to become something, and
recording an unknown outcome as a failure is the safe direction -- it will
cause a retry to be considered rather than a real effect to be forgotten.
"""

from alembic import op
import sqlalchemy as sa
import sqlmodel

revision = "a7c3f81e5d24"
down_revision = "f2a91c4d7e10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("toolcall", sa.Column("effect_key", sqlmodel.sql.sqltypes.AutoString(), nullable=True))
    op.create_index(op.f("ix_toolcall_effect_key"), "toolcall", ["effect_key"], unique=False)
    op.alter_column("toolcall", "success", existing_type=sa.BOOLEAN(), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE toolcall SET success = false WHERE success IS NULL")
    op.alter_column("toolcall", "success", existing_type=sa.BOOLEAN(), nullable=False)
    op.drop_index(op.f("ix_toolcall_effect_key"), table_name="toolcall")
    op.drop_column("toolcall", "effect_key")
