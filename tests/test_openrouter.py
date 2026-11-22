import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr

from prawdowid.application.analyses import AnalysisError, ErrorCode
from prawdowid.application.evidence import EvidenceService, _Assessment, _Extraction, _Judgment
from prawdowid.domain.models import Claim, EvidencePassage, Verdict
from prawdowid.infrastructure.openrouter import OpenRouterClient

MODEL = "openai/gpt-4.1-mini"
KEY = SecretStr("unit-test-key")
URL = "https://openrouter.ai/api/v1/chat/completions"


def completion(content="{}", **overrides):
    choice = {"finish_reason": "stop", "message": {"role": "assistant", "content": content}}
    return {"choices": [choice], **overrides}


def check_schema(node):
    if isinstance(node, list):
        for item in node:
            check_schema(item)
    if isinstance(node, dict):
        assert "default" not in node
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert node["required"] == list(node["properties"])
        for item in node.values():
            check_schema(item)


@pytest.mark.parametrize("schema_type", [_Extraction, _Assessment, _Judgment])
async def test_openrouter_strict_schema_and_bounded_request(schema_type):
    original = schema_type.model_json_schema()
    prompt = json.dumps({"task": "extract", "input_text": "Publiczny dokument"})

    def reply(request):
        assert str(request.url) == URL
        assert request.headers["authorization"] == "Bearer unit-test-key"
        body = json.loads(request.content)
        assert body["messages"][1] == {"role": "user", "content": prompt}
        assert body["model"] == MODEL and body["stream"] is False
        assert body["provider"]["require_parameters"] is True
        assert body["max_tokens"] > 0 and "tools" not in body
        assert body["response_format"]["type"] == "json_schema"
        spec = body["response_format"]["json_schema"]
        assert spec["strict"] is True
        check_schema(spec["schema"])
        assert spec["schema"]["properties"].keys() == original["properties"].keys()
        assert "unit-test-key" not in request.content.decode()
        return httpx.Response(200, json=completion())

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        assert (
            await OpenRouterClient(client, KEY, MODEL).complete_json(schema=original, prompt=prompt)
            == {}
        )
        assert "authorization" not in client.headers
    assert original == schema_type.model_json_schema()


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("401", ErrorCode.MODEL_UNAVAILABLE),
        ("429", ErrorCode.PROVIDER_RATE_LIMITED),
        ("timeout", ErrorCode.TIMEOUT),
        ("307", ErrorCode.MODEL_UNAVAILABLE),
        ("malformed", ErrorCode.MODEL_UNAVAILABLE),
        ("oversize", ErrorCode.MODEL_UNAVAILABLE),
        ("truncated", ErrorCode.MODEL_UNAVAILABLE),
        ("refusal", ErrorCode.MODEL_UNAVAILABLE),
        ("tools", ErrorCode.MODEL_UNAVAILABLE),
        ("error_envelope", ErrorCode.PROVIDER_RATE_LIMITED),
    ],
)
async def test_openrouter_failures_are_typed_and_sanitized(kind, expected):
    calls = []

    def reply(request):
        calls.append(str(request.url))
        if kind.isdigit():
            return httpx.Response(
                int(kind),
                headers={"location": "https://other.invalid"},
                text="unsafe provider details",
            )
        if kind == "timeout":
            raise httpx.ReadTimeout("unsafe provider details")
        if kind == "oversize":
            return httpx.Response(200, content=b" " * 262145)
        if kind == "error_envelope":
            return httpx.Response(200, json={"error": {"code": 429, "message": "unsafe details"}})
        resp = completion("unsafe provider details" if kind == "malformed" else "{}")
        choice = resp["choices"][0]
        if kind == "truncated":
            choice["finish_reason"] = "length"
        if kind == "refusal":
            choice["message"]["refusal"] = "unsafe provider details"
        if kind == "tools":
            choice["message"]["tool_calls"] = [{"type": "function"}]
        return httpx.Response(200, json=resp)

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply), follow_redirects=True) as c:
        with pytest.raises(AnalysisError) as err:
            await OpenRouterClient(c, KEY, MODEL).complete_json(
                schema={"type": "object"}, prompt="{}"
            )
    assert calls == [URL]
    assert err.value.code == expected
    assert "unsafe" not in str(err.value) and "unit-test-key" not in str(err.value)


async def test_openrouter_job_usage_isolated_and_limited():
    active = peak = 0

    async def reply(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        usage = json.loads(json.loads(request.content)["messages"][1]["content"])
        return httpx.Response(200, json=completion(usage=usage))

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        model = OpenRouterClient(client, KEY, MODEL, max_concurrency=1)

        async def run(counters):
            with model.measure_usage() as usage:
                for observed in counters:
                    await model.complete_json(
                        schema={"type": "object"}, prompt=json.dumps(observed)
                    )
                return usage

        one = {"prompt_tokens": 12, "completion_tokens": 2, "cost": 0.000125}
        known, partial = await asyncio.gather(run([one, one]), run([one, {}, one]))
    assert peak == 1
    assert (known.tokens_in, known.tokens_out, known.cost_usd) == (24, 4, Decimal("0.000250"))
    assert (partial.tokens_in, partial.tokens_out, partial.cost_usd) == (None, None, None)


async def test_openrouter_rejects_invented_citation_under_existing_evidence_rules():
    citation = {
        "passage_id": "p1",
        "quote": "Edukacja zostanie zreformowana.",
        "relation": "supports",
    }

    def reply(request):
        return httpx.Response(200, json=completion(json.dumps({"citations": [citation]})))

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        claim = Claim("c1", "Edukacja zostanie zreformowana.", "verifiable", "public")
        passage = EvidencePassage(
            "p1", "d1", "Ministerstwo opublikowało raport.", "Gov", "Raport", "https://gov.pl", None
        )
        result = await EvidenceService(OpenRouterClient(client, KEY, MODEL)).verify(
            claim, [passage]
        )
    assert result.citations == ()
    assert result.verdict == Verdict.INSUFFICIENT_EVIDENCE


async def test_hosted_verification_filters_secondary_sources_with_private_query():
    private = EvidencePassage(
        "p0", "d0", "private sentinel", "Gov", "Restricted", "https://gov.pl/doc?user=1", None
    )
    public = EvidencePassage(
        "p1", "d1", "Opublikowano raport.", "Gov", "Raport", "https://gov.pl/report", None
    )
    sent = []

    def reply(request):
        assert b"private sentinel" not in request.content
        payload = json.loads(json.loads(request.content)["messages"][1]["content"])
        sent.extend(row["passage_id"] for row in payload["passages"])
        return httpx.Response(200, json=completion('{"citations":[]}'))

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        model = OpenRouterClient(client, KEY, MODEL)
        claim = Claim("c1", "Opublikowano raport.", "verifiable", "public")
        await EvidenceService(model, public_sources_only=True).verify(claim, [private, public])
    assert sent == ["p1"]
