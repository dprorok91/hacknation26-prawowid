"""Report source flags and non-interference with fact-check results."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from prawdowid.api.schemas import DiscoveredLink, SourceReference
from prawdowid.application.pipeline import _reference
from prawdowid.domain.source_flags import classify_source, legacy_source_type


@pytest.mark.parametrize(
    ("url", "category", "role"),
    [
        ("https://www.gov.pl/web/rcb/raport-dobowy3", "official", "core"),
        ("https://www.bip.krakow.pl/?dok_id=226666", "official", "core"),
        ("https://powietrze.gios.gov.pl/pjp/", "official", "core"),
        ("https://policja.pl/pol/aktualnosci", "official", "core"),
        ("https://tvn24.pl/polska", "major_media", "core"),
        ("https://pap.pl/aktualnosci", "major_media", "core"),
        ("https://demagog.org.pl/wypowiedzi/abc", "fact_check", "core"),
        ("https://konkret24.tvn24.pl/artykul/abc", "fact_check", "core"),
        ("https://lokalna-redakcja.pl/newsy/123", "other_media", "perspective"),
        ("https://gov.pl.bad-domain.pl/report", "other_media", "perspective"),
        ("https://demagog.org.pl.bad-domain.pl/report", "other_media", "perspective"),
        ("https://example.com/?target=gov.pl", "other_media", "perspective"),
    ],
)
def test_source_categories_are_based_on_accurate_domains(url, category, role):
    flags = classify_source(url)
    assert flags.source_category == category
    assert flags.source_role == role


def test_google_news_publisher_provisional_label_not_trusted_on_other_hosts():
    google = classify_source(
        "https://news.google.com/rss/articles/abc", publisher="TVN24",
        provider="google_news", metadata_only=True,
    )
    assert (google.source_category, google.classification_basis) == (
        "major_media", "rss_publisher_unverified"
    )
    spoof = classify_source(
        "https://attacker.example/report", publisher="TVN24",
        provider="google_news", metadata_only=True,
    )
    assert spoof.source_category == "other_media"


def test_legacy_database_source_type_preserves_existing_three_type_schema():
    assert legacy_source_type(classify_source("https://gov.pl")) == "primary"
    assert legacy_source_type(classify_source("https://demagog.org.pl")) == "factcheck"
    assert legacy_source_type(classify_source("https://tvn24.pl")) == "news"
    assert legacy_source_type(classify_source("https://blog.pl")) == "news"


def test_cited_source_reference_carries_both_flags():
    passage = SimpleNamespace(
        passage_id="p1", publisher="BIP Kraków", title="SCT", published_at=None,
        fetched_at=datetime.now(UTC), url="https://www.bip.krakow.pl/?dok_id=226666",
        source_type="primary",
    )
    refs = {}
    source_id = _reference(refs, passage, "SCT obowiązuje od 01.01.2026 r.", "supports")
    assert refs[source_id]["source_category"] == "official"
    assert refs[source_id]["source_role"] == "core"
    assert SourceReference.model_validate(refs[source_id]).source_category == "official"


def test_backward_compatible_report_defaults_for_old_saved_results():
    old = DiscoveredLink(title="Artykuł", publisher="Lokalna", url="https://local.pl",
                         provider="google_news")
    assert (old.source_category, old.source_role) == ("other_media", "perspective")
