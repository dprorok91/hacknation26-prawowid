"""Document restrictions must override even an approved host."""

import pytest

from prawdowid.infrastructure.pdf_text import pdf_text
from prawdowid.infrastructure.safe_web import extract_article

HTML = b"<html><title>Note</title><main><p>Important public information.</p></main></html>"


def test_noindex_header_overrides_grant():
    article = extract_article(HTML, allow_text=True, robots="noindex")
    assert article.body is None


def test_unrestricted_approved_page_can_be_extracted():
    article = extract_article(HTML, allow_text=True)
    assert article.body == "Important public information."


@pytest.mark.asyncio
async def test_pdf_noindex_header_overrides_grant():
    assert await pdf_text(b"%PDF-1.7\nplaceholder", robots="noindex") is None
