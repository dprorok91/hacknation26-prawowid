# Competition compliance — extracted from the 11-page MSZ brief (2026-11-21)

## Required / gate
- Text or URL input, responsive WWW **plus another channel or fully automatic demonstration adapter**. Chrome extension is useful but should not be sole basis for claiming multi-channel compliance unless organizers agree.
- Extract claim(s), then return exactly one of: `confirmed`, `false`, `misleading`, `insufficient_evidence`, `not_verifiable`. Technical failures, unclear questions and unsupported image are separate error states.
- At least 100 real documents/records from >=10 domains, >=2 source types including primary/public or official material and credible secondary reporting (or justified alternative). Sources audit/list including licenses and timestamps.
- Every factual assertion linked to concrete supporting materials: title, publisher, date, URL, supporting quote when possible. Explicit abstention if evidence lacking.
- Fully automatic request → retrieval → answer → log. No manual curation per query. Baseline trace capture: request time, cited sources, status; redacted personal data.
- Modular source/model/channel adapters; injection protections and clear privacy/licensing. Basic response time/cost measurements; 1000 query forecast.

## Acceptance targets (brief section 6)
- macro-F1 >= 0.75 on public test classes (hidden benchmark separately)
- citation coverage >= 95%; citation quote correctness >= 85%
- correct abstention >= 80%; injection defense >= 90%
- median text/URL latency <= 30s
- 100% end-to-end mandatory cases, 100% same result for same evidence across channels
- reference set min 30 examples with simple facts, old context, conflicting sources, no evidence, injection, multi-channel and provider errors.

## Deliverables
Description max 4k characters, PDF slides <=12, demo video <=3 min, repo/run instructions, architecture/providers/source licenses, reproducible tests, risk register, cost estimates. No secrets in submission.

## Scoring
30% verification quality, 20% safety/transparency, 20% deployability, 15% usability, 15% innovation after basics passed. This means narrative visualization should come AFTER basic robust evidence and multi-channel implementation.

## Explicit non-goals for 24h MVP
Full multimedia verification, continuous crawling of all Poland, automated X Community Notes publication, game, claim of objective news-bias scoring.

## Source
Uploaded `MSZ - Pełna treść zadania konkursowego.pdf`, pp. 1–11. Reconsult original PDF before final competition submission; this checklist paraphrases requirements, it is not a substitute for the official text.
