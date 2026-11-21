# Codex environment inventory and verification

Recorded 21 November 2026 on Ubuntu 24.04.4 LTS, x86-64. The repository uses the current ChatGPT sign-in and Codex model selection. Setup uses the locked development dependencies and preserves an existing `.env`. The installation and troubleshooting guide covers reproduction and maintenance. [Setup guide](SETUP_SKILLS.md)

## Tools and integrations

| Component | Verified version | Scope and source |
|---|---|---|
| Codex CLI | 0.171.0 | `~/.local/bin/codex`, official OpenAI standalone installer |
| uv | 0.13.9 | `~/.local/bin/uv`, official Astral installer |
| Git and GitHub CLI | 2.43.0 and 2.45.0 | Host executables in `/usr/bin` |
| Python and pip | 3.12.3 and 24.0 | Host executables in `/usr/bin`. Project dependencies sync to `.venv` from `uv.lock`. |
| Node.js and npm | 24.14.0 and 11.9.0 | Existing user NVM installation |
| Docker Engine and Compose | 29.5.3 and 5.1.4 | Host installations. The approved host session reaches the daemon using the account's existing `docker` group. |
| GitHub Spec Kit | specify-cli 1.4.3 | PyPI via uv tool at `~/.local/bin/specify`. Codex integration in `.specify` and skills in `.agents/skills`. |
| Chrome DevTools MCP | 1.10.1 | ECC plugin resource. The installed Brave 145.1.87.191 passed with a session override. |
| GitHub Codex plugin | 2f1a8948 | Existing `github@openai-curated` installation audited in the CLI listing. |

[Codex CLI](https://developers.openai.com/codex/cli) · [Astral uv](https://docs.astral.sh/uv/getting-started/installation/) · [Spec Kit 1.4.3](https://github.com/github/spec-kit/releases/tag/v1.4.3) · [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)

## Plugins and skills

Native plugins are enabled in the user Codex profile and cached below `~/.codex/plugins/cache`. The marketplace snapshots used for initial installation were `4adeee6732a12d1540f49a4d50a38ce8c97f9a53` for ECC and `42203085cfc5736204bf0f6d436dff7e1da0bf3f` for wshobson. Marketplace checkouts can advance on CLI startup. The installed plugin versions below were checked separately. [ECC native Codex integration](https://github.com/affaan-m/ECC#codex-app-and-cli) · [wshobson native Codex integration](https://github.com/wshobson/agents#codex-cli)

| Native plugin | Version | Intended use | Evidence |
|---|---|---|---|
| `ecc@ecc` | 2.2.3 | Planning, contract-first workflow, refactoring, research, documentation, defensive review, and browser QA | Enabled, upstream cache check passed, `ecc:browser-qa` discovered |
| `backend-development@claude-code-workflows` | 1.3.3 | API contracts, modular architecture, and integration patterns | Enabled, `backend-development:api-design-principles` discovered |
| `developer-essentials@claude-code-workflows` | 1.0.5 | Debugging, review, testing, and Git workflows | Enabled, `developer-essentials:debugging-strategies` discovered |
| `security-scanning@claude-code-workflows` | 1.3.3 | Threat modeling and defensive review | Enabled, `security-scanning:stride-analysis-patterns` discovered |
| `python-development@claude-code-workflows` | 1.2.4 | Python tooling, testing, and service development | Enabled, `python-development:python-testing-patterns` discovered |
| `javascript-typescript@claude-code-workflows` | 1.2.4 | JavaScript and TypeScript patterns and tests | Enabled, `javascript-typescript:modern-javascript-patterns` discovered |

| Skill or workflow | Source revision and scope | Intended use |
|---|---|---|
| `vercel-react-best-practices` | Vercel Agent Skills `5a37a6329fbad3666c3207392a6a7c0110e92efb`, project scope | React and Next.js performance |
| `vercel-composition-patterns` | Same Vercel revision, project scope | Composable component APIs |
| `web-design-guidelines` | Same Vercel revision, project scope | UI and accessibility review |
| `speckit-constitution`, `speckit-specify`, `speckit-clarify`, `speckit-plan`, `speckit-tasks`, `speckit-implement`, `speckit-analyze`, `speckit-checklist`, `speckit-converge` | GitHub Spec Kit 1.4.3, project scope | Invoke the nine workflows in Codex using their `$speckit-*` names |
| `openai-docs` | OpenAI Skills catalog, built-in with Codex 0.171.0 in `~/.codex/skills/.system` | Official OpenAI platform and API guidance |

The three Vercel directories match the hashes in [skills-lock.json](../skills-lock.json). The discovery check confirmed the listed project and OpenAI system skills. ECC provides browser QA guidance and its Chrome DevTools MCP supplies the browser tools. The OpenAI Playwright Interactive skill's full-access requirement conflicts with this project's sandbox settings. [Vercel source](https://github.com/vercel-labs/agent-skills/tree/5a37a6329fbad3666c3207392a6a7c0110e92efb) · [Spec Kit Codex integration](https://github.github.com/spec-kit/integrations/codex.html) · [OpenAI Skills](https://github.com/openai/skills) · [Playwright Interactive prerequisites](https://github.com/openai/skills/blob/main/skills/.curated/playwright-interactive/SKILL.md)

The wshobson native manifests expose skills. The adapter for generated agents and command-derived skills is separate. ECC's native plugin includes command-derived skills and hook definitions. Hook execution has its own Codex trust setting.

## Custom subagents

The five definitions reside in `.codex/agents/`. The effective project configuration enables a maximum of five concurrent subagents. Each definition inherits the selected model and provider. [Codex subagent configuration](https://developers.openai.com/codex/multi-agent)

| Role | Responsibilities | Verification |
|---|---|---|
| `requirements_researcher` | Verify requirements and dependencies, identify assumptions and unknowns, and define acceptance criteria | Valid TOML, completed live sample |
| `solution_architect` | Design interfaces, persistence, migration, concurrency, and component boundaries | Valid TOML, completed live sample |
| `frontend_builder` | Implement accessible React or browser extension code within assigned files and contracts | Valid TOML and spawned for a read-only role check in this Codex session |
| `backend_data_builder` | Implement API, stores, migrations, concurrency controls, and integration tests within assigned files | Valid TOML and spawned for a read-only role check in this Codex session |
| `quality_reviewer` | Independently inspect requirements, security, failure handling, concurrency, and tests | Valid TOML, completed live sample |

## Verification report

| Check | Result | Evidence |
|---|---|---|
| Repeated setup | PASS | Ran `./scripts/setup.sh --skip-db` twice with an offline copy of the uv cache. Both runs synced the locked environment and preserved the checksums of `.env`, `uv.lock`, and `skills-lock.json`. |
| Host and Codex tooling | PASS | `./scripts/doctor.sh` confirmed installed tools and the existing ChatGPT sign-in. The managed sandbox requires approval for the Codex runtime-state checks on this host. |
| GitHub CLI authentication | PASS on host | `gh auth status --hostname github.com` confirmed the existing sign-in on the host. The managed shell could not verify it. |
| Effective Codex config and skills | PASS | `./scripts/verify.sh --smoke` used the strict App Server configuration and the `skills/list` response. It found the active project layer, a five-subagent cap, and 428 enabled skill entries including all 19 inventory samples. The audited project and selected plugin skill directories had zero exact duplicate skill names. |
| ECC cache | PASS | From the reviewed ECC checkout, `node scripts/codex/check-plugin-cache.js --plugin-dir ~/.codex/plugins/cache/ecc/ecc/2.2.3` resolved the skills, MCP, hooks, and icons. |
| Spec Kit integration | PASS | `specify check`, `specify integration list`, and `specify artifact list --json` confirmed Codex and nine commands. `SPECIFY_FEATURE_DIRECTORY=specs/001-core bash .specify/scripts/bash/check-prerequisites.sh --paths-only --json` resolved the existing spec, plan, and tasks. A fresh Codex-skill initialization also succeeded in a scratch directory. |
| Live read-only sample | PASS | `./scripts/verify.sh --smoke` completed. The verifier matched three named child sessions with completed responses to the same parent. The reviewer checked pool exhaustion, cancellation, bounded parallel probes, and credential redaction in the readiness contract. |
| Browser | PASS with session override | ECC registered one `chrome-devtools` server. The MCP 1.10.1 handshake succeeded. With the installed Brave path, the server opened an isolated page and read its title and heading. The default launch reported absent Google Chrome. [Browser override](SETUP_SKILLS.md#diagnose-failures) |
| Git and secrets | PASS | The verifier checked the Git index for sensitive-looking filenames, verified `.env` is ignored, and confirmed `.env.example` is trackable. `git diff --check` passed. This is a filename check. |
| Tests, Bash syntax, and tooling lint | PASS | `uv run --offline --locked pytest -q` reported 11 passed. `bash -n scripts/setup.sh scripts/verify.sh scripts/doctor.sh`, Ruff lint, and format checks for the changed Python tooling passed. |
| Full project lint | PASS | `uv run --offline --locked ruff check .` passed after the four import and annotation findings were fixed. The four edited modules pass the formatter check. The repository-wide formatter still flags three existing files, including a generated Spec Kit helper. |
| Docker daemon access | PASS on host | The host reports an active Docker service, an effective `docker` group, and a `root:docker` socket with mode `0660`. Host `docker info --format '{{.ServerVersion}}'` returned `29.5.3`. The managed process lacks that group and receives permission denied. |
| PostgreSQL runtime | PASS | `docker compose --env-file .env config --quiet` passed. A disposable `pgvector/pgvector:pg16` container ran PostgreSQL 16.16 and pgvector 0.8.7. A temporary Alembic revision passed two upgrade and downgrade cycles, including a `vector(384)` column. Forty simultaneous duplicate enqueue calls produced one row. Two overlapping transactions using `FOR UPDATE SKIP LOCKED` claimed distinct jobs. The disposable container was removed. |
| Application migrations and worker concurrency | BLOCKED | `alembic heads` reports a missing `script_location`. `make worker` reports that the worker is a stub. The database fixture exercised the runtime. Application revisions, persistence, and worker integration coverage are tracked in P0-02 and P0-03. [Project tasks](../tasks/TASKS.md) |
| PowerShell wrappers | PARTIAL | The wrappers share the verified Python entry point. PowerShell is unavailable on the audited host, so the wrappers were not run. |

Codex logged a transient Git fetch timeout while refreshing the ECC marketplace during a smoke run. The installed ECC cache and its runtime discovery checks passed.

## Reuse and maintenance

- Use the [setup guide](SETUP_SKILLS.md) to reproduce the recorded tool versions, recover the native plugins, or upgrade and uninstall individual components.
- Use the [development workflow](DEVELOPMENT_WORKFLOW.md) to initialize a new Codex project, describe a task, create specs and contracts, and run independent review.
- Begin new work with the [empty task template](../TASK.md) and [first-project prompt](../codex/FIRST_PROJECT_PROMPT.md). The existing [PRAWDOWID implementation prompt](../codex/PROMPT.md) applies to this project.
- The host session can use the configured Docker daemon with its existing group membership. The managed session requires an approved host operation for Docker commands. [Docker Engine group access](https://docs.docker.com/engine/install/linux-postinstall/#manage-docker-as-a-non-root-user)
- Use the setup guide's browser override on this host or install Chrome through the platform's normal package management workflow.
