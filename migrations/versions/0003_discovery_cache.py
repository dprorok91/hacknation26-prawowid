"""Cache public news metadata and bound optional provider credits.

Data is metadata-only. No user text, API keys, or publisher article bodies are stored.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003_discovery_cache"
down_revision = "0002_delete_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "discovery_cache",
        sa.Column("cache_key", sa.String(64), primary_key=True),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("results_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_discovery_cache_expires_at", "discovery_cache", ["expires_at"])
    op.create_table(
        "provider_daily_usage",
        sa.Column("provider", sa.Text, primary_key=True),
        sa.Column("day_utc", sa.Date, primary_key=True),
        sa.Column("credits_used", sa.Integer, nullable=False, server_default="0"),
        sa.CheckConstraint("credits_used >= 0", name="provider_usage_nonnegative"),
    )


def downgrade() -> None:
    op.drop_table("provider_daily_usage")
    op.drop_index("ix_discovery_cache_expires_at", table_name="discovery_cache")
    op.drop_table("discovery_cache")
