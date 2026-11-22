"""Regression for live evidence IDs vs PostgreSQL UUID foreign keys."""

from uuid import uuid4

import pytest

from prawdowid.infrastructure.reports import evidence_rows, save_report

LIVE_ID = "live:4ad620995594f316:357:556"
URL = ("https://zdmk.krakow.pl/nasze-dzialania/"
       "strefa-czystego-transportu-miasto-proponuje-zmiany/")


def live_ref(passage_id=LIVE_ID, url=URL):
    return {
        "source_id": f"{passage_id}:quote",
        "passage_id": passage_id,
        "url": url,
        "exact_quote": "Miasto przygotowało projekt zmiany uchwały o SCT.",
        "relation": "supports",
    }


class NoDBReads:
    async def execute(self, _statement):
        raise AssertionError("Live-only references must not query UUID passage tables")


@pytest.mark.asyncio
async def test_live_only_report_skips_uuid_query():
    rows = await evidence_rows(NoDBReads(), {"sources": [live_ref()]})
    assert rows == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("passage_id,url", [
    ("live:made-up:0", URL),
    (LIVE_ID, "https://example.org/attack"),
    (LIVE_ID, "http://zdmk.krakow.pl/unsafe"),
])
async def test_reject_invalid_live_reference(passage_id, url):
    assert await evidence_rows(
        NoDBReads(), {"sources": [live_ref(passage_id, url)]}
    ) is None


@pytest.mark.asyncio
async def test_invalid_non_live_id_fails_closed_before_uuid_bind():
    assert await evidence_rows(NoDBReads(), {"sources": [
        {**live_ref(), "passage_id": "not-a-uuid"}
    ]}) is None


class FakeWrites:
    def __init__(self):
        self.written_tables = []

    async def scalar(self, stmt):
        assert stmt.table.name == "claims"
        return str(uuid4())

    async def execute(self, stmt):
        self.written_tables.append(stmt.table.name)


@pytest.mark.asyncio
async def test_live_citations_remain_in_json_not_uuid_evidence_table():
    ref = live_ref()
    report = {
        "sources": [ref],
        "claims": [{
            "text": "Władze Krakowa zaproponowały zmniejszenie SCT.",
            "verdict": "confirmed", "rationale": "Zweryfikowano dwa fragmenty.",
            "evidence_source_ids": [ref["source_id"]],
        }],
        "narratives": [{
            "source_id": ref["source_id"], "emphasis": ["test"],
            "highlighted_passage_ids": [LIVE_ID], "caveat": "test",
        }],
    }
    writes = FakeWrites()
    await save_report(writes, str(uuid4()), report, {})
    assert "evidence" not in writes.written_tables
    assert "narrative_frames" not in writes.written_tables
    # save_report must not mutate the user-visible, quote-bearing JSON report.
    assert report["sources"] == [ref]
    assert report["claims"][0]["evidence_source_ids"] == [ref["source_id"]]
