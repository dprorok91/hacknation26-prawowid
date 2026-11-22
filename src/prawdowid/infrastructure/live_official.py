"""Bounded, transient evidence from reviewed municipal public-information pages.

This is NOT a general web-scraper or a publisher approval mechanism. The
explicit Krakow municipal road-authority scope is a limited public-information
exception; downloaded HTML is not saved to the corpus, and only short,
verbatim editorial passages are used in one report. SafeWeb enforces DNS,
robots, redirects, response size, and robots/noindex gates still apply.
"""

import re
from dataclasses import dataclass, replace
from hashlib import sha256
from urllib.parse import urlsplit

from prawdowid.application.evidence_selection import Span, select
from prawdowid.domain.models import Claim, DocumentCandidate, EvidencePassage
from prawdowid.domain.semantics import aliases_for
from prawdowid.domain.urls import canonical_url
from prawdowid.infrastructure.safe_web import FetchError, SafeWeb, extract_article

# Public-information source reviewed for this MVP, NOT arbitrary .krakow.pl sites.
# Information-sector reuse policy: https://www.bip.krakow.pl/?dok_id=48482
# Rights and excluded third-party material still require page-level screening.
MUNICIPAL_HOSTS = frozenset({"zdmk.krakow.pl"})
MAX_LIVE_PAGES = 2
MAX_SNIPPET_CHARS = 1500
MAX_LIVE_PASSAGES = 3

OFFICIAL_SCT_PROPOSAL = (
    "https://zdmk.krakow.pl/nasze-dzialania/"
    "strefa-czystego-transportu-miasto-proponuje-zmiany/"
)


@dataclass(frozen=True)
class LiveEvidenceResult:
    passages: tuple[EvidencePassage, ...] = ()
    attempted: int = 0
    usable: int = 0
    errors: tuple[str, ...] = ()


def official_entrypoints(claim: str) -> tuple[DocumentCandidate, ...]:
    """Reviewed starting links; never embed an assertion or model-made quote.

    This fallback is purposely narrow: a claim about a proposed reduction of
    Krakow's SCT. It is *not* a claim that a new zone is already in force.
    """
    lower = claim.casefold()
    if (
        ("krakow" in lower or "kraków" in lower or "krakowa" in lower)
        and aliases_for(lower)
        and re.search(r"zmniejsz|mniejsz|ogranicz|redukc|projekt|zapropon|propon", lower)
    ):
        return (DocumentCandidate(
            canonical_url=OFFICIAL_SCT_PROPOSAL,
            publisher="Zarząd Dróg Miasta Krakowa",
            headline="Strefa Czystego Transportu: miasto proponuje zmiany",
            published_at=None,
            provider="reviewed_official_entrypoint",
        ),)
    return ()


def _paragraphs(body: str) -> list[str]:
    parts = [" ".join(piece.split()) for piece in body.split("\n\n")]
    parts = [piece for piece in parts if len(piece) >= 20]
    # Joining only adjacent original passages preserves literal quote boundaries.
    blocks = []
    for i in range(len(parts)):
        block = "\n\n".join(parts[i:i + 3])
        if len(block) > MAX_SNIPPET_CHARS:
            block = "\n\n".join(parts[i:i + 2])
        if len(block) > MAX_SNIPPET_CHARS:
            block = parts[i][:MAX_SNIPPET_CHARS]
        if block and block not in blocks:
            blocks.append(block)
    return blocks


def _score(text: str, claim: str) -> int:
    """Rank claim-specific excerpts, without claiming they prove the assertion."""
    lower = text.casefold()
    words = set(re.findall(r"[\wąćęłńóśźż]{4,}", claim.casefold()))
    found = sum(word in lower for word in words)
    if aliases_for(claim) and ("sct" in lower or "strefa czystego transportu" in lower):
        found += 4
    if re.search(r"zapropon|propon|projekt", claim.casefold()) and re.search(
        r"propon|projekt|przygotowa", lower
    ):
        found += 4
    if re.search(r"zmniejsz|mniejsz|ogranicz", claim.casefold()) and re.search(
        r"zmniejsz|mniejsz|ogranicz", lower
    ):
        found += 4
    if "do tego czasu" in lower or "konsultacj" in lower:
        found += 1
    return found


def matching_live_passages(claim: str, passages: tuple[EvidencePassage, ...]):
    """Require a claim-specific connection before submitting a live excerpt."""
    selected = tuple(
        passage for passage in passages
        if isinstance(passage, LiveOfficialPassage) and claim in passage.selected_for
    )
    if selected:
        return selected[:MAX_LIVE_PASSAGES]
    # Older non-indexed source adapters retain the legacy topic-only boundary.
    return tuple(
        passage for passage in passages
        if (not isinstance(passage, LiveOfficialPassage) or not passage.selected_for)
        and _score(passage.exact_text, claim) >= 8
    )[:MAX_LIVE_PASSAGES]


@dataclass(frozen=True)
class LiveOfficialPassage(EvidencePassage):
    source_type: str = "primary"
    selected_for: tuple[str, ...] = ()
    start: int = 0
    end: int = 0


class LiveOfficialEvidence:
    """Fetch at most two direct civic pages per analysis, with no stored body."""

    def __init__(self, web: SafeWeb):
        self.web = web

    async def collect(
        self, candidates: tuple[DocumentCandidate, ...], claims: tuple[Claim, ...]
    ) -> LiveEvidenceResult:
        matched = []
        seen = set()
        for item in candidates:
            try:
                url = canonical_url(item.canonical_url)
            except ValueError:
                continue
            host = urlsplit(url).hostname
            if host not in MUNICIPAL_HOSTS or url in seen:
                continue
            if urlsplit(url).scheme != "https":
                continue
            seen.add(url)
            matched.append((url, item))
            if len(matched) == MAX_LIVE_PAGES:
                break

        excerpts: list[EvidencePassage] = []
        errors: list[str] = []
        usable = 0
        for url, item in matched:
            try:
                page = await self.web.read_article(url)
            except FetchError as exc:
                errors.append(exc.reason)
                continue
            # A cross-domain redirect does not inherit the reviewed host policy.
            if urlsplit(page.url).hostname not in MUNICIPAL_HOSTS:
                errors.append("redirect_outside_reviewed_scope")
                continue
            if page.media_type not in {"text/html", "application/xhtml+xml"}:
                errors.append("unsupported_content")
                continue
            article = extract_article(
                page.content, allow_text=True, robots=page.robots, source_url=page.url
            )
            if not article.body:
                errors.append("no_reusable_editorial_text")
                continue
            uid = sha256(page.url.encode()).hexdigest()[:16]
            selected_by_span: dict[tuple[int, int], LiveOfficialPassage] = {}
            for claim in claims[:10]:
                # Select from the FULL article before truncating to three. Each
                # picked excerpt is a literal, bounded source slice with offsets.
                picked, _diagnostic = select(article.body, claim.text)
                # Preserve the existing one-quote path when two complementary
                # editorial spans are adjacent within a short literal window.
                # Long pages still use separate exact quotes and group checks.
                if len(picked) >= 2:
                    left = min(part.start for part in picked)
                    right = max(part.end for part in picked)
                    if right - left <= 500:
                        joined = article.body[left:right]
                        # Only merge when every interior paragraph is editorial;
                        # this prevents navigation links becoming quote evidence.
                        if "zobacz także" not in joined.casefold():
                            picked = [Span(0, left, right, joined)]
                for span in picked[:MAX_LIVE_PASSAGES]:
                    key = (span.start, span.end)
                    prior = selected_by_span.get(key)
                    if prior is not None:
                        selected_by_span[key] = replace(
                            prior,
                            selected_for=tuple(dict.fromkeys(
                                (*prior.selected_for, claim.text)
                            )),
                        )
                        continue
                    selected_by_span[key] = LiveOfficialPassage(
                        passage_id=f"live:{uid}:{span.start}:{span.end}",
                        document_id=f"live:{uid}",
                        exact_text=span.text,
                        publisher="Zarząd Dróg Miasta Krakowa",
                        title=article.title or item.headline,
                        url=page.url,
                        published_at=article.published_at,
                        selected_for=(claim.text,),
                        start=span.start,
                        end=span.end,
                    )
            if not selected_by_span:
                errors.append("no_claim_relevant_excerpt")
                continue
            usable += 1
            excerpts.extend(selected_by_span.values())
        return LiveEvidenceResult(tuple(excerpts), len(matched), usable, tuple(errors))
