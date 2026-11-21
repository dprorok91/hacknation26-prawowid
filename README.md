Proejct vision:

### PRAWDOWID — Zobacz, jak powstają narracje

PRAWDOWID to narzędzie do analizy i porównywania sposobów, w jakie polskie media przedstawiają te same wydarzenia.

Zamiast arbitralnie rozstrzygać, kto mówi prawdę, wykorzystujemy AI do identyfikowania różnic w narracjach, selekcji faktów, interpretacjach oraz wzajemnych reakcjach mediów i osób publicznych. Pokazujemy nie tylko, **co się wydarzyło, ale również jak powstawały, ścierały się i ewoluowały narracje wokół wydarzenia.**

**Prototyp** obejmuje silnik agregujący i porównujący polskie publikacje oraz rozszerzenie przeglądarki, które jednym kliknięciem pozwala zobaczyć alternatywne perspektywy i prześledzić źródła poszczególnych twierdzeń.

**Nasza filozofia:** Nie obiecujemy obiektywizmu. Dążymy do świadomego i transparentnego subiektywizmu.

### Codex development environment

Run `./scripts/setup.sh --skip-db` for the Codex workspace and locked Python dependencies. Use `./scripts/setup.sh` to start local PostgreSQL as well. PowerShell users can run `./scripts/setup.ps1` with the same options.

- [Setup and troubleshooting](docs/SETUP_SKILLS.md)
- [Development workflow](docs/DEVELOPMENT_WORKFLOW.md)
- [Installation and verification report](docs/INSTALLATION_MANIFEST.md)
- [First-project prompt](codex/FIRST_PROJECT_PROMPT.md)

### Run the application

Use the existing Python 3.12 environment, Docker Compose PostgreSQL 16 with pgvector, and a local Ollama instance serving `qwen3:4b`.

```bash
uv sync --locked --extra dev
cp -n .env.example .env
make db-up
uv run --locked alembic upgrade head
uv run --locked python scripts/catalog.py import tests/fixtures/source_records.jsonl
make serve
```

In a second terminal, run `make worker`. Open `http://127.0.0.1:8000` for the Polish web UI or `/docs` for the API. The message-demo endpoint accepts the same input and creates the same type of analysis resource. For a PostgreSQL integration run, set `TEST_DATABASE_URL` to an already migrated disposable database before `make test`.

The collected snapshot contains discovery metadata. Approve article text only after checking the publisher's reuse terms. For example, Gov.pl's general terms specify CC BY-SA 4.0 for text unless noted otherwise. The worker still checks robots, paywall markers, and publisher directives on each fetch. [Gov.pl policy](https://www.gov.pl/web/gov/prawa-autorskie)

```bash
uv run --locked python scripts/catalog.py approve www.gov.pl \
  --publisher 'Gov.pl' --type primary \
  --license 'CC BY-SA 4.0 (unless marked otherwise)' \
  --policy https://www.gov.pl/web/gov/prawa-autorskie
```

For hybrid retrieval, sync the locked `ml` extra and prefetch the official multilingual-E5-small snapshot at revision `fd1525a9fd15316a2d503bf26ab031a61d056e98` into the Hugging Face cache. The worker only loads local safetensors and uses full-text search if embeddings are unavailable. [E5 model card](https://huggingface.co/intfloat/multilingual-e5-small)

Private text searches the local corpus and uses the configured Ollama endpoint. Keep the endpoint local for private input. The random analysis ID grants read access to a private report, so protect it as a credential and keep access logs disabled. Only the original response includes a separate deletion token. Defaults are a 15-minute retention window for text and 24 hours for URL analyses, configurable with `TEXT_RETENTION_MINUTES` and `ANALYSIS_RETENTION_HOURS`. See the evaluation runbook for measured results and competition gaps. [Evaluation](docs/EVALUATION.md)
