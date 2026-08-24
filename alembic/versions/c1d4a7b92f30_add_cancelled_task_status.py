"""add cancelled task status

Revision ID: c1d4a7b92f30
Revises: ea37ad306457
Create Date: 2026-08-24 12:05:00.000000

Adds 'CANCELLED' to the taskstatus Postgres enum (see db/models.py's
TaskStatus and cancellation.py). SQLModel stores the enum by NAME, not
value, which is why this adds 'CANCELLED' rather than 'cancelled' -- check
`SELECT unnest(enum_range(NULL::taskstatus))` against an existing DB and
you'll see PENDING/RUNNING/... in caps.

ALTER TYPE ... ADD VALUE cannot run inside a transaction block on
PostgreSQL < 12 and still can't be followed by a use of the new value in
the same transaction, so it runs in an autocommit block. There is no
downgrade: Postgres has no ALTER TYPE ... DROP VALUE, and rebuilding the
enum would mean rewriting every column that uses it -- documented rather
than faked with a no-op that silently leaves the value in place.
"""
from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision = 'c1d4a7b92f30'
down_revision = 'ea37ad306457'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'CANCELLED'")


def downgrade() -> None:
    # Intentionally not implemented -- see module docstring.
    pass
