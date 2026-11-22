"""Explicit editorial provenance labels; never infer truth from publisher category.

Two report flags are `source_category` and `source_role`. Roles organize
presentation, not evidence eligibility or automatic claim verdicts.
Classifications rely on an explicit domain registry; Google News publisher
labels are a weaker, annotated fallback for RSS metadata only.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

SOURCE_CATEGORIES = frozenset(("official", "major_media", "fact_check", "other_media"))
SOURCE_ROLES = frozenset(("core", "perspective"))

# Exact known government/public-service domains and subdomains. This list is
# intentionally configurable in code, not a blanket ".pl" assumption.
OFFICIAL_DOMAINS = frozenset({
    "gov.pl", "odyseusz.gov.pl", "policja.pl", "mazowiecka.policja.gov.pl",
    "monitoring.paa.gov.pl", "powietrze.gios.gov.pl", "moje.cert.pl",
    "drogi.gddkia.gov.pl", "ulc.gov.pl", "portalpasazera.pl", "utk.gov.pl",
    "kgsok.pl", "sar.gov.pl", "umgdy.gov.pl", "ums.gov.pl",
    "meteo.imgw.pl", "hydro.imgw.pl", "baltyk.imgw.pl",
    "krakow.pl", "bip.krakow.pl", "ztp.krakow.pl", "zdmk.krakow.pl",
    "edziennik.malopolska.uw.gov.pl", "isap.sejm.gov.pl",
})
FACT_CHECK_DOMAINS = frozenset({
    "demagog.org.pl", "konkret24.tvn24.pl", "sprawdzam.afp.com",
    "fakehunter.pap.pl", "fakenews.pl",
})
MAJOR_MEDIA_DOMAINS = frozenset({
    "pap.pl", "tvn24.pl", "polsatnews.pl", "tvp.info", "onet.pl",
    "wiadomosci.wp.pl", "wp.pl", "interia.pl", "rmf24.pl", "radiozet.pl",
    "wyborcza.pl", "rp.pl", "dziennik.pl", "money.pl", "businessinsider.com.pl",
    "gazetaprawna.pl", "forsal.pl",
})

# Only for Google News metadata, never for content retrieved from arbitrary
# sites (publisher names in downloaded HTML can be forged).
TRUSTED_RSS_PUBLISHERS = {
    "pap": "major_media", "polska agencja prasowa": "major_media",
    "tvn24": "major_media", "polsat news": "major_media",
    "onet": "major_media", "wp wiadomości": "major_media",
    "interia": "major_media", "rmf24": "major_media",
    "gazeta wyborcza": "major_media", "rzeczpospolita": "major_media",
    "demagog": "fact_check", "konkret24": "fact_check",
    "afp sprawdzam": "fact_check", "fakehunter": "fact_check",
}


@dataclass(frozen=True)
class SourceFlags:
    source_category: str
    source_role: str
    classification_basis: str = "domain"


def _matches(host: str, domains: frozenset[str]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def classify_source(
    url: str, *, publisher: str = "", provider: str = "", metadata_only: bool = False
) -> SourceFlags:
    """Mark provenance, not reliability. Unclassified media remain supplemental.

    On Google News RSS, the URL may point to a Google redirect instead of
    the newspaper. In that *metadata-only* case, a known outlet's display
    name can provide a provisional label, never a verified source identity.
    """
    try:
        host = (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        host = ""
    if _matches(host, FACT_CHECK_DOMAINS):
        return SourceFlags("fact_check", "core")
    if _matches(host, OFFICIAL_DOMAINS):
        return SourceFlags("official", "core")
    if _matches(host, MAJOR_MEDIA_DOMAINS):
        return SourceFlags("major_media", "core")
    if metadata_only and host in ("news.google.com", "google.com") and (
        "google" in provider.casefold() or provider == ""
    ):
        known = TRUSTED_RSS_PUBLISHERS.get(publisher.strip().casefold())
        if known:
            return SourceFlags(known, "core", "rss_publisher_unverified")
    return SourceFlags("other_media", "perspective", "unclassified")


def legacy_source_type(flags: SourceFlags) -> str:
    """Map to the existing DB CHECK constraint, without a schema migration."""
    return {
        "official": "primary", "fact_check": "factcheck",
        "major_media": "news", "other_media": "news",
    }[flags.source_category]
