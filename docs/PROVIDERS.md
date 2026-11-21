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
