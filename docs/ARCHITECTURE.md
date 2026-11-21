# Architecture (C4-lite / UML)

## Context and adapters
```mermaid
flowchart TB
  WEB[Responsive WWW] --> HTTP[HTTP API]
  EXT[Chrome MV3: action + selection menu] --> HTTP
  ADAPT[Discord / demo message adapter] --> HTTP
  HTTP --> APP[Analysis Orchestrator]
  APP --> QC[Gate + Claim Extraction]
  APP --> RET[Hybrid Retrieval]
  APP --> VER[Evidence Verification]
  APP --> NAR[Framing Comparison]
  RET --> PG[(Postgres + pgvector + FTS + jobs)]
  RET --> DISC[Discovery: RSS / GDELT / FactCheck / optional Brave]
  APP --> MOD[LLM adapter: Ollama / hosted]
  WORK[Worker in separate process] <--> PG
  WORK --> FETCH[Safe Fetch + Indexing]
  FETCH --> PG
```

## Retrieval's precise hot path
```mermaid
flowchart TB
  I[Extract entity/event/claims/date] --> CA{Fresh cached report?}
  CA -->|yes| OUT[Return source-linked report]
  CA -->|no| L[Local: lexical + vector in parallel]
  L --> COVER{Enough distinct evidence?}
  COVER -->|yes| R[Per-claim retrieval + rerank]
  COVER -->|no| X[Concurrent external discovery with per-provider budgets]
  X --> JOB[Deduplicated fetch jobs]
  JOB --> W[Bounded workers: fetch, validate, chunk, embed]
  W --> R
  R --> CITE[Evidence IDs + exact quote + support relation]
  CITE --> V[Verdict OR honest abstention]
  CITE --> N[Comparative frames, no global bias score]
  V --> OUT
  N --> OUT
```

## Worker with durable ownership
```mermaid
sequenceDiagram
  participant A as API request A
  participant B as API request B
  participant DB as PostgreSQL
  participant W1 as Worker 1
  participant W2 as Worker 2
  participant N as News endpoint
  A->>DB: INSERT job(key=canonical_url) ON CONFLICT DO NOTHING
  B->>DB: INSERT same job key ON CONFLICT DO NOTHING
  W1->>DB: SELECT ... FOR UPDATE SKIP LOCKED
  W2->>DB: SELECT ... FOR UPDATE SKIP LOCKED
  DB-->>W1: Job lease (commit immediately)
  DB-->>W2: Other job / none
  W1->>N: GET with timeout & SSRF guard (outside DB transaction)
  N-->>W1: Article metadata and allowed text
  W1->>DB: UPSERT document/passages + mark job completed (short txn)
  DB-->>A: Evidence query sees indexed result
  DB-->>B: Same indexed result
```

## Main UML ports/interfaces
```mermaid
classDiagram
 direction LR
 class AnalysisOrchestrator { +analyze(AnalysisRequest) AnalysisResponse }
 class InputGate { <<interface>> +evaluate(input) GateDecision }
 class ClaimExtractor { <<interface>> +extract(input) ClaimBundle }
 class SourceDiscovery { <<interface>> +discover(EventQuery) DiscoveryResult[] }
 class DocumentFetcher { <<interface>> +fetch(url) FetchedDocument }
 class DocumentRepository { <<interface>> +upsert(doc) DocumentId +get(url) Document }
 class EvidenceRetriever { <<interface>> +retrieve(ClaimBundle) EvidenceSet }
 class Embedder { <<interface>> +embed_query(text) Vector +embed_passages(texts) Vector[] }
 class JobRepository { <<interface>> +enqueue(key, payload) JobId +lease_next(worker) Job? +complete(id) void }
 class ClaimVerifier { <<interface>> +verify(claim,evidence) ClaimVerdict }
 class NarrativeComparer { <<interface>> +compare(event,documents) MediaFrame[] }
 class LLMProvider { <<interface>> +complete_structured(schema,prompt) dict }
 AnalysisOrchestrator ..> InputGate
 AnalysisOrchestrator ..> ClaimExtractor
 AnalysisOrchestrator ..> EvidenceRetriever
 AnalysisOrchestrator ..> ClaimVerifier
 AnalysisOrchestrator ..> NarrativeComparer
 EvidenceRetriever ..> Embedder
 EvidenceRetriever ..> SourceDiscovery
 EvidenceRetriever ..> DocumentRepository
 ClaimExtractor ..> LLMProvider
 ClaimVerifier ..> LLMProvider
 NarrativeComparer ..> LLMProvider
```

**Deployment:** one repo, one backend codebase, API and worker processes. NO microservices, no queue server, no content-script that continuously monitors browsing.
