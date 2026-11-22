# V28 — context-aware local retrieval and guarded calendar arithmetic

- Contextual source names come only from an adjacent previous claim in the same paragraph when the current claim uses an anaphoric reference (`tego dnia`, `datą obchodów`). The original claim remains unchanged in the report.
- Local retrieval performs a bounded second query with this source name and reserves result slots for evidence from the contextual query. No new web search is triggered.
- Only narrowly curated Polish inflectional equivalents are used in the post-retrieval and citation relevance checks: `obchod…`, `zainicjow…`, and `dzień/dnia`. This is not an entailment classifier.
- A deterministic check may compare a claimed celebration date with an explicitly quoted ordinal weekday rule. It does not assert identity or fabricate a quote, and does not infer publication dates. The LLM still decides support/refutation using a verbatim citation.
- F1 publication-date abstention remains intentional because indexed document `published_at` is missing.
- Regression tests must run with Ruff and full pytest in a copy of the user’s repository before any watcher ZIP is emitted.
