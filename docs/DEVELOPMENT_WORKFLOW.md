# Development workflow

## Open a project

For PRAWDOWID, run `./scripts/setup.sh --skip-db` to prepare the Codex workspace and locked Python development dependencies, or run `./scripts/setup.sh` to start local PostgreSQL as well. On Windows with PowerShell, use `./scripts/setup.ps1 --skip-db` or `./scripts/setup.ps1`. Start `codex` from the repository root and use the existing ChatGPT session and model selection. [Setup and troubleshooting](SETUP_SKILLS.md)

For a new repository, run the following from its parent directory. Spec Kit uses Bash by default on Linux and macOS and PowerShell on Windows. [Spec Kit installation guide](https://github.github.com/spec-kit/installation.html)

```bash
specify init my-project --integration codex --integration-options=--skills --non-interactive
cd my-project
codex
```

Adapt `AGENTS.md` to the new codebase. Copy the five role definitions and the `[agents]` settings if you want the same delegation workflow. Fill out `TASK.md` and paste the first-project prompt into Codex. For PRAWDOWID implementation, start from its dedicated prompt. [Task template](../TASK.md) · [Reusable prompt](../codex/FIRST_PROJECT_PROMPT.md) · [PRAWDOWID prompt](../codex/PROMPT.md)

## From task to review

Type the `$speckit-*` names below in the Codex chat. For a small change, describe the requested behavior and run the relevant checks directly. [Spec Kit Codex integration](https://github.github.com/spec-kit/integrations/codex.html)

1. Use `$speckit-constitution` to establish engineering principles in a new project. Read the existing constitution before changing an established project.
2. Use `$speckit-specify` with the task and `$speckit-clarify` for decisions that affect scope, contracts, or acceptance tests. Ask `requirements_researcher` for evidence, assumptions, dependencies, and measurable acceptance criteria.
3. Agree on the API schemas, status codes, typed errors, and compatibility expectations with `solution_architect`. Use `$speckit-plan` to record architecture, persistence, and failure handling, and `$speckit-tasks` to order the work and assign files.
4. Run `$speckit-analyze` to check the spec, plan, and task list for gaps. Use `$speckit-checklist` when a focused checklist will help review the requirements.
5. Write tests first for retrieval, persistence, concurrency, and security boundaries, then run `$speckit-implement`. Assign distinct files to `frontend_builder` and `backend_data_builder` where work can proceed in parallel. The main agent integrates the changes and runs the checks.
6. Ask `quality_reviewer` to independently check acceptance criteria, security boundaries, failure handling, and test evidence. Use `$speckit-converge` when you want an assessment of the code against the spec and any missing work appended to `tasks.md`.

For the existing feature, set `SPECIFY_FEATURE_DIRECTORY=specs/001-core` before launching Codex if Spec Kit cannot infer the feature on `main`. The current project integration uses Bash for its Spec Kit helpers. [Project integration settings](../.specify/integration.json)

## Verify the environment

Run `./scripts/verify.sh` or `./scripts/verify.ps1` for configuration and discovery checks. Run `./scripts/verify.sh --smoke` or `./scripts/verify.ps1 --smoke` to exercise the requirements, architecture, and reviewer roles in a live read-only Codex session. The smoke uses the active ChatGPT session and checks the three role identities in the local Codex session records. Run `./scripts/doctor.sh` or `./scripts/doctor.ps1` for tool and host diagnostics. [Installation manifest](INSTALLATION_MANIFEST.md)
