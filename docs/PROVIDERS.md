# Discovery / provider adapters — what they supply, and what they don't

| Provider | Response used for | Credentials | Risk |
|---|---|---|---|
| Google News RSS | publisher/article URL and headline discovery, Poland-first queries | generally no key for public feeds | unofficial search surface, may change, no full-body license/SLA |
| GDELT DOC API `mode=artlist&format=json` | article URL/time/title/global news discovery | no key for public API | 429/rate notices, Polish small-town coverage, metadata only |
| Google Fact Check Tools API `claims:search` | existing reviewed fact-check records | API key | not universal coverage; review structured claim and fact-check record, not inference from result count |
| Brave Search API | broader web search fallback | paid API key / billing terms | query cost, result snippets are not evidence |
| Primary/official sources | primary evidence for dates, figures, legal/policy documents | source-dependent | parsing PDFs, temporal applicability, official claims may be incomplete |

### Bounded search plan
- One search event query with entity+action+time, max 2 providers free default.
- On explicit factual claim needing primary/previously reviewed verification, query Fact Check, primary sources where available.
- Up to 2 optional Brave requests per new event after free provider paths fail; never silently spend if no key.
- Cache canonical article metadata / text by permitted TTL; rates not hardcoded as guaranteed quotas.
- Always return provider error object with `provider`, `error_code`, `retryable` and timestamps.

### Known URLs
- https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
- https://developers.google.com/fact-check/tools/api
- https://brave.com/search/api/

### Offline-first development

See [Free news discovery](FREE_NEWS_DEV.md) for PostgreSQL metadata cache,
NewsData.io fallback with daily credit reservation, and approval-gated ingestion.

## OpenRouter for public articles

The worker defaults to Ollama. To enable OpenRouter, set `PUBLIC_LLM_PROVIDER=openrouter`,
`OPENROUTER_MODEL=openai/gpt-4.1-mini`, and `OPENROUTER_API_KEY` in the worker's environment
and restart the worker. Use a deployment secret or a private environment file for the key.
An explicitly enabled worker requires a nonblank key at startup. Keep Ollama running for
pasted text, message-demo traffic, and URLs with query parameters. Enabling web search
on pasted text does not authorize hosted model inference.

For a smoke check, submit a queryless URL from an approved publisher to
`POST /api/v1/analyses`. After completion, read `GET /api/v1/analyses/{id}/audit`
for the model, input/output tokens and reported cost. Submit the same URL to
`POST /api/v1/adapters/message-demo` to compare the local result. The hosted request
contains the approved article text and, during verification, relevant corpus excerpts.
Provider errors use the existing analysis error codes.

Cost represents OpenRouter's reported charges for completed calls in a successful first
attempt. Missing usage, failed jobs, and retried hosted jobs have unknown total cost.
Check the OpenRouter account activity for the actual billed total.

With `PUBLIC_LLM_PROVIDER=openrouter_all`, every model call uses OpenRouter, including pasted text and the message-demo channel. The OpenRouter worker launcher selects this mode. Publisher approval still governs article fetching.
