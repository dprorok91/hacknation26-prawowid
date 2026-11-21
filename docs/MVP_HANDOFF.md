# PRAWDOWID MVP — verification handoff (snapshot `6740692`)

This note is based on the two shared file batches, not on a local PostgreSQL/Ollama run. Do **not** mark an implementation checkpoint complete solely because its code or tests exist.

## What already exists

- P0-00–P0-05: implementation and checkpoint entries marked done.
- P0-06: SQL lexical/vector/RRF retrieval with per-claim provenance and a lexical fallback; tests for Polish event matching and 384-dimensional E5.
- P0-07: structured Ollama extraction, literal-quote verification, abstention, and injection fixtures.
- P0-08: basic multi-source frame selection; tests for independent vs syndicated sources. The present frame text is a quoted excerpt, not a complete comparison of selected facts and common ground.
- P0-09: FastAPI reporting and responsive web form; static DOM tests, but no proven real-browser journey.
- P0-10: shared message-demo adapter, audit, deletion, and retention.
- P0-11: evaluation script, 30 reviewed public claims and 10 synthetic attacks; full benchmark gates **not measured** in the uploaded materials.
- P1-12: extension not present in the uploaded tree; intentionally deferred.

## Specific completion blockers

1. **Citable source coverage:** `source_records.jsonl` contains 112 discovery records; they are metadata-only by design, and that count must not be treated as 112 usable documents. Run `scripts/check_corpus_ready.py` after importing and approving sources under their policies. A usable entry must have a current licensed text version and indexed passages. The minimum is 100 documents, 10 domains, 2 source types.
2. **Ground-truth diversity:** 30 `reviewed_cases.jsonl` rows currently have only `confirmed` (21) and `false` (9) labels. Add human-reviewed `misleading`, `insufficient_evidence`, and `not_verifiable` examples to obtain 30+ cases spanning all five classes. Prefer balanced coverage, not an artificially good score.
3. **Citation review:** the current cases have no `reviewed_citation` labels. Citation *support correctness* cannot be measured without independently checked original quotes. Do not conflate title metadata with body-text evidence.
4. **Live verification:** PostgreSQL integration tests need `TEST_DATABASE_URL` pointing to a migrated **disposable** database. Test the running API + worker + Ollama using real sources you have approved and measured. Do not infer a passing p50 latency from offline mocked tests.
5. **Framing quality:** add a two-outlet, same-event test where two consequences are both true, but each article emphasizes a different one. Verify matching, common ground, dates, exact citations and no attribution of motives. Existing framing only shows independent excerpts.
6. **Demo/packaging:** evaluate actual accuracy/abstention/injection metrics, a real source-count report, same-result two-channel parity, 1000-query cost estimate, and accessible real-browser walkthrough. PDFs and demo exist by filename in the inventory, but their content is not in these batches and was not audited.

## Commands on the developer's Ubuntu machine

Use the existing `uv` environment and `.env` (do not upload `.env`). The script is read-only except for running tests and writing logs:

```bash
bash scripts/mvp_gate.sh
```

To cover DB integration, set `TEST_DATABASE_URL` to a **migrated disposable test database** before running; tests may create or remove data in it. The run also inspects the configured runtime corpus with read-only SQL. Check `~/Downloads/prawdowid-mvp-check/SUMMARY.txt`.

When the API **and worker** are already running, and you have an approved, fetchable public `SMOKE_URL`, you can optionally run:

```bash
RUN_LIVE=1 RUN_EVAL=1 SMOKE_URL='https://approved.example/article' \
  bash scripts/mvp_gate.sh
```

Replace the example URL with an actual permitted article. The evaluation may take a while; the runner is designed to record failures instead of silently counting them as successful calls. The attack-only run uses `--gate-set injection`; the reviewed-only run uses `--gate-set reviewed`, and the full runner defaults to all gates.

## Checkpoint policy

- P0-06 through P0-10: treat as **IMPLEMENTED / VERIFICATION PENDING** until all applicable local integration, safety and actual demo tests pass.
- P0-11: **PARTIAL** until all five reviewed classes, verified citations, citable corpus and scorecards are independently supported by measurement.
- P1-12: optional until baseline P0 is green.

Avoid manually checking off tasks or claiming a passing competition gate without a reproducible logged command and its result.
