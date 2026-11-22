# V20 — fresh URL analysis and article-body selection

- URL analysis cache now uses `PIPELINE_VERSION=v20`, so an old, completed
  V18/V19 report is never returned as a result of a newly submitted URL.
- The bounded URL fetch queue also uses a new parser revision to avoid replaying
  an old, teaser-only text extraction.
- Web index requests are marked `Cache-Control: no-store` and point to
  `/assets/app.js?v=20` so browsers do not keep the V18 JavaScript after deploy.
- When a short `<article>` card competes with full editorial material in a
  different container, the parser favors the substantive editorial body.
- Metadata-only web discovery is **not** evidence. If official-source text is
  not approved, indexed and retrievable, the report must still abstain. No
  automatic domain license approval or bypass of robots/paywalls is introduced.
- To confirm deployment, check the page source for `app.js?v=20`, and rerun a
  URL submission; it must get a new analysis ID rather than reuse an old one.

Regression: `tests/test_v20_cache_and_editorial.py` plus the full repo suite.
