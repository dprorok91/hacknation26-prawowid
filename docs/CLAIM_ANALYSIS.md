# PRAWDOWID: claim-level analysis (MVP iteration)

## Principle

An analysis may contain several statements, but **each statement** receives its own
classification, evidence query, citation list, verdict and coverage information.
A claim being factual in principle does **not** imply that the available sources
can verify it. Do not convert general biological knowledge into proof of a specific
private assertion.

## Internal stages

1. **Preserve exact input spans.** The model extracts original substrings. A
   conservative sentence fallback is used when a small model calls a complete
   declarative sentence a `non_claim`; it never auto-confirms anything.
2. **Scope before searching.** Public factual claims are eligible for optional
   external discovery. Personal assertions (e.g. `Moja krowa daje mleko.`) are
   considered factual but require evidence about that individual case. They do
   not trigger public news discovery.
3. **Retrieve per claim.** Local retrieval uses the specific claim text, with
   curated aliases such as `SCT` ↔ `strefa czystego transportu` for recall.
   Proposal, implementation and referendum are separate actions; the event
   filter remains intentionally conservative.
4. **Bounded discovery.** When explicitly enabled for text, the worker runs
   searches for up to three public claims per analysis. The report says which
   statements were searched, skipped, or omitted by the budget. The latest
   Google News/GDELT adapters and cache are reused; this change creates no
   new news-provider contract or database migration.
5. **Evidence-bound verdict.** Only exact quotes from permitted, indexed
   content can support or refute a claim. Discovery headlines and links are
   metadata, **never** proof. Prior fact-checks are valuable supporting
   sources if genuinely matched to the same assertion, but their labels are
   not automatically copied as PRAWDOWID's verdicts.
6. **Two report layers.** A statement-level view shows colored source spans,
   an accessible hover/focus explanation and a detailed claim card. A separate
   coverage view counts publishers with citable evidence and publishers
   returned for the broader topic, **without combining them** or calculating
   ungrounded political-slant percentages.

## Examples / acceptance cases

| Input | Result without source text |
| --- | --- |
| `Władze Krakowa zaproponowały zmniejszenie SCT.` | Factual, public, search eligible; insufficient evidence |
| `Moja krowa daje mleko.` | Factual, private scope; insufficient evidence even if general cow facts are retrieved |
| `Krowy dają mleko.` | General factual claim, public research possible; insufficient evidence until an applicable source is cited |
| `SCT to fatalny pomysł.` | Opinion; no factual verdict forced |
| `Strefa czystego transportu` | Topic/fragment, not an independent assertion |
| Two unrelated sentences | Independent retrieval/discovery; no cross-claim event merging |

## Source types and fact-checkers

Source types for citable, publisher-approved documents already include `news`,
`primary` and `factcheck`. Metadata from Demagog, Konkret24 or AFP Sprawdzam
is labeled as a fact-check **candidate**, not as verified support. The system
will not scrape these sites or treat their displayed ratings as ground truth.
A dedicated Google Fact Check Claim Search integration is a possible later
adapter, subject to API terms, limits, rights and exact-claim alignment.

## Privacy, integrity and limitations

- The external-news checkbox is **unchecked by default** and visibly rendered.
- The source text for text submissions is carried inside the private short-lived
  report (first 5,000 characters for annotations). It is not presented as a
  public report; the existing analysis ID and retention controls still apply.
- When no publisher is approved, the result may show discovered links but **no
  citable verdict**. This is an honest, expected limitation.
- We do **not** infer a claim's truth from publisher reputation, ideological
  profile, the number of articles found, or a topical headline.
- No universal Polish semantic normalizer is claimed. The SCT alias and action
  guards are an intentionally small, deterministic starting point.
- The colored highlights and coverage layout need visual/browser validation
  on Ubuntu beyond the syntax and static regression tests.
- The previous live-search patch already persisted `web_search` through
  PostgreSQL jobs. This iteration fixes the invisible checkbox and provides
  per-claim search diagnostics; it does not replace the job store.

## Local verification

```bash
uv run --locked python scripts/sync_claim_contracts.py
uv run --locked ruff check .
uv run --locked pytest -q
```

For actual PostgreSQL integration tests, set `TEST_DATABASE_URL` to a **separate
migrated disposable database**. Restart both API and worker after installing;
refresh Brave with Ctrl+Shift+R. Submit a public factual statement with the
news-search checkbox enabled. The report must display the attempt status and
sources discovered; without approved article passages it must abstain.
