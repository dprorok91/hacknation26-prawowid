# V19 — real article coverage and truthful completeness

- Prefer the substantive HTML article body over the first teaser card; retain paragraphs and list items, while excluding navigation and paywalls.
- Extract up to 10 independently assessed statements from two batches; when an LLM under-selects, use verbatim sentence/list spans as *candidates* for semantic assessment, never as verified facts.
- Expose `coverage.input_chars`, `coverage.input_segments`, `coverage.extracted_claims`, `coverage.cited_claims`, and `coverage.partial_analysis` in the report and UI.
- A discovery URL or headline is not evidence. `cited_claims` requires validated quotation references.
- The source's licensing, robots, and fetch restrictions remain in effect; inaccessible articles still require user-provided text.
- Segment counts are heuristic diagnostics (not guaranteed complete claim counts). Budget remains at most two extraction batches and three public searches.

- User-submitted URLs use a versioned fetch deduplication key for a one-time refresh after the HTML parser upgrade; crawler/indexer default behavior remains unchanged.
