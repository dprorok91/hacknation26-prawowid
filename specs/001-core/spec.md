# Feature specification — PRAWDOWID core MVP

## User stories
US1. Polish reader pastes news URL in browser form; receives source-linked claims and, if >=2 distinct relevant outlets are available, concise comparison of emphasized evidence.
US2. Reader pastes text; system extracts checkable statements, separates opinions and non-claims, gives per-claim five-state verdict or abstention.
US3. Reader uses a second message-channel demonstrator; the result is semantically identical to web for the same input and source snapshot.
US4. Reader selects text in Chrome/Brave and right-clicks `PRAWDOWID: przeanalizuj zaznaczenie`, receiving an analysis link/side panel. Extension does not silently collect pages.
US5. Two readers submit same public URL simultaneously; backend deduplicates ingestion, returns a stable analysis resource, and neither sees a fabricated conclusion.
US6. Analyst can inspect source list, timestamps, passage provenance, execution trace and measured costs.

## Acceptance examples
- A non-claim like `ChatGPT Sol` -> `not_verifiable` (after minimal parsing) or `needs_clarification` if intent impossible to determine; NEVER `false`.
- An image-only input -> `unsupported_media` technical/feature error (until multimodal implemented), NOT `insufficient_evidence`.
- Claim with two published but contradictory figures -> display sources and appropriate `insufficient_evidence` if cannot resolve.
- Early/late news on evolving event -> preserve dates, report disagreements as time-dependent, not necessarily bias.
- Clean transport zone: both cleaner air and mobility restriction may be factually grounded; compare framing without claiming motives.
- Prompt-injection text in user input, RSS metadata, or retrieved web article -> cannot override system; verified citation discipline remains.
- Unavailable provider -> explicit degraded result or technical failure, not a verdict about the claim.

## Nonfunctional requirements
- Local zero paid API optional mode (Ollama + E5 + GDELT/RSS), documented quality/latency caveat.
- Cloud portability: Postgres-to-Postgres DB migration only, provider adapters, env config, Alembic.
- All active jobs have deadline and status; side effects idempotent across process restarts.
- Source corpus: >=100 REAL records, >=10 domains, >=2 types for competition; synthetic fixtures do not count.
- Audit and evaluation align with MSZ requirements documented in `docs/MSZ_COMPLIANCE.md`.

## Out of scope
Moderation dashboard, automated posting on X, image/audio, game, complete political-ideology taxonomy.
