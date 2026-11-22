"""Approved-site-only article, PDF, and pagination candidate parsing."""

from prawdowid.application.source_discovery import discover_links


def test_links_find_articles_pdfs_and_pagination_on_same_host_only():
    html = '''<html><body><main>
      <a href="/aktualnosci/2026/komunikat-rcb">Wazny komunikat z dnia 22 listopada 2026</a>
      <a href="/materialy/report.pdf">Pobierz pelny raport urzedowy za 2026 rok</a>
      <a href="?page=2" rel="next">Następna</a>
      <a href="/aktualnosci/2026/komunikat-rcb">Zduplikowany komunikat nr 1</a>
      <a href="https://unknown.example/news/1">Obca domena i jej artykul</a>
      <a href="http://127.0.0.1/admin">Niebezpieczny adres panelu</a>
      <a href="/raport?id=1&amp;access_token=ABC">Prywatny dokument urzedowy</a>
      <a href="/galeria/a.jpg">Zobacz zdjecie z wydarzenia</a>
      <nav><a href="/aktualnosci/2026/nawigacja">Zawartosc menu nie jest artykulem</a></nav>
    </main></body></html>'''.encode()
    links = discover_links(html, "https://www.gov.pl/web/rcb/aktualnosci")
    urls = [row.canonical_url for row in links.articles]
    assert len(urls) == 2
    assert "https://www.gov.pl/aktualnosci/2026/komunikat-rcb" in urls
    assert "https://www.gov.pl/materialy/report.pdf" in urls
    assert links.next_pages == ("https://www.gov.pl/web/rcb/aktualnosci?page=2",)
    assert all("access_token" not in url for url in urls)


def test_listing_honors_nofollow_meta_and_bounds():
    html = b'''<html><head><meta name="robots" content="index,nofollow"></head>
    <body><a href="/a/notice">A very long article title</a></body></html>'''
    assert not discover_links(html, "https://gov.pl/start").articles
    assert not discover_links(html, "https://gov.pl/start").next_pages


def test_query_document_is_not_excluded_due_to_parameter_name():
    html = b'''<a href="/?dok_id=226666">SCT Krakow publiczny dokument urzedowy</a>
    <a href="/komunikat?ida[4]=112">Komunikat Policji o wydarzeniu</a>'''
    result = discover_links(html, "https://www.bip.krakow.pl/")
    assert len(result.articles) == 2
    assert any("dok_id=226666" in row.canonical_url for row in result.articles)
    assert any("ida%5B4%5D=112" in row.canonical_url for row in result.articles)
