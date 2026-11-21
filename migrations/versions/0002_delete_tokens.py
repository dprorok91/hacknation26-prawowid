"""Require a separate capability for deleting analyses."""

import sqlalchemy as sa
from alembic import op

revision = "0002_delete_tokens"
down_revision = "0001_initial"


def upgrade():
    op.add_column("analyses", sa.Column("delete_token_hash", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("analyses", "delete_token_hash")
