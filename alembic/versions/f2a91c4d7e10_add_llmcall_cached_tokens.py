"""add llmcall.cached_tokens

Cost is now derived from tokens at read time (see agentsys/pricing.py), which
needs to know how much of the prompt was served from the provider's cache and
therefore billed at half rate. Without it, derivation overstates spend on
exactly the long-prompt agent_step calls that cache best -- measured ~3% high
across this project's own history.

NULLABLE WITH NO BACKFILL, ON PURPOSE. NULL means "not recorded", which is
the honest state for every row written before this column existed; a default
of 0 would assert that nothing was cached, which is false and would make old
rows read as more expensive than they actually were. pricing.py flags a
NULL-carrying total as estimated instead of guessing.

Revision ID: f2a91c4d7e10
Revises: 81b311712d89
"""

from alembic import op
import sqlalchemy as sa

revision = "f2a91c4d7e10"
down_revision = "81b311712d89"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llmcall", sa.Column("cached_tokens", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("llmcall", "cached_tokens")
