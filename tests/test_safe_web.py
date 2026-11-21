import httpx
import pytest

from prawdowid.infrastructure.safe_web import FetchError, SafeWeb, extract_article


async def public_ip(host, port):
    return ("8.8.8.8",)


@pytest.mark.asyncio
@pytest.mark.parametrize("answers", [("127.0.0.1",), ("8.8.8.8", "192.168.1.2"), ("fc00::1",)])
async def test_dns_must_return_only_public_addresses(answers):
    async def resolve(host, port):
        return answers

    def unexpected(request):
        pytest.fail("Unsafe DNS result reached HTTP transport")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(FetchError):
            await SafeWeb(client, resolve=resolve).read("https://news.example/story")


@pytest.mark.asyncio
async def test_publishers_sharing_a_pinned_ip_never_share_cookies():
    def respond(request):
        assert "Cookie" not in request.headers
        return httpx.Response(
            200, text="body", headers={"Content-Type": "text/plain", "Set-Cookie": "id=fixture"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        web = SafeWeb(client, resolve=public_ip)
        await web.read("https://publisher-one.example/story")
        await web.read("https://publisher-two.example/story")


@pytest.mark.asyncio
async def test_pinned_address_and_redirect_revalidation():
    def redirect(request):
        assert request.url.host == "8.8.8.8"
        assert request.headers["Host"] == "news.example"
        assert request.extensions["sni_hostname"] == "news.example"
        return httpx.Response(302, headers={"Location": "http://127.0.0.1/blocked"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(redirect)) as client:
        with pytest.raises(FetchError):
            await SafeWeb(client, resolve=public_ip).read("https://news.example/story")


@pytest.mark.asyncio
async def test_robots_and_response_budget():
    requests = []

    def respond(request):
        requests.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/plain"},
                text="User-agent: *\nDisallow: /private",
            )
        return httpx.Response(200, headers={"Content-Type": "text/html"}, content=b"x" * 100)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        web = SafeWeb(client, resolve=public_ip, max_bytes=60)
        with pytest.raises(FetchError):
            await web.read_article("https://news.example/private")
        assert requests == ["/robots.txt"]
        with pytest.raises(FetchError):
            await web.read("https://news.example/large")


def test_extracts_body_only_when_policy_allows():
    raw = b"<title>News</title><article><script>no</script><p>Exact paragraph.</p></article>"
    assert not extract_article(raw, allow_text=False).body
    assert extract_article(raw, allow_text=True).body == "Exact paragraph."
    assert (
        extract_article(
            raw.replace(b"Exact paragraph.", b"Exact&nbsp;paragraph."), allow_text=True
        ).body
        == "Exact paragraph."
    )
    assert not extract_article(raw, allow_text=True, robots="noarchive").body
    assert not extract_article(
        raw.replace(b"<article>", b"<article class=paywall>"), allow_text=True
    ).body
