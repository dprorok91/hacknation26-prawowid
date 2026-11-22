# Public-source live search (local MVP)

**Privacy:** Text submissions remain local by default. The web checkbox sets
`web_search: true` in `POST /api/v1/analyses`; the option persists only in the
analysis job payload. The external providers receive a search query generated
from the submitted text, so only use this option for public information.

**Discovery:** The worker already includes Google News RSS and GDELT discovery.
These services provide links and metadata, not verified evidence. Search hits
are shown in a **separate, clearly labeled, non-evidence list**; the links and
metadata are also saved in the report JSON. The worker now
attempts discovery even when the corpus is empty. Downloading article text is
restricted to domains explicitly approved in `sources` and subject to robots,
license, SSRF, and extraction checks. No discovered headline/snippet is used as
a citation.

**Before a live demo:**

1. Inspect the rights/terms for a specific publisher and approve it with the
   repository's `scripts/catalog.py approve` command. Do **not** approve an
   entire domain based only on a search result. For Gov.pl text, review
   https://www.gov.pl/web/gov/prawa-autorskie and any page-specific exceptions.
2. Use the public-text checkbox, or submit an approved public article URL.
3. Run the API, worker, PostgreSQL and Ollama, then inspect the source links
   and literal quoted spans in the resulting report.
4. Expect `insufficient_evidence` when eligible sources cannot be fetched or
   no supplied passage supports the claim. An empty framing panel is expected
   with fewer than two independent matched sources.

**Important limitation:** This patch does not continuously crawl the web, and
it does not make unknown news publishers automatically reusable. The corpus
checker may still show zero ready documents until eligible articles are fetched.

**Local timing:** the worker defaults to a 28-second analysis deadline; a
small laptop running a local model may need `ANALYSIS_DEADLINE_SECONDS=90` in
`.env`, followed by a worker restart. That is a local demonstration setting,
not proof of the target p50 latency <=30 seconds.

**Deterministic source warm-up:** after a publisher is explicitly approved,
run `uv run --locked python scripts/queue_approved_urls.py 'https://publisher.example/approved-public-article'`.
This inserts durable `FETCH_URL` jobs using the existing queue; the worker
fetches, validates, extracts and indexes article paragraphs as permitted.
Re-run `scripts/check_corpus_ready.py` and submit the associated claim again.
