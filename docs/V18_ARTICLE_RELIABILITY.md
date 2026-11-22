# V18 — article analysis reliability

- In URL mode a blocked/unavailable publisher is never scraped through an unsafe
  bypass. The interface offers an explicit **paste article text** action after
  `source_unavailable`; the user supplies the material and separately opts into
  web search. Text submissions retain the existing short retention policy.
- The default analysis deadline rises from 28 to **180 seconds**, matching
  bounded multi-claim extraction and externally searched articles. A previously
  set `ANALYSIS_DEADLINE_SECONDS` environment override still wins; restart the
  worker after the patch. The browser poll horizon rises to 240 seconds.
- Six or more sentences are split into at most **two** exact-text batches. Each
  extraction returns at most five selected propositions; at most **ten** claims
  are included. The result notes the limit. This is *not* unlimited article
  coverage, and not a guarantee that every factual proposition was found.
- Broad universal claims cause a second **local** evidence query requesting
  counter-context. This can improve recall, but does not determine the verdict.
  All verdict citations must still match literal retrieved source passages.
- Tracking-only empty `utm` query parameters are stripped in canonical source
  URLs, and identical citations from the same canonical URL are deduplicated.
- The retrieval merger now accepts both dataclass-backed search results and plain
  test doubles. This fixes the V17 pytest failure detected by the watcher.
- The worker logs bounded structural OpenRouter failure categories, HTTP status
  codes, and schema class names, never model responses or user-submitted text.
  Existing public error codes are unchanged. Some `model_unavailable` cases may
  still need a new run to identify their exact cause.
- Online discovery remains globally bounded at **three** queries per analysis;
  unsearched claims are explicitly marked. The deadline is a safeguard, not an
  SLA. `--timeout` on a client only controls polling, not worker execution.

## After deployment

Run `uv run --locked ruff check .` and `uv run --locked pytest -q` (these are
already required by the builder and patch watcher). To test articles whose
publishers block fetching, paste only text you have the right to process into a
local file (do not upload copyright-restricted content to third parties without
permission) and run:

```bash
uv run --locked python scripts/test_article_source_flags.py \
    --text-file ~/Downloads/article.txt --online --timeout 240
```

Check `journalctl --user -u prawdowid-worker.service -n 80 --no-pager -l` for
redacted provider failure categories and the `/api/v1/analyses/<id>/audit`
endpoint for totals. Tests do not establish the factual correctness of verdicts.
