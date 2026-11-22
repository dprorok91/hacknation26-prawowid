# V27 — extraction resilience and source-evidence discipline

V27 is an incremental patch on top of **successfully deployed V26**.

## Behavior changes

- Bound extraction to five proposals per request, even when the provider's
  reduced strict schema does not enforce array length. The existing two-batch
  analysis cap remains ten. Partial coverage is still disclosed.
- On a Pydantic schema error, retry the structured model call once with a
  concise schema-reminder field. Rate limits, timeouts and model-transport
  failures are **not** retried here. Log safe validation error *types* only;
  never log source content, text or raw model responses.
- A citation for a substantive claim must share at least half of its lexical
  subject/predicate anchors, retaining verified SCT acronym-plus-date aliases.
  Supporting quotes must contain the numbers/dates asserted by the claim.
  This is a **relevance safeguard**, not independent textual entailment/NLI.
- Identical quotations from different URLs on the same hostname count as
  one cited excerpt per claim, not multiple independent confirmations.
  Display `Portal Gov.pl` for citations on this shared government hostname,
  rather than assigning an unrelated ministry's source-record display_name.
- Approve legacy `www.gov.pl` disabled *metadata-only* registrations matching
  the exact known label+policy URL from earlier versions; preserve other
  manual and third-party policies.

## Boundaries and unresolved issues

- These safeguards can yield more `insufficient_evidence` verdicts. They do
  not solve retrieval recall for historical details such as “1993”.
- Postgres and live OpenRouter checks require the real deployment environment.
- Accuracy on the ten-claim adversarial article must be tested again after
  deployment; no new accuracy result is claimed by offline unit tests.
- The existing 17 PostgreSQL-dependent tests require `TEST_DATABASE_URL`.

## Verification gate (before package publication)

```bash
uv run --locked ruff check .
uv run --locked pytest -q
```

The patch must be applied to an isolated copy of **V26** for testing. Only a
clean pass of both commands authorizes publication of the watcher-compatible
ZIP; failure must produce no installable package.
