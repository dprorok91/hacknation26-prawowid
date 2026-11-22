"""No external API calls: provider contracts, cost gates and correctness boundaries."""

import json

import httpx
import pytest

from prawdowid.domain.models import EventQuery
from prawdowid.infrastructure.discovery import (
    DiscoveredArticle,
    DiscoveryReport,
)
from prawdowid.infrastructure.search_v2 import (
    ParallelSearch,
    SourceFirstDiscovery,
    _annotations,
    plan_queries,
)


@pytest.mark.parametrize("text, expected", [
    ("Strefa Czystego Transportu w Krakowie zaczęła obowiązywać 1 stycznia 2026 roku", "styczeń"),
    ("Od 1 stycznia 2026 wszystkie samochody spalinowe nie mogą wjechać do SCT w Krakowie bez żadnych wyjątków", "wyjątki"),
    ("SCT w Krakowie sama obniżyła NO2 o 10% do listopada 2026", "NO2"),
])
def test_query_strategy_reaches_correct_question(text, expected):
    plan = plan_queries(EventQuery(text))
    assert expected.casefold() in " ".join((*plan.news, plan.web)).casefold()
    assert all("Kraków" in query for query in (*plan.news, plan.web))
    assert len(plan.news) == 2


def test_unsafe_or_unverified_citation_links_are_excluded():
    payload = {"choices": [{"message": {"content": "[fake](https://fake.example)",
        "annotations": [
            {"type": "url_citation", "url_citation": {"url": "http://127.0.0.1/secret", "title": "Private"}},
            {"type": "url_citation", "url_citation": {"url": "https://gov.pl/web/test", "title": "Official"}},
            {"type": "url_citation", "url_citation": {"url": "https://gov.pl/web/test", "title": "Duplicate"}},
        ]}}]}
    matches = _annotations(payload, "parallel_turbo", 5)
    assert len(matches) == 1
    assert matches[0].canonical_url == "https://gov.pl/web/test"
    assert matches[0].provider == "parallel_turbo"


@pytest.mark.asyncio
async def test_turbo_parallel_request_has_hard_one_call_and_correct_mode():
    requests = []

    def reply(request):
        body = json.loads(request.content)
        requests.append(body)
        assert request.headers["Authorization"] == "Bearer testing-key"
        return httpx.Response(200, json={"usage": {"server_tool_use_details": {"web_search_requests": 1}},
            "choices": [{"message": {"annotations": [{"type": "url_citation", "url_citation": {
                "url": "https://bip.krakow.pl/notice", "title": "Uchwała"}}]}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await ParallelSearch(client, "testing-key", model="openai/gpt-4.1-mini", timeout=2).discover(
            "Kraków SCT wyjątki", "turbo"
        )
    assert len(result) == 1
    assert requests[0]["max_tool_calls"] == 1
    assert requests[0]["tools"][0]["parameters"]["engine"] == "parallel"
    assert requests[0]["tools"][0]["parameters"]["mode"] == "turbo"


@pytest.mark.asyncio
async def test_no_credits_uses_news_only_and_preserves_queries():
    class News:
        async def discover(self, event):
            doc = DiscoveredArticle(
                "https://news.google.com/rss/articles/abc", "Kraków", "SCT", None,
                "google_news_rss", "krakow.pl",
            )
            return DiscoveryReport((doc,))

    class Budget:
        async def claim(self, provider):
            assert provider == "parallel_search"
            return False

    class Parallel:
        async def discover(self, *args):
            pytest.fail("No paid calls when budget exhausted")

    result = await SourceFirstDiscovery(News(), parallel=Parallel(), budget=Budget()).discover(
        EventQuery("SCT Kraków bez żadnych wyjątków")
    )
    assert len(result.candidates) == 1
    assert len(result.queries) == 3
    assert result.queries[-1]["status"] == "budget_exhausted"


@pytest.mark.asyncio
async def test_official_direct_link_prevents_paid_search():
    class News:
        async def discover(self, event):
            doc = DiscoveredArticle("https://www.bip.krakow.pl/act", "BIP", "Uchwała", None,
                                    "google_news_rss", "bip.krakow.pl")
            return DiscoveryReport((doc,))

    class Parallel:
        async def discover(self, *args):
            pytest.fail("Direct official results should avoid paid search")

    result = await SourceFirstDiscovery(News(), parallel=Parallel()).discover(
        EventQuery("SCT Kraków bez wyjątków")
    )
    assert len(result.candidates) == 1
    assert len(result.queries) == 2


@pytest.mark.asyncio
async def test_parallel_turbo_falls_back_to_basic_and_preserves_news():
    class News:
        async def discover(self, event):
            doc = DiscoveredArticle("https://news.google.com/rss/articles/abc", "Kraków", "SCT", None,
                                    "google_news_rss", "krakow.pl")
            return DiscoveryReport((doc,))

    class Parallel:
        def __init__(self):
            self.calls = []

        async def discover(self, query, mode):
            self.calls.append(mode)
            if mode == "turbo":
                from prawdowid.infrastructure.discovery import ProviderFailure
                raise ProviderFailure("no_usable_citations", retryable=False)
            return (DiscoveredArticle("https://www.bip.krakow.pl/act", "BIP", "Uchwała", None,
                                      "parallel_basic", "bip.krakow.pl"),)

    parallel = Parallel()
    result = await SourceFirstDiscovery(News(), parallel=parallel).discover(
        EventQuery("SCT Kraków bez wyjątków")
    )
    assert parallel.calls == ["turbo", "basic"]
    assert len(result.candidates) == 2
    assert result.errors[0].error_code == "no_usable_citations"


@pytest.mark.asyncio
async def test_parallel_cache_avoids_second_paid_call():
    class News:
        async def discover(self, event):
            return DiscoveryReport()

    class Cache:
        def __init__(self):
            self.data = {}

        async def get(self, provider, event):
            return self.data.get((provider, event.query_text))
        async def put(self, provider, event, value):
            self.data[(provider, event.query_text)] = value

    class Parallel:
        calls = 0
        async def discover(self, query, mode):
            self.calls += 1
            return (DiscoveredArticle("https://www.bip.krakow.pl/act", "BIP", "Uchwała", None,
                                      "parallel_turbo", "bip.krakow.pl"),)

    parallel = Parallel()
    source = SourceFirstDiscovery(News(), parallel=parallel, cache=Cache())
    first = await source.discover(EventQuery("SCT Kraków bez wyjątków"))
    second = await source.discover(EventQuery("SCT Kraków bez wyjątków"))
    assert parallel.calls == 1
    assert first.queries[-1]["status"] == "completed"
    assert second.queries[-1]["status"] == "cached"


@pytest.mark.asyncio
async def test_single_sentence_extract_cannot_remove_exception():
    from prawdowid.application.evidence import EvidenceService

    original = "W Krakowie wszystkie samochody spalinowe mają zakaz wjazdu do SCT bez żadnych wyjątków"

    class Stub:
        async def complete_json(self, *, schema, prompt):
            payload = json.loads(prompt)
            if payload["task"] == "extract":
                return {"claims": [{"text": original.replace(" bez żadnych wyjątków", ""),
                                     "kind": "verifiable", "scope": "public"}],
                        "query_text": "Krakowie SCT"}
            return {"kind": "verifiable", "scope": "public", "reason": "Dokument prawny pozwala sprawdzić tezę.",
                    "empirical_test": "Sprawdzenie uchwały i zwolnień."}

    _event, claims = await EvidenceService(Stub()).extract(original)
    assert claims[0].text == original
    assert plan_queries(EventQuery(claims[0].text)).news[0].find("wyjątki") >= 0


@pytest.mark.asyncio
async def test_pipeline_exposes_queries_without_creating_evidence():
    from prawdowid.api.schemas import AnalysisReport
    from prawdowid.application.analyses import Submission
    from prawdowid.application.evidence import Verification
    from prawdowid.application.pipeline import AnalysisPipeline
    from prawdowid.domain.models import Claim, Verdict
    from prawdowid.infrastructure.search_v2 import SearchReport

    statement = "W Krakowie wszystkie samochody spalinowe nie mają wyjątków w SCT."

    class Evidence:
        async def extract(self, text):
            return EventQuery("SCT"), (Claim("c1", text, scope="public"),)

        async def verify(self, claim, passages):
            return Verification(Verdict.INSUFFICIENT_EVIDENCE, "Brak fragmentów źródeł.")

    class Retrieval:
        async def search(self, event, text):
            class Empty:
                passages = ()
                warnings = ()
            return Empty()

    class Discovery:
        async def discover(self, event):
            assert event.query_text == statement
            article = DiscoveredArticle(
                "https://www.bip.krakow.pl/?dok_id=226666", "BIP", "SCT",
                None, "parallel_turbo", "bip.krakow.pl",
            )
            return SearchReport((article,), (), ({
                "provider": "parallel_turbo", "query": "SCT Kraków wyjątki",
                "status": "completed",
            },))

    class Corpus:
        async def enrich(self, candidates):
            return False

    report = await AnalysisPipeline(Evidence(), Retrieval(), Discovery(), Corpus()).analyze(
        "a1", Submission("text", statement, web_search=True)
    )
    assert report["search"]["queries"] == [{
        "claim_id": "c1", "provider": "parallel_turbo",
        "query": "SCT Kraków wyjątki", "status": "completed",
    }]
    assert report["sources"] == []
    assert report["claims"][0]["verdict"] == "insufficient_evidence"
    AnalysisReport.model_validate(report)


@pytest.mark.asyncio
async def test_parallel_rejects_citations_without_search_use_proof():
    def reply(request):
        return httpx.Response(200, json={
            "choices": [{"message": {"annotations": [{"type": "url_citation",
                "url_citation": {"url": "https://gov.pl/source", "title": "Document"}}]}}],
            "usage": {"server_tool_use_details": {"web_search_requests": 0}},
        })

    from prawdowid.infrastructure.discovery import ProviderFailure
    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(ProviderFailure, match="search_not_invoked"):
            await ParallelSearch(client, "test-only", model="openai/gpt-4.1-mini", timeout=2).discover(
                "public search query", "turbo"
            )
