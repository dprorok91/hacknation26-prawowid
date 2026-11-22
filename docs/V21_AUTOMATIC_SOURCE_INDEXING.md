# V21: automatic approved-source ingestion

PRAWDOWID records the **40 supplied official URLs** as metadata automatically
when the worker starts, and every hour afterward. This does not authorize full
text reuse. Metadata is not evidence.

The existing worker runs a bounded indexing cycle every hour, starting
immediately on startup:

1. Preserve all pre-existing publisher policy fields. New hosts are disabled.
2. Find up to 40 metadata-only or expired documents belonging to **enabled,
   explicitly approved, licensed** publisher domains; queue `FETCH_URL` jobs.
3. Rotate through up to eight approved HTML listing pages, discovering and
   queueing up to 40 same-host article/PDF links per cycle. SafeWeb enforces
   robots, DNS/SSRF, redirect, media type and response size restrictions.
4. The fetch worker ingests text into the existing passages table; HTML/PDF
   remains metadata-only if unsupported or not permitted. The next cycle
   resumes indexing; no external cron, manual `--apply` or API secrets required.

The selection includes all approved publisher categories, not just official
sources. Current source approval requirements remain in effect. JS applications,
interactive maps and dynamic data portals cannot be automatically indexed as
static news articles; they need dedicated adapters. Robots restrictions and
unavailable pages are not bypassed.

**Diagnose all supplied sources**:

```bash
uv run --locked python scripts/source_index_status.py
uv run --locked python scripts/news_status.py
journalctl --user -u prawdowid-worker.service -n 50 --no-pager -l
```

`POLICY_NOT_APPROVED` means the source URL has been registered but its domain has
no enabled reuse approval. `APPROVED_WAITING_FOR_TEXT` means the domain is
approved but the database has no indexed unexpired text for it.
`NEEDS_SPECIAL_ADAPTER` identifies interactive sources that require their own
source-specific integration. `INDEXED` only indicates that a domain has **at
least one** indexed document; it is not proof every individual URL was indexed.

Approval is configured separately via `scripts/catalog.py approve` after
reviewing the relevant policy. Do not assume a user-provided link has granted
rights or that any link is itself evidence. The report only cites actual
retrieved, license-compliant passages.

Both rejected V19 and V20 are bundled as **source changes based on the last
confirmed working V18**, with the V19 Ruff defect corrected. V21 does not
require either rejected patch.
