# Source provenance and report roles — V12

The two flags on **every newly discovered link and every cited excerpt**:

- `source_category`: `official`, `major_media`, `fact_check`, `other_media`.
- `source_role`: `core`, `perspective`.

Government/public-service sites, selected large editorial outlets and
fact-checking publications appear as core sources **for navigation**.
Smaller outlets and unknown publishers appear as additional perspectives.
These tags are **not measures of reliability**, confidence scores, verdict
evidence, or a reason to suppress a contrary report.

Classification is based on a reviewed domain registry. When Google News
supplies only a redirect URL, a publisher name can give a provisional
*metadata-only* label; unverified publisher text from another host never
overrides its domain.

`source_type` in the database remains the existing backward-compatible enum
(`primary`, `news`, `factcheck`). The two new report flags are derived from
the repository registry and exposed in API responses. This requires no new
migration. Saved reports without fields receive compatible API defaults.

Run after V12 deployment to register/normalize source records (no fetch):

    uv run --locked python scripts/register_source_roles.py
    uv run --locked python scripts/register_source_roles.py --apply

The registration never approves rights/licenses or adds text content.
It only upserts publisher-domain metadata and updates existing `source_type`
when the host matches the reviewed registry. Existing source approvals,
published documents, citations and retention policy are not altered.

URL model-eligibility filtering was removed from report verification and
external-model URL routing. See `docs/issues/001-hosted-model-url-privacy-review.md`
for deferred risk review. SafeWeb SSRF, robots, redirect and license gates
remain intact. The message-demo's external-model consent remains disabled.

Regression tests ensure that a Google News article from an unknown publisher
stays supplementary and that formal fact-check metadata is never silently
converted into a PRAWDOWID verdict.
