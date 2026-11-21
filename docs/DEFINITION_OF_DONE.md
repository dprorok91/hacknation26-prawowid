# Done means proof

- [ ] `curl -X POST /api/v1/analyses` with public news URL returns 202, then GET returns verifiable JSON without manual action.
- [ ] Input text with short non-claim does not receive fabricated truth verdict.
- [ ] Repeated URL requests do not duplicate documents/jobs; 2 workers do not own same lease.
- [ ] Every sourced factual assertion has exact passage-level provenance, dates, publisher, URL.
- [ ] Contradictory/no-evidence cases abstain.
- [ ] Same-event narrative comparison includes different framings even where both facts are true; does not accuse publishers of intent.
- [ ] Message adapter and WWW use identical orchestrator and source snapshot.
- [ ] Tests cover SSRF, injection, query limits, provider outage.
- [ ] PostgreSQL migration and cloud DSN change verified.
- [ ] 100 REAL doc/records, 10 domains, >=2 source types audited (not synthetic).
- [ ] Scorecard with measured benchmark metrics, 1000-request cost estimate, PDF <=12 slides, demo <=3min, README.
