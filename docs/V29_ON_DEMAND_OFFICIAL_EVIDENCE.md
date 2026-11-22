# V29 – evidence from reviewed municipal public-information pages

V29 addresses a concrete gap: an online discovery result is **metadata**, not a usable citation. The local corpus stays publisher-approved; the live path is restricted to **zdmk.krakow.pl** and consumes *only transient, bounded editorial passages* during an explicitly public online search. This host is Krakow's municipal road authority. The municipal public-sector information reuse explanation is https://www.bip.krakow.pl/?dok_id=48482. Excluded works, images, robots/noindex/noarchive/nosnippet, paywalls, and changes to publication terms remain blockers. V29 does **not** treat all Polish news as freely reusable.

## Safeguards

- Only an exact HTTPS hostname in the reviewed registry is eligible; redirects to other hosts are discarded.
- The existing SafeWeb checks per-hop DNS / SSRF, robots.txt, MIME type and response size.
- The editorial extractor rejects restrictive robots meta and paywall signals.
- At most 2 pages, 3 passages per page and 1500 characters per passage per analysis; downloaded HTML/body are not persisted. Only a validated literal quote, URL, provenance and bounded report material are stored.
- A reviewed starting URL supports claims about Krakow SCT **proposals**. This is a discovery seed, not a hardcoded answer, and its text is fetched and checked on each run. It must never be interpreted as evidence that the proposal was enacted.
- Source metadata alone cannot become evidence. A model must choose a verbatim quotation; claim-relevance guards remain in force.
- For all other unapproved publishers, online results remain labeled discovery-only until publisher-specific reuse review.

## Smoke test

After deploying V29, enable online search and submit: `Władze Krakowa zaproponowały zmniejszenie SCT.` Check that the official ZDMK link is actually fetched and that the resulting decision cites a literal source fragment. Also test `Władze Krakowa już wprowadziły zmniejszoną SCT.` and verify that a proposal is not treated as enactment. If access is unavailable or the page forbids reuse, the correct outcome is abstention, not confirmation.

V29 does not implement general cross-outlet narrative clustering, full-article completeness, or rights review of new publishers.
