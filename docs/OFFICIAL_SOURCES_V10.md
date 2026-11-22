# PRAWDOWID V10 — official source URL patterns

The seed catalog has **40 supplied entry point URLs**. It represents **39 HTTP
resources** after normalizing a `#/...` hash route. `scripts/official_sources.json`
retains both original input URLs. The script `register_official_sources.py` adds
only **metadata**, never source licensing approvals or automatic document fetches.

## Verified patterns (public search samples, 2026-11-22)

| Site | Public URL pattern | How to handle |
|---|---|---|
| BIP Kraków | `/?dok_id=226666` | Public query identifier; preserve it |
| GIOŚ | `/pjp/current?lang=pl` | Query changes view/language, not secret |
| GIOŚ | `/pjp/content/show/1005904?lang=pl` | Numeric document ID in path |
| GIOŚ | `/pjp/content/api?theme=default` | API documentation; separate connector |
| GDDKiA | `/informacje-drogowe/lista-utrudnien?woj=mazowieckie` | Filtered live data, not article |
| GDDKiA | `/mapy/mapa-informacji-drogowej?situation=...` | Map state / encoded identifier |
| GDDKiA | `/mapy/mapa-informacji-drogowej?lat=...&lng=...` | Map viewport, not a document |
| ULC | `/prawa-pasazera/poradnik-dla-pasazerow?start=10` | Pagination |
| ULC | `/?Itemid=138` | CMS item identifier |
| ULC | `/_download/kopp/Prawa-pasazerow-ulotka-do-druku.pdf` | PDF; current text-only fetcher does not parse it |
| CERT Polska | `/komunikaty/2026/218/falszywe-informacje-.../` | Numeric IDs in paths; article |
| Gov.pl alerts | `#/_/mazowieckie/wszystkie` | Client-side route; fragment is not sent to server |

A public URL parameter is not a reason to reject an approved citation. Unknown
query parameter names can identify public documents. Access tokens, user/session
credentials, private/IP destinations and unsafe redirects remain disallowed.

## Install and metadata import

After the watcher has successfully deployed V10:

```bash
cd ~/projects/hacknation26-prawowid
uv run --locked python scripts/register_official_sources.py
uv run --locked python scripts/register_official_sources.py --apply
uv run --locked python scripts/news_status.py
```

The import is idempotent and does not downgrade pre-existing `ready`/`text_only`
documents, overwrite approvals or create fetch jobs. The host `www.gov.pl`
contains many agencies; the DB source table is host-granular, not a claim that
all content under that host has the same reuse policy.

To inspect unusual link shapes on the supplied sites, with robots enforcement,
private URL checks and **without importing page text**:

```bash
uv run --locked python scripts/audit_official_url_patterns.py
```

It creates `~/Downloads/prawowid_official_url_audit_YYYYMMDD_HHMMSS.zip`.
This is an independent address-shape audit, not the claim verification pipeline.
It does not render JavaScript or parse PDFs. Link metadata is not evidence.

## Evidence matching

V10 additionally recognizes `SCT` as `Strefa Czystego Transportu` and normalizes
common Polish date formats solely for conservative **quote anchor checking**.
It does **not** invent a citation if the model provides none. When none survives
validation, counts of rejection reasons are written to the application logs,
without original claim text or private URLs.

## Security

Source URLs must be reviewed for permitted reuse before enabling text ingestion.
`enabled=False` and `approved_at=NULL` remain the defaults for newly registered
hosts. JS app states and live map filters should get dedicated data adapters,
not be treated as ordinary article HTML.
