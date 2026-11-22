# Issue: review URL and hosted-model privacy policy

**Status:** deferred by product owner (V12).

The `eligible_model_url()` allow/deny check was removed from report
verification, URL routing, official seed registration and the link auditor.
Generic query parameters such as `?dok_id=...` or `?id=...` must never
suppress an already indexed and approved document citation.

## Follow-up security review
- Do not infer that an HTTP URL is public or safe from its syntax alone.
- Review consent for hosted LLM transfer, especially `openrouter_all`.
- Check source-policy approval and content-license enforcement before
  ingestion (existing gates remain intact).
- Retain SafeWeb SSRF, IP/DNS, redirect, robots and size protections.
- Avoid passing URLs or access tokens to LLMs, reports and logs.
- Add privacy redaction where source URLs may contain session identifiers.
- Review threat model and add comprehensive tests before release.

**V12 scope:** remove the ad-hoc `eligible_model_url` gate; do not remove
SSRF protections or licensing gates.
