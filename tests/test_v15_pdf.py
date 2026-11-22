"""PDF extraction fails closed when conversion is not allowed or unavailable."""

import pytest

from prawdowid.infrastructure.pdf_text import pdf_text


@pytest.mark.asyncio
async def test_pdf_blocks_non_pdf_and_robot_protected_content():
    assert await pdf_text(b"not-a-pdf") is None
    assert await pdf_text(b"%PDF-1.4\nexample", robots="nosnippet") is None
    assert await pdf_text(b"%PDF-1.4\nexample", robots="noarchive") is None
    assert await pdf_text(b"%PDF-1.4\nexample", robots="nosnippet, index") is None


@pytest.mark.asyncio
async def test_bounded_pdf_refuses_oversized_input():
    assert await pdf_text(b"%PDF-" + b"a" * (2 * 1024 * 1024)) is None


@pytest.mark.asyncio
async def test_pdf_extracts_actual_converter_output_when_tool_is_available(monkeypatch):
    from prawdowid.infrastructure import pdf_text as extractor

    class Converter:
        returncode = 0

        async def communicate(self, raw=None):
            assert raw == b"%PDF-1.5\npublic-fixture"
            return ("Raport publiczny. " * 25).encode(), b""

    async def fake_converter(*args, **kwargs):
        assert args[0] == "pdftotext"
        assert args[-2:] == ("-", "-")
        return Converter()

    monkeypatch.setattr(extractor.shutil, "which", lambda _: "/usr/bin/pdftotext")
    monkeypatch.setattr(extractor.asyncio, "create_subprocess_exec", fake_converter)
    result = await extractor.pdf_text(b"%PDF-1.5\npublic-fixture")
    assert "Raport publiczny" in result


@pytest.mark.asyncio
async def test_pdf_http_response_only_allowed_through_article_fetch():
    import httpx

    from prawdowid.infrastructure.safe_web import FetchError, SafeWeb

    async def public_dns(_host, _port):
        return ("8.8.8.8",)

    def respond(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200, headers={"Content-Type": "text/plain"}, text="User-agent: *\nAllow: /"
            )
        return httpx.Response(
            200, headers={"Content-Type": "application/pdf"}, content=b"%PDF-1.5\nexample"
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        web = SafeWeb(client, resolve=public_dns)
        page = await web.read_article("https://example.org/aktualnosci/report.pdf")
        assert page.media_type == "application/pdf"
        assert page.content.startswith(b"%PDF-")
        with pytest.raises(FetchError, match="unsupported_content"):
            await web.read("https://example.org/aktualnosci/report.pdf")
