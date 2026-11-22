"""Regression: no punctuation/keyword verdict rules, prior reviews remain metadata."""

import json

import httpx
import pytest

from prawdowid.api.schemas import AnalysisReport
from prawdowid.application.analyses import Submission
from prawdowid.application.evidence import EvidenceService
from prawdowid.application.pipeline import AnalysisPipeline
from prawdowid.infrastructure.factchecks import (
    FactCheckLookup,
    GoogleClaimReviewAPI,
    PolishFactcheckRSS,
)


class AlternatingExtractor:
    def __init__(self):
        self.extract_calls = 0
        self.assess_calls = 0

    async def complete_json(self, *, schema, prompt):
        payload = json.loads(prompt)
        if payload["task"] == "extract":
            self.extract_calls += 1
            kind = "non_claim" if self.extract_calls % 2 else "verifiable"
            return {"claims": [{"text": payload["input_text"], "kind": kind}]}
        assert payload["task"] == "assess"
        self.assess_calls += 1
        text = payload["statement"]
        if text.startswith("SCT to"):
            return {"kind": "opinion", "scope": "unknown", "reason": "This is a personal value judgment without an agreed measurement."}
        if text.startswith("Moja krowa"):
            return {"kind": "verifiable", "scope": "private", "reason": "Testing this individual cow requires observations of that animal.", "empirical_test": "Observe this specific cow producing milk."}
        if text == "Strefa czystego transportu":
            return {"kind": "non_claim", "scope": "unknown", "reason": "Topic name with no claim about the world."}
        return {"kind": "verifiable", "scope": "public", "reason": "A record of the formal proposal could confirm or refute the assertion.", "empirical_test": "Find the official proposal or public record by Kraków authorities."}


@pytest.mark.asyncio
async def test_second_semantic_review_stabilizes_both_first_round_outputs():
    provider = AlternatingExtractor()
    service = EvidenceService(provider)
    examples = (
        ("Władze Krakowa zaproponowały zmniejszenie SCT", "verifiable", "public"),
        ("SCT to fatalny pomysł", "opinion", "unknown"),
        ("Moja krowa daje mleko", "verifiable", "private"),
        ("Strefa czystego transportu", "non_claim", "unknown"),
    )
    for _ in range(10):
        for text, kind, scope in examples:
            _, claims = await service.extract(text)
            assert len(claims) == 1
            assert claims[0].text == text
            assert claims[0].kind == kind
            assert claims[0].scope == scope
            assert claims[0].reviewed
            if kind == "verifiable":
                assert claims[0].empirical_test
    assert provider.extract_calls == provider.assess_calls == 40


@pytest.mark.asyncio
async def test_missing_empirical_test_never_launches_online_search():
    class InvalidReview(AlternatingExtractor):
        async def complete_json(self, *, schema, prompt):
            payload = json.loads(prompt)
            if payload["task"] == "assess":
                return {"kind": "verifiable", "scope": "public", "reason": "An assertion, but without a proposed test."}
            return await super().complete_json(schema=schema, prompt=prompt)

    _, claims = await EvidenceService(InvalidReview()).extract("Warszawa ogłosiła nowe prawo")
    assert claims[0].kind == "unsupported_scope"
    assert claims[0].scope == "unknown"


@pytest.mark.asyncio
async def test_google_claimreview_retains_attribution_not_verdict_authority():
    def reply(request):
        assert request.url.host == "factchecktools.googleapis.com"
        assert request.headers["X-Goog-Api-Key"] == "test-api-key"
        assert "test-api-key" not in str(request.url)
        return httpx.Response(200, json={"claims": [{
            "text": "Inne twierdzenie o SCT",
            "claimReview": [{
                "publisher": {"name": "Demagog"},
                "url": "https://demagog.org.pl/analiza/123",
                "title": "Ocena wypowiedzi",
                "textualRating": "Fałsz",
                "reviewDate": "2026-11-21T11:00:00Z",
            }],
        }]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        api = GoogleClaimReviewAPI(client, "test-api-key")
        results, errors = await FactCheckLookup((api,)).search("Władze Krakowa zmniejszyły SCT")
    assert errors == ()
    assert results[0].reviewed_claim == "Inne twierdzenie o SCT"
    entry = results[0].as_report("c1")
    assert entry["match_status"] == "not_assessed"
    assert entry["published_rating"] == "Fałsz"
    assert "verdict" not in entry


@pytest.mark.asyncio
async def test_keyless_polish_factcheck_search_returns_links_only():
    xml = '''<rss><channel><item>
      <title>Sprawdzenie wypowiedzi</title>
      <link>https://news.google.com/rss/articles/example</link>
      <source url="https://demagog.org.pl">Demagog</source>
      <pubDate>Sat, 21 Nov 2026 12:00:00 GMT</pubDate>
    </item></channel></rss>'''

    def reply(request):
        assert "site:demagog.org.pl" in request.url.params["q"]
        return httpx.Response(200, content=xml)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        result = await PolishFactcheckRSS(client).search("strefa czystego transportu")
    assert len(result) == 1
    assert result[0].published_rating is None
    assert result[0].reviewed_claim is None
    assert result[0].publisher == "Demagog"


@pytest.mark.asyncio
async def test_factcheck_failure_is_not_transformed_into_a_verdict():
    def reply(_):
        raise httpx.ConnectError("connection failed")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        service = FactCheckLookup((PolishFactcheckRSS(client),))
        result, errors = await service.search("Claim about a local rule")
    assert not result and errors == ("PolishFactcheckRSS: unavailable",)


@pytest.mark.asyncio
async def test_lookup_cache_avoids_repeated_external_requests():
    count = 0

    def reply(request):
        nonlocal count
        count += 1
        return httpx.Response(200, json={"claims": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        service = FactCheckLookup((GoogleClaimReviewAPI(client, "key"),))
        for _ in range(12):
            result, errors = await service.search("Exactly the same public claim")
            assert result == () and errors == ()
    assert count == 1


@pytest.mark.asyncio
async def test_report_links_existing_factchecks_to_individual_claim_without_evidence():
    class FakeRetrieval:
        async def search(self, event, text):
            class Result:
                passages = ()
                warnings = ()
            return Result()

    class FakeDiscovery:
        async def discover(self, event):
            from prawdowid.infrastructure.discovery import DiscoveryReport
            return DiscoveryReport()

    class FakeCorpus:
        async def enrich(self, candidates):
            return False

    class FakeLookup:
        async def search(self, statement):
            from prawdowid.infrastructure.factchecks import PriorReview
            return ((PriorReview(
                publisher="Demagog", title="Inne twierdzenie", url="https://demagog.org.pl/c",
                reviewed_claim="Nieco inne zdarzenie", published_rating="Fałsz",
                provider="google_fact_check",
            ),), ())

    report = await AnalysisPipeline(
        EvidenceService(AlternatingExtractor()), FakeRetrieval(), FakeDiscovery(),
        FakeCorpus(), factchecks=FakeLookup(),
    ).analyze("demo", Submission("text", "Władze Krakowa zaproponowały zmniejszenie SCT", web_search=True))
    assert report["claims"][0]["claim_type"] == "factual"
    assert report["claims"][0]["verdict"] == "insufficient_evidence"
    assert report["sources"] == []
    assert report["fact_checks"][0]["claim_id"] == "c1"
    assert report["fact_checks"][0]["published_rating"] == "Fałsz"
    AnalysisReport.model_validate(report)


@pytest.mark.asyncio
async def test_malformed_google_claimreview_shape_is_not_a_verdict():
    def invalid_response(_):
        return httpx.Response(200, json={"claims": {"invalid": "shape"}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(invalid_response)) as client:
        result, errors = await FactCheckLookup((GoogleClaimReviewAPI(client, "test-key"),)).search(
            "Władze Krakowa zaproponowały zmniejszenie SCT"
        )
    assert result == ()
    assert errors == ("GoogleClaimReviewAPI: unavailable",)
