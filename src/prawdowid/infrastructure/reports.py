"""Persist exact evidence links and enforce current publication permissions."""

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


async def evidence_rows(session: AsyncSession, report: dict) -> dict | None:
    references = report.get("sources", [])
    ids = {ref["passage_id"] for ref in references}
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
        await session.execute(
            sa.insert(narrative_frames).values(
                analysis_id=analysis_id,
                document_id=rows[reference["passage_id"]].document_id,
                emphasis_json=frame["emphasis"],
                highlighted_passage_ids=frame["highlighted_passage_ids"],
                uncertainty_text=frame["caveat"],
            )
        )
