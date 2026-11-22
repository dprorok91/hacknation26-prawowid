# V25: policy-gated automatic indexing for www.gov.pl

At worker startup and each hourly source-indexing cycle, the application
registers metadata and conditionally approves the exact `www.gov.pl` host.
This is an explicit PRAWDOWID project decision backed by the Polish
public-sector-information reuse regime. The source record stores the official
reuse-policy URL and a descriptive licensing label for provenance.

The grant is limited to `www.gov.pl` (not `*.gov.pl`, `gov.pl`, or other state
portals). Existing manual source policies, disabled licensed sources, robots
exclusions and page-level `noarchive`, `nosnippet`, `noindex` opt-outs are
preserved. Approval does not authorize reuse of third-party copyrighted
materials, sensitive personal information or a document with restrictive terms.
Operators must review such documents and remove them from the corpus.

This approval makes catalogued and discovered `www.gov.pl` resources eligible
for the V24 hourly indexing queue; it **does not** guarantee every interactive
page or PDF is extractable or that an official source is factually correct.
The job queue continues to obey its per-cycle bounds and follows no external
or unapproved host links.

After V25 deployment, use:

```bash
uv run --locked python scripts/source_index_status.py
uv run --locked python scripts/news_status.py
journalctl --user -u prawdowid-worker.service -n 80 --no-pager -l
```

Expected: `www.gov.pl` entries should no longer be `POLICY_NOT_APPROVED`.
They may still show `APPROVED_WAITING_FOR_TEXT` or `NEEDS_SPECIAL_ADAPTER`.
Citable passages should appear only once fetch jobs complete, and `news_status`
should then show more `official` documents with text. No other publisher is
automatically approved.

Legal overview: https://www.gov.pl/web/gov/wykorzystaj-informacje-sektora-publicznego2
