"""Explicit host-level approval for public-sector text published on www.gov.pl.

This is a project policy decision, not a claim that every attachment or
third-party work on the host is free of restrictions. Existing editorial
opt-outs, SafeWeb robots checks, and manual source-policy overrides still apply.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from prawdowid.infrastructure.db_models import sources

GOVPL_HOST = "www.gov.pl"
GOVPL_POLICY_URL = "https://www.gov.pl/web/gov/wykorzystaj-informacje-sektora-publicznego2"
GOVPL_LICENSE_LABEL = "gov.pl public-sector information (2021 reuse act; document exceptions)"
_LEGACY_METADATA_LABEL = "Gov.pl copyright and reuse notice; first-page headline metadata"
_LEGACY_METADATA_URL = "https://www.gov.pl/web/gov/prawa-autorskie"


def govpl_approval_statement(*, approved_at: datetime | None = None):
    """Approve only the exact www.gov.pl host, not arbitrary subdomains.

    Preserve manually approved and disabled sources. Migrate only a fresh
    metadata-only registration or the exact previous gov.pl seed metadata
    tuple; this tuple was historically inserted as a disabled placeholder.
    """
    now = approved_at or datetime.now(UTC)
    approval = {
        "source_type": "primary",
        "policy_url": GOVPL_POLICY_URL,
        "license_label": GOVPL_LICENSE_LABEL,
        "enabled": True,
        "approved_at": now,
    }
    return (
        insert(sources)
        .values(domain=GOVPL_HOST, display_name="Portal Gov.pl", **approval)
        .on_conflict_do_update(
            index_elements=[sources.c.domain],
            set_={**approval, "display_name": "Portal Gov.pl"},
            where=sa.and_(
                sources.c.approved_at.is_(None),
                sources.c.enabled.is_(False),
                sa.or_(
                    sa.and_(
                        sources.c.license_label.is_(None),
                        sources.c.policy_url.is_(None),
                    ),
                    sa.and_(
                        sources.c.license_label == _LEGACY_METADATA_LABEL,
                        sources.c.policy_url == _LEGACY_METADATA_URL,
                    ),
                ),
            ),
        )
    )


async def approve_govpl(session: AsyncSession) -> None:
    """Idempotently grant host approval after registering the seed metadata."""
    await session.execute(govpl_approval_statement())
