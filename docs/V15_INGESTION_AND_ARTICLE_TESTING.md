# V15: approved-source indexing and multi-claim article regressions

## What this release changes

- `scripts/discover_approved_articles.py` explores up to 8 approved listing pages
  (configurable, max 30), follows bounded pagination, finds HTML article/PDF
  links under the same approved domain, and optionally queues up to 25. On
  dry run it performs bounded, robots-checked reads but never modifies DB.
- `scripts/index_approved_sources.py` previews and optionally enqueues up to 20
  (configurable up to 100) **already known metadata URLs** belonging to approved
  publishers. Defaults to `official`. No additional license grants, web requests,
  or hidden always-on crawler. Existing worker handles retries/deduplication,
  DNS/SSRF, robots, redirects, and license controls.
- `src/prawdowid/infrastructure/pdf_text.py` extracts text from the first 10
  pages of PDFs only if system `pdftotext` (poppler-utils) is available, the
  downloaded PDF is within the configured bytes budget, robots allow text,
  and the final domain is approved. No OCR, so scanned documents stay metadata-only.
- Multi-claim article regression: `scripts/test_article_source_flags.py` submits
  four statements together. Defaults to local corpus; `--online` is explicit
  and may incur LLM/search fees. `--text-file` accepts a UTF-8 article excerpt.
- Report `coverage.evidence_by_category` displays four provenance groups,
  including empty groups. The UI attaches visible category+role tags to each
  cited passage, not only to discovery links. Official/major/fact-check labels
  are origin flags, not an indicator of truthfulness.

## Deploy and run

After the watcher reports `WDROŻONO` for v15:

```bash
cd ~/projects/hacknation26-prawowid
uv run --locked python scripts/index_approved_sources.py --category official
uv run --locked python scripts/index_approved_sources.py --category official --apply
uv run --locked python scripts/discover_approved_articles.py
# Only after reviewing which approved URLs it discovered:
uv run --locked python scripts/discover_approved_articles.py --apply
uv run --locked python scripts/news_status.py
uv run --locked python scripts/test_article_source_flags.py
```

On Ubuntu, install `poppler-utils` if you need public PDF text extraction
(optional; the code degrades to metadata-only otherwise).

## Honest limitations

1. Only **approved sources** can produce indexed passages or authentic citations.
   Four-category coverage cannot be guaranteed if approved content in a category
   does not exist. The report explicitly marks categories lacking citations.
2. This release queues **known metadata URLs** and optionally discovers
   article/PDF links from *already approved* public HTML listing pages.
   It is not a general web crawler, does not render JS, and does not grant
   approval to new domains. Sites with maps still need dedicated adapters.
3. The existing claim extraction maximum is 5. The public online-search budget
   remains at most 3 claim searches per analysis.
4. Each category reflects the publisher domain, not an independent statement
   about the accuracy of the quotation or completeness of event coverage.
5. PDFs that lack embedded text are not OCR'd; any unsupported PDF stays
   `metadata_only` rather than fabricating citations.
