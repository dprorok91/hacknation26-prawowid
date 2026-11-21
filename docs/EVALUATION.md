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
