from datetime import UTC, datetime

from prawdowid.application.narratives import compare_narratives
from prawdowid.domain.models import EventQuery, EvidencePassage


def hit(identifier, publisher, text):
    return EvidencePassage(
        identifier,
        identifier,
        text,
        publisher,
        publisher,
        f"https://{identifier}.example/news",
        datetime(2026, 1, 1, tzinfo=UTC),
    )


def test_compares_two_matched_sources_with_different_factual_emphasis():
    air = hit("air", "Wydawca A", "Strefa ograniczyła poziom zanieczyszczeń w centrum.")
    mobility = hit("cars", "Wydawca B", "Strefa ograniczyła wjazd starszych samochodów.")
    result = compare_narratives(EventQuery("Strefa ograniczyła ruch"), [air, mobility])
    assert len(result) == 2
    assert {summary.quote for summary in result} == {air.exact_text, mobility.exact_text}
    assert len({summary.event_id for summary in result}) == 1


def test_syndicated_copy_is_not_independent_comparison():
    text = "Strefa ograniczyła poziom zanieczyszczeń w centrum miasta."
    assert not compare_narratives(
        EventQuery("Strefa ograniczyła ruch"),
        [hit("first", "Wydawca A", text), hit("copy", "Wydawca B", text)],
    )
