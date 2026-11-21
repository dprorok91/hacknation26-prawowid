"""Read-only check: how many *citable* documents are actually ready, not just URLs.

Usage: uv run --locked python scripts/check_corpus_ready.py
Never inserts, approves, or fetches sources. Exit 0 only when the competition
corpus thresholds are satisfied; 1 for insufficient coverage; 2 for DB failure.
"""

import asyncio
import json

import sqlalchemy as sa
from sqlalchemy.exc import SQLAlchemyError

from prawdowid.config import get_settings
from prawdowid.infrastructure.db import create_database

# Require an unexpired, publisher-approved document with current-version passages.
# A discovery metadata record alone must not count toward the evidence-ready corpus.
COUNTS = sa.text(
    """
    WITH usable AS (
        SELECT d.id, s.domain, s.source_type
        FROM documents AS d
        JOIN sources AS s ON s.id = d.source_id
        JOIN document_versions AS v
          ON v.document_id = d.id AND v.content_version = d.content_version
        WHERE s.enabled IS TRUE
          AND s.approved_at IS NOT NULL
          AND s.license_label <> ''
          AND d.text_license = s.license_label
          AND v.text_license = s.license_label
          AND d.retrieval_status IN ('ready', 'text_only')
          AND v.retrieval_status IN ('ready', 'text_only')
          AND d.expires_at > now()
          AND v.expires_at > now()
          AND EXISTS (
              SELECT 1 FROM passages AS p
              WHERE p.document_id = d.id AND p.content_version = d.content_version
          )
    )
    SELECT count(*) AS documents,
           count(DISTINCT domain) AS domains,
           count(DISTINCT source_type) AS source_types
    FROM usable
    """
)


async def counts():
    db = create_database(get_settings().database_url)
    try:
        async with db.sessions() as session:
            result = (await session.execute(COUNTS)).one()
            return dict(zip(("documents", "domains", "source_types"), map(int, result)))
    finally:
        await db.dispose()


def verdict(row):
    return {
        "documents": {"actual": row["documents"], "required": 100},
        "domains": {"actual": row["domains"], "required": 10},
        "source_types": {"actual": row["source_types"], "required": 2},
    }


def main():
    try:
        row = asyncio.run(counts())
    except (SQLAlchemyError, OSError, ValueError, RuntimeError):
        # Do not expose DSN, SQL parameters, driver exceptions, or credentials.
        print(json.dumps({"status": "BLOCKED", "reason": "database_unavailable"}))
        return 2
    gates = verdict(row)
    passed = all(item["actual"] >= item["required"] for item in gates.values())
    print(json.dumps({"status": "PASS" if passed else "PARTIAL", "corpus": gates}, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
