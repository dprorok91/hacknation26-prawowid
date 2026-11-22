"""Public official document URLs keep query identifiers, without exposing secrets."""

import json

import pytest

from prawdowid.application.evidence import EvidenceService
from prawdowid.domain.models import Claim, EvidencePassage, Verdict
from prawdowid.domain.urls import canonical_url, parse_http_url


@pytest.mark.parametrize(
    "url",
    [
        "https://www.bip.krakow.pl/?dok_id=226666",
        "https://www.bip.krakow.pl/?dok_id=abc&lang=pl",
        "https://gov.pl/doc?id=123&access_token=secret",
        "https://gov.pl/doc#/_/mazowieckie",
    ],
)
def test_public_document_query_parameters_are_valid_http_syntax(url):
    assert parse_http_url(url).scheme == "https"


def test_canonical_url_preserves_document_identity_parameters():
    url = "https://www.bip.krakow.pl/?dok_id=226666&lang=pl&utm_source=abc"
    assert canonical_url(url) == "https://www.bip.krakow.pl/?dok_id=226666&lang=pl"


@pytest.mark.asyncio
async def test_hosted_verification_accepts_approved_bip_passage_with_query_id():
    quote = "Strefa Czystego Transportu (SCT) obowiązuje od 01.01.2026 r."
    claim = Claim("c1", quote, "verifiable", "public")
    passage = EvidencePassage(
        "p1", "d1", quote, "BIP Krakowa", "Strefa Czystego Transportu",
        "https://www.bip.krakow.pl/?dok_id=226666", None,
    )

    class Model:
        async def complete_json(self, *, schema, prompt):
            payload = json.loads(prompt)
            assert payload["passages"][0]["text"] == quote
            return {
                "citations": [{"passage_id": "p1", "quote": quote, "relation": "supports"}],
                "missing_context": False,
            }

    result = await EvidenceService(Model(), public_sources_only=True).verify(claim, [passage])
    assert result.verdict == Verdict.CONFIRMED
    assert len(result.citations) == 1


@pytest.mark.asyncio
async def test_hosted_verification_accepts_non_bip_document_with_public_params():
    quote = "Informacja o zmianie przepisów obowiązuje od 1 stycznia 2026 r."
    claim = Claim("c1", quote, "verifiable", "public")
    passage = EvidencePassage(
        "p1", "d1", quote, "Dziennik Urzędowy", "Akt prawny",
        "https://edziennik.mazowieckie.pl/legalact/2026/101?id=101&lang=pl", None,
    )

    class Model:
        async def complete_json(self, *, schema, prompt):
            payload = json.loads(prompt)
            assert payload["passages"][0]["text"] == quote
            return {
                "citations": [{"passage_id": "p1", "quote": quote, "relation": "supports"}],
                "missing_context": False,
            }

    result = await EvidenceService(Model(), public_sources_only=True).verify(claim, [passage])
    assert result.verdict == Verdict.CONFIRMED
    assert len(result.citations) == 1


@pytest.mark.asyncio
async def test_hosted_verification_does_not_discard_passages_due_to_url_parameters():
    quote = "Rozporządzenie obowiązuje od 1 stycznia 2026 roku."

    class Model:
        async def complete_json(self, *, schema, prompt):
            payload = json.loads(prompt)
            assert payload["passages"][0]["text"] == quote
            # Neither URL nor token should be sent to the model in evidence payload.
            assert "access_token" not in prompt
            return {
                "citations": [{"passage_id": "p1", "quote": quote, "relation": "supports"}],
                "missing_context": False,
            }

    passage = EvidencePassage(
        "p1", "d1", quote, "Portal", "Dokument",
        "https://gov.pl/doc?id=123&access_token=secret", None,
    )
    claim = Claim("c1", quote, "verifiable", "public")
    result = await EvidenceService(Model(), public_sources_only=True).verify(claim, [passage])
    assert result.verdict == Verdict.CONFIRMED
    assert len(result.citations) == 1
