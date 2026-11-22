"""Isolated PRAWDOWID V31 LLM assessment: no DB, API, queue or repo writes.

Use from the project checkout after V31 has passed the watcher:
  uv run --locked python ~/Downloads/prawowid_v31_llm_assessment.py --offline
  uv run --locked python ~/Downloads/prawowid_v31_llm_assessment.py --openrouter --report ~/Downloads/prawowid_v31_llm_results.json
  uv run --locked python ~/Downloads/prawowid_v31_llm_assessment.py --live --report ~/Downloads/prawowid_v31_live_llm_results.json

--offline: deterministic adversarial mock assessments (NO API, NO network).
--openrouter: live model over synthetic PUBLIC fixtures (uses configured key).
--live: same model + actual SafeWeb retrieval from ZDMK and gov.pl. One official
site at a time; no database, discovery service or source approval changes.
All model results are assessed against deliberately limited fixture expectations.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from prawdowid.application.evidence import EvidenceService
from prawdowid.application.evidence_selection import select
from prawdowid.config import get_settings
from prawdowid.domain.models import Claim, EvidencePassage, Verdict
from prawdowid.infrastructure.live_official import (
    OFFICIAL_SCT_PROPOSAL,
    LiveOfficialEvidence,
    matching_live_passages,
    official_entrypoints,
)
from prawdowid.infrastructure.openrouter import OpenRouterClient
from prawdowid.infrastructure.safe_web import SafeWeb, extract_article

SCT_CLAIM = "Władze Krakowa zaproponowały zmniejszenie SCT."
SCT_ENACTED = "Władze Krakowa już wprowadziły zmniejszoną SCT."
SCT_SOURCE = OFFICIAL_SCT_PROPOSAL
SCT_BODY = "\n\n".join((
    "Niezależnie od konsultacji społecznych miasto przygotowało projekt zmiany uchwały o SCT. Projekt dotyczy zasięgu strefy i zasad jej działania.",
    "Najważniejsze założenia projektu",
    "Mniejsza strefa",
    "Urzędnicy przedstawili dodatkowo informacje o konsultacjach i harmonogramie prac. " * 6,
    "Obecnie SCT obejmuje ok. 196 km², czyli niemal całe miasto. Po zmianie obejmie ok. 0,99 km² w centrum. Uzasadnienie zmiany opisujemy poniżej.",
    "Do czasu przyjęcia uchwały obowiązują dotychczasowe granice i zasady strefy.",
    "Zobacz także: Założenia do projektu uchwały Rady Miasta Krakowa [pdf]",
))
BRIDGE = "Most Nova w Toruniu został już otwarty dla samochodów."
BRIDGE_QUOTE = "Most Nova pozostaje zamknięty dla samochodów. Wjazd publiczny jest nadal zabroniony."
BOREAL = "Program Boreal zmniejszył liczbę hospitalizacji o 30 procent."
BOREAL_QUOTE = "W ramach programu Boreal nie mierzono hospitalizacji. Nie ma danych pozwalających stwierdzić spadek liczby przyjęć do szpitala."
GOV_URL = (
    "https://www.gov.pl/web/infrastruktura/swiatowy-dzien-pamieci-"
    "ofiar-wypadkow-drogowych---dzialamy-na-rzecz-poprawy-bezpieczenstwa-na-drogach"
)
GOV_CLAIM = (
    "Światowy Dzień Pamięci Ofiar Wypadków Drogowych obchodzony jest w trzecią "
    "niedzielę listopada i został zainicjowany w 1993 r."
)


@dataclass(frozen=True)
class AssessmentCase:
    id: str
    claim: str
    passages: tuple[EvidencePassage, ...]
    expected: tuple[Verdict, ...]
    require_quotes: int


def plain_passage(source: str, text: str, idx: int = 0) -> EvidencePassage:
    return EvidencePassage(
        passage_id=f"corpus:{idx}", document_id=f"corpus:{source}",
        exact_text=text, publisher="Fixture (fictional)", title="Isolated model fixture",
        url=source, published_at=None,
    )


def sct_passages(body: str = SCT_BODY) -> tuple[EvidencePassage, ...]:
    # This uses the same V31 selector and provenance as the integrated live source.
    from prawdowid.infrastructure.live_official import LiveOfficialPassage

    picked, _trace = select(body, SCT_CLAIM)
    return tuple(
        LiveOfficialPassage(
            passage_id=f"live:fixture:{span.start}:{span.end}",
            document_id="live:fixture", exact_text=span.text,
            publisher="Zarząd Dróg Miasta Krakowa", title="SCT proposal",
            url=SCT_SOURCE, published_at=None, selected_for=(SCT_CLAIM,),
            start=span.start, end=span.end,
        )
        for span in picked
    )


def fixture_cases() -> tuple[AssessmentCase, ...]:
    sct = sct_passages()
    return (
        AssessmentCase("SCT_PROPOSAL_TWO_QUOTES", SCT_CLAIM, sct,
                       (Verdict.CONFIRMED,), 2),
        AssessmentCase("SCT_NOT_ENACTED", SCT_ENACTED, sct,
                       (Verdict.INSUFFICIENT_EVIDENCE, Verdict.FALSE), 0),
        AssessmentCase("BRIDGE_STILL_CLOSED", BRIDGE,
                       (plain_passage("https://example.org/bridge", BRIDGE_QUOTE),),
                       (Verdict.FALSE,), 1),
        AssessmentCase("WRONG_METRIC_ABSTAIN", BOREAL,
                       (plain_passage("https://example.org/boreal", BOREAL_QUOTE),),
                       (Verdict.INSUFFICIENT_EVIDENCE, Verdict.FALSE), 0),
    )


class MockModel:
    """Return known adversarial structured outputs; no real LLM used."""

    def __init__(self, *, mode: str):
        self.mode = mode

    async def complete_json(self, *, schema, prompt):
        req = json.loads(prompt)
        rows = req["passages"]
        if self.mode == "all":
            citations = [
                {"passage_id": row["passage_id"], "quote": row["text"],
                 "relation": "supports"}
                for row in rows
            ]
        elif self.mode == "one":
            citations = [{"passage_id": rows[0]["passage_id"],
                          "quote": rows[0]["text"], "relation": "supports"}]
        elif self.mode == "fabricated":
            citations = [{"passage_id": rows[0]["passage_id"],
                          "quote": "THIS NEVER APPEARED ON THE PAGE", "relation": "supports"}]
        else:
            citations = []
        return {"citations": citations, "missing_context": False}


async def run_offline() -> tuple[bool, list[dict]]:
    rows = []
    for case, mode, allowed in (
        (fixture_cases()[0], "all", {Verdict.CONFIRMED}),
        (fixture_cases()[0], "one", {Verdict.INSUFFICIENT_EVIDENCE}),
        (fixture_cases()[0], "fabricated", {Verdict.INSUFFICIENT_EVIDENCE}),
        (fixture_cases()[1], "all", {Verdict.INSUFFICIENT_EVIDENCE}),
    ):
        outcome = await EvidenceService(MockModel(mode=mode)).verify(
            Claim(case.id, case.claim, "verifiable", "public"), case.passages
        )
        passed = outcome.verdict in allowed
        entry = {"id": case.id + ":" + mode, "status": "PASS" if passed else "FAIL",
                 "verdict": outcome.verdict.value, "citations": len(outcome.citations)}
        rows.append(entry)
        print("OFFLINE", entry)
    return all(x["status"] == "PASS" for x in rows), rows


async def run_cases(model, cases: tuple[AssessmentCase, ...], *, label: str):
    results = []
    for case in cases:
        try:
            outcome = await EvidenceService(model).verify(
                Claim(case.id, case.claim, "verifiable", "public"), case.passages
            )
            actual = outcome.verdict
            count = len(outcome.citations)
            exact = all(item.quote in item.passage.exact_text for item in outcome.citations)
            passed = actual in case.expected and count >= (
                case.require_quotes if actual is Verdict.CONFIRMED else 0
            ) and exact
            result = {"id": case.id, "status": "PASS" if passed else "FAIL",
                      "verdict": actual.value, "expected": [v.value for v in case.expected],
                      "citations": count, "exact": exact,
                      "quotes": [{"url": item.passage.url, "quote": item.quote[:500],
                                  "relation": item.relation.value}
                                 for item in outcome.citations]}
        except Exception as exc:  # noqa: BLE001 - fail closed and report provider errors
            result = {"id": case.id, "status": "ERROR", "error_type": type(exc).__name__}
        results.append(result)
        print(label, json.dumps(result, ensure_ascii=False))
    return results


async def live_cases(client: httpx.AsyncClient):
    web = SafeWeb(client, timeout=20)
    # Site 1: reviewed municipal adapter, with the V31 integrated passage selector.
    chosen = await LiveOfficialEvidence(web).collect(
        official_entrypoints(SCT_CLAIM), (Claim("c1", SCT_CLAIM),)
    )
    sct = matching_live_passages(SCT_CLAIM, chosen.passages)
    if not sct:
        raise RuntimeError("ZDMK retrieval produced no claim passages")
    # Site 2: gov.pl article, assessed only transiently, not written to the corpus.
    page = await web.read_article(GOV_URL)
    article = extract_article(
        page.content, allow_text=True, robots=page.robots, source_url=page.url
    )
    if not article.body:
        raise RuntimeError("gov.pl extraction produced no editorial content")
    excerpts, _ = select(article.body, GOV_CLAIM)
    gov = tuple(
        EvidencePassage(
            passage_id=f"gov:{i}", document_id="gov:1", exact_text=p.text,
            publisher="Ministerstwo Infrastruktury", title=article.title or "gov.pl",
            url=page.url, published_at=article.published_at,
        ) for i, p in enumerate(excerpts)
    )
    if not gov:
        raise RuntimeError("gov.pl selector produced no evidence")
    return (
        AssessmentCase("LIVE_ZDMK_SCT", SCT_CLAIM, sct,
                       (Verdict.CONFIRMED,), 2),
        AssessmentCase("LIVE_GOVPL_EVENT", GOV_CLAIM, gov,
                       (Verdict.CONFIRMED,), 1),
    )


async def execute(args):
    rows = {}
    success = True
    if args.offline:
        ok, rows["offline"] = await run_offline()
        success &= ok
    if args.openrouter or args.live:
        settings = get_settings()
        if not settings.openrouter_api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not configured")
        async with httpx.AsyncClient(trust_env=False, timeout=60) as client:
            model = OpenRouterClient(
                client, settings.worker_openrouter_key(), settings.openrouter_model,
                timeout_seconds=50,
            )
            if args.openrouter:
                rows["model_fixtures"] = await run_cases(model, fixture_cases(), label="MODEL")
                success &= all(x["status"] == "PASS" for x in rows["model_fixtures"])
            if args.live:
                try:
                    cases = await live_cases(client)
                    rows["live_sites"] = await run_cases(model, cases, label="LIVE")
                    success &= all(x["status"] == "PASS" for x in rows["live_sites"])
                except Exception as exc:  # noqa: BLE001 - fail closed and report provider errors
                    rows["live_sites"] = [{"status": "ERROR", "error_type": type(exc).__name__,
                                           "reason": str(exc)[:150]}]
                    print("LIVE_ERROR", rows["live_sites"])
                    success = False
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print("REPORT:", args.report)
    print("=== BEGIN PASTE TO CHATGPT: V31 LLM ASSESSMENT ===")
    print(json.dumps({"status": "PASS" if success else "FAIL", "results": rows},
                     ensure_ascii=False, indent=2))
    print("=== END PASTE TO CHATGPT: V31 LLM ASSESSMENT ===")
    return 0 if success else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true", help="deterministic adversarial fake model")
    parser.add_argument("--openrouter", action="store_true", help="actual paid model, synthetic fixtures")
    parser.add_argument("--live", action="store_true", help="actual paid model + two official websites")
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()
    if not (args.offline or args.openrouter or args.live):
        parser.error("select --offline, --openrouter and/or --live")
    try:
        return asyncio.run(execute(args))
    except Exception as exc:  # noqa: BLE001 - fail closed and report provider errors
        print("FAILED:", type(exc).__name__, str(exc)[:150], file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
