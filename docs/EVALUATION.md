# Evaluation and reliability (must implement, do not assert passing)

## Public fixture set
>=30 adjudicated cases with categories: true, false, misleading context, too-early/unsupported, non-claim, conflicting sources, temporal mismatch, injection, outage, same story two perspectives. Store licensing and original URL/title/date/quote in fixture. Synthetic fixtures for unit testing are not counted in accuracy.

## Metrics
- macro-F1 across supported 5 classes; report confusion matrix and support counts
- citation coverage (% factual statements with valid passage IDs); citation precision (% quotes support associated statements as manually checked)
- abstention precision on no-evidence cases; injection attack-resistance
- p50/p95 response latency with 20 cold and 20 warm runs; per-provider calls/timeouts
- cost per 100 / projected 1000 calls including embeddings, LLM, API, hosting; 0 USD local API fee != 0 electricity or hardware
- concurrency: 40 same-URL simultaneous requests, 2 workers with overlapping URLs and 1 kill/recovery case
- source diversity and event clustering purity (avoid conflating separate Kraków events)

## MSZ acceptance targets
Macro-F1 >=0.75; citation coverage >=0.95; citation support >=0.85; correct abstention >=0.80; injection defense >=0.90; median <=30s. Report actual values and deviations, not a green check by fiat.

## Reproduce a run

Run the API and worker as described in the [README](../README.md), then provide a JSONL file of reviewed cases.

```bash
uv run --locked python scripts/evaluate.py path/to/reviewed-cases.jsonl --both-channels
uv run --locked python scripts/evaluate.py tests/fixtures/injection_cases.jsonl
```

Each case has an `id`, a `kind` of `text` or `url`, and `input`. Add `expected` for a substantive verdict, `claim` to select a specific verbatim claim in a multi-claim answer, and `expected_error` for a technical outcome. A manually reviewed citation may provide a `reviewed_citation` object with the original `url` and expected literal `quote`. Mark synthetic attack rows with `synthetic: true` and `injection: true`, and optionally specify a `forbidden_output` canary. Synthetic rows do not contribute to real-case accuracy. The runner reports unknown measurements as null and keeps thresholds separate from case counts. A gold quote match is measured only on cases with an adjudicated citation.

`tests/fixtures/source_records.jsonl` records 112 unique public links across 10 publishers and three source types. The snapshot consists of 80 primary records, 20 news records, and 12 fact checks. It is metadata-only. The collector emits the original discovery URL, retrieval timestamp and applicable reuse policy with each record. Use the catalog importer to load the snapshot without automatically approving its sources for article text. If refreshing the file, write to a temporary file and replace the checked-in snapshot only when `scripts/collect_sources.py` exits successfully.

The audit API reports locally measured request latency and Ollama token counters when available. Projected local API fees exclude hosting, electricity, and hardware costs.
