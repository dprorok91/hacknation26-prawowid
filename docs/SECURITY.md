# Security model (prompt injection is not a filter)

## Trust boundaries
1. Input text/URL is untrusted and can include commands to the LLM. Parse as DATA in a strictly delimited field; never concatenate into system/developer instruction content.
2. Source articles/RSS/search snippets are more untrusted; **do not let their content trigger tools, alter prompts, access secrets, or request filesystem/network actions**.
3. Network fetcher enforces SSRF guards: HTTPS/HTTP only; DNS resolves only globally routable IPs; check every redirect and resolved address; no localhost, internal IPv4/IPv6, metadata service, file/gopher schemes; restrict ports, size (e.g. <2MB compressed + decompression limit), timeout and redirects; enforce `robots.txt`/licenses when applicable.
4. Store clear document provenance (URI, publisher, fetched_at, published_at, content_hash, extraction status). HTML stripping / sanitization reduces injection surface but is NOT a reliable defense.
5. LLM outputs structured JSON only; never execute code/tools from it. Verify citation `passage_id` belongs to retrieved evidence set and `exact_quote` is actual substring (after explicit normalization rules). No evidence -> abstain.
6. LLM prompt asks to tag untrusted instructions as irrelevant; separately use behavioral tests to check the guard. No guarantee 100% injection detection.
7. Do not retain entire arbitrary user-supplied text by default; log redacted hashes/metrics only. Cap retention and add per-session delete. Redact obvious PII. No credentials in repo.
8. Inference must be an adapter: local Ollama default (privacy-friendly); any cloud LLM needs a visible provider/data-region disclosure.
9. API request size & URL allowlist/denylist checks, rate limiting, CORS origins, response JSON schema, structured error codes, no open proxy.
10. Threat exercise STRIDE: fake source identities, content tampering, missing audit, PII leak, request flooding, privilege escalation via prompt. Tests include injections in input, RSS, article body, metadata.

## Error taxonomy (must not masquerade as verdicts)
`invalid_input`, `needs_clarification`, `unsupported_media`, `source_unavailable`, `provider_rate_limited`, `model_unavailable`, `timeout`, `internal_error`.

## Nonfunctional assumptions
The product is informational; fact checking is supported by evidence quality, not an LLM 'confidence score' or site ideology. Negative assertions about actors/intent require explicit sources. Avoid full article reproduction by default.
