# AI models: concrete choices and honest limitations (checked 2026-11-21)

## Default offline-friendly stack
| Role | Implementation | Approximate download | Why / limitation |
|---|---|---:|---|
| Embeddings | `intfloat/multilingual-e5-small` via `sentence-transformers` | depends on package/cache | 384 dimensions, 94 languages incl. Polish; lightweight CPU inference. Follow model-card prefixes `query:` and `passage:`. Test Polish domain recall. |
| LLM small | `qwen3:4b` via Ollama | ~2.5 GB Q4 | Local structured extraction + summary; strong starting candidate for PL, **not independently validated for factual judgments**. |
| LLM bigger | `qwen3:8b` via Ollama | ~5.2 GB Q4 | Candidate for stronger extraction/comparison; needs more RAM/VRAM and may exceed 30s on CPU. |
| LLM alternate | `gemma3:4b` via Ollama | ~3.4 GB Q4 | Multilingual/vision alternative; different license, evaluate before use. |
| Embeddings alternate | `embeddinggemma` via Ollama | ~622 MB | 300M multilingual, easy Ollama setup; changes vector size and requires full re-index, test against E5. |

**These are engineering candidates, not a claim that any model meets the MSZ macro-F1 target.** Measure with Polish evaluation data. On a modest laptop CPU, running local LLM + fetch + reasoning under 30 seconds may be impossible; choose a hosted provider only if benchmarks justify it and disclose processing location/cost.

## Ollama usage
`ollama pull qwen3:4b`; `ollama pull qwen3:8b` only if machine fits. Ollama local API `http://127.0.0.1:11434/api/chat`, request `format` as JSON schema, `stream:false`. Set context budget and short output, no tool execution; validate against Pydantic anyway. Use `think:false` where supported if prioritizing latency; always test whether it changes extraction quality.

## E5 prefix issue
Use `query: ...` for claim/event queries, `passage: ...` for source chunks, `SentenceTransformer(...).encode(..., normalize_embeddings=True)`. Persist `embedding_model_id='intfloat/multilingual-e5-small@revision'`. The model is trained for retrieval, NOT for truth/bias verdicts.

## Benchmark before selecting
- 30+ Polish adjudicated examples incl. 5 output classes and 10 injections; compare qwen3:4b, qwen3:8b, a hosted model where available.
- Claim extraction accuracy; citation exact-match; evidence-support checks; retrieval recall@10, event-purity, F1, abstention, injection resistance, p50 latency and cost/100.
- Test 2–3 hard factual claims, 2 timeline conflicts, selective true facts with opposing interpretations, 2 no-evidence cases.

## Published references
- https://ollama.com/library/qwen3:4b
- https://ollama.com/library/qwen3:8b
- https://ollama.com/library/gemma3:4b
- https://ollama.com/library/embeddinggemma
- https://huggingface.co/intfloat/multilingual-e5-small
- https://docs.ollama.com/api
