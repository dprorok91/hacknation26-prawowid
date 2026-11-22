"""Persist exact evidence links and enforce current publication permissions."""

import re
from urllib.parse import urlsplit
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from prawdowid.infrastructure.db_models import (
    claims,
    document_versions,
    documents,
    evidence,
    narrative_frames,
    passages,
    sources,
)

# Live evidence has a source-text identifier, NOT a PostgreSQL passage UUID.
# The vetted SafeWeb/LLM evidence is retained in analyses.response_json,
# but must not be inserted into evidence.passage_id (UUID foreign key).
_LIVE_ID = re.compile(r"live:[0-9a-f]{16}:[0-9]+:[0-9]+\Z")
_LIVE_HOST = "zdmk.krakow.pl"


def _is_live_reference(ref: dict) -> bool:
    passage_id = ref.get("passage_id")
    return isinstance(passage_id, str) and passage_id.startswith("live:")


def _valid_live_reference(ref: dict) -> bool:
    url = urlsplit(ref.get("url", ""))
    quote = ref.get("exact_quote")
    return (
        bool(_LIVE_ID.fullmatch(ref["passage_id"]))
        and url.scheme == "https"
        and url.hostname == _LIVE_HOST
        and isinstance(quote, str)
        and bool(quote.strip())
        and ref.get("relation") in ("supports", "refutes", "context")
    )


async def evidence_rows(session: AsyncSession, report: dict) -> dict | None:
    references = report.get("sources", [])
    for ref in references:
        if _is_live_reference(ref) and not _valid_live_reference(ref):
            return None
    ids = {ref["passage_id"] for ref in references if not _is_live_reference(ref)}
    # Fail closed on non-live, non-UUID passage identifiers rather than crash
    # SQLAlchemy's PostgreSQL UUID binder during job completion.
    try:
        if any(str(UUID(passage_id)) != passage_id for passage_id in ids):
            return None
    except (ValueError, AttributeError, TypeError):
        return None
    if not ids:
        return {}
    statement = (
        sa.select(passages.c.id, passages.c.text, passages.c.document_id)
        .join(documents, passages.c.document_id == documents.c.id)
        .join(sources, documents.c.source_id == sources.c.id)
        .join(
            document_versions,
            sa.and_(
                document_versions.c.document_id == passages.c.document_id,
                document_versions.c.content_version == passages.c.content_version,
            ),
        )
        .where(
            passages.c.id.in_(ids),
            sources.c.enabled.is_(True),
            sources.c.approved_at.is_not(None),
            sources.c.license_label != "",
            document_versions.c.text_license == sources.c.license_label,
            document_versions.c.retrieval_status.in_(("ready", "text_only")),
            document_versions.c.expires_at > sa.func.now(),
        )
    )
    rows = {row.id: row for row in await session.execute(statement)}
    for ref in references:
        if _is_live_reference(ref):
            continue
        row = rows.get(ref["passage_id"])
        if row is None or not ref.get("exact_quote") or ref["exact_quote"] not in row.text:
            return None
    return rows


async def save_report(session: AsyncSession, analysis_id: str, report: dict, rows: dict) -> None:
    refs = {ref["source_id"]: ref for ref in report.get("sources", [])}
    for verdict in report.get("claims", []):
        claim_id = await session.scalar(
            sa.insert(claims)
            .values(
                analysis_id=analysis_id,
                text=verdict["text"],
                kind="non_claim" if verdict["verdict"] == "not_verifiable" else "verifiable",
                verdict=verdict["verdict"],
                rationale=verdict["rationale"],
            )
            .returning(claims.c.id)
        )
        for source_id in verdict["evidence_source_ids"]:
            reference = refs[source_id]
            if _is_live_reference(reference):
                # Preserve source and quote in response_json; the normalized
                # evidence table only accepts persisted passages with UUID IDs.
                continue
            await session.execute(
                sa.insert(evidence).values(
                    claim_id=claim_id,
                    passage_id=reference["passage_id"],
                    relation=reference.get("relation", "context"),
                    exact_quote=reference["exact_quote"],
                    validation_passed=True,
                    checked_at=sa.func.now(),
                )
            )
    for frame in report.get("narratives", []):
        reference = refs[frame["source_id"]]
        if _is_live_reference(reference):
            continue
        await session.execute(
            sa.insert(narrative_frames).values(
                analysis_id=analysis_id,
                document_id=rows[reference["passage_id"]].document_id,
                emphasis_json=frame["emphasis"],
                highlighted_passage_ids=frame["highlighted_passage_ids"],
                uncertainty_text=frame["caveat"],
            )
        )
