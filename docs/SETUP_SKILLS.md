# Codex setup and troubleshooting

## Set up a new machine

Install Codex CLI and uv using their official instructions. Verify `codex --version`, `codex login status`, and `uv --version`. Use ChatGPT sign-in when the CLI needs authentication. Install Git and Python 3.11 or newer for the repository, and Node.js with npm for the bundled Chrome DevTools MCP. Docker with Compose is needed for local PostgreSQL. [Codex CLI](https://developers.openai.com/codex/cli) · [uv installation](https://docs.astral.sh/uv/getting-started/installation/) · [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)

Before changing user-level tooling, copy the active Codex `config.toml` from `$CODEX_HOME` or `~/.codex` to a private backup. Review the marketplace repositories at the intended revisions. On a new profile, use the recorded `specify-cli` version with `uv tool install specify-cli==VERSION` and pass `--ref COMMIT` to `codex plugin marketplace add` to reproduce the recorded marketplace snapshots. Substitute the version and commits from the manifest. [Spec Kit installation](https://github.github.com/spec-kit/installation.html) · [Installation manifest](INSTALLATION_MANIFEST.md)

If the marketplaces or plugins are missing, install them using these commands. Supply the reviewed `--ref` for each new marketplace registration when reproducing a specific snapshot. [ECC Codex setup](https://github.com/affaan-m/ECC#codex-app-and-cli) · [wshobson Codex setup](https://github.com/wshobson/agents#codex-cli)

```bash
codex plugin marketplace add affaan-m/ECC
codex plugin add ecc@ecc
codex plugin marketplace add wshobson/agents
codex plugin add developer-essentials@claude-code-workflows
codex plugin add security-scanning@claude-code-workflows
codex plugin add python-development@claude-code-workflows
codex plugin add javascript-typescript@claude-code-workflows
codex plugin add backend-development@claude-code-workflows
```

The native ECC plugin supplies skills, including skills adapted from commands, and Chrome DevTools MCP for browser QA. Its hooks have a separate trust decision. Use the native installation as the sole ECC setup. The wshobson plugins expose skills, and the optional wshobson adapter separately generates Codex agents and command-derived skills. This repository uses its five project-specific agents in `.codex/agents/`. The Vercel and Spec Kit skills are project-local in `.agents/skills/`. [ECC Codex setup](https://github.com/affaan-m/ECC#codex-app-and-cli) · [wshobson Codex setup](https://github.com/wshobson/agents#codex-cli) · [Codex subagents](https://developers.openai.com/codex/multi-agent)

From the repository root, run `./scripts/setup.sh` (or `./scripts/setup.ps1` in PowerShell) to sync locked development requirements and start local PostgreSQL. Pass `--skip-db` to prepare the Codex workspace without starting PostgreSQL. Setup preserves an existing `.env`. The current repository already has the Spec Kit integration. For an existing Spec Kit project that lacks the Codex integration, use `specify integration install codex --integration-options=--skills`. Use the workflow guide to initialize a new project. [Spec Kit Codex integration](https://github.github.com/spec-kit/integrations/codex.html) · [Development workflow](DEVELOPMENT_WORKFLOW.md)

## Diagnose failures

| Symptom | What to check |
|---|---|
| `codex`, `uv`, or `specify` is missing | Check `PATH` and the selected executable, especially if multiple installers were used. Use `uv tool dir --bin` to locate the uv tool executables. |
| Codex skills or agents are missing | Start Codex from the repository root. Confirm the repository is trusted after reviewing its config and restart the session after installation. Run the verification script for discovery evidence. |
| ECC is listed but its skills or MCP are missing | Run the upstream cache checker from a reviewed ECC checkout with `node scripts/codex/check-plugin-cache.js`. Check `codex mcp list` for server registration and use a disposable local page to verify browser access. |
| Spec Kit selects the wrong feature | Use `specify integration list` to check the default integration. Set `SPECIFY_FEATURE_DIRECTORY=specs/001-core` for the existing project on `main`. |
| A live Codex check fails | Use `codex login status` and `codex doctor --summary` to check authentication and connectivity. If the selected model is unavailable, pick a supported model with `/model` in Codex. |
| Docker reports permission denied | Check `docker compose version` and `docker info`. Fix daemon access through the platform's documented setup and rerun, or use `--skip-db` while working on the Codex environment. |
| GitHub CLI reports an invalid login | Reauthenticate with `gh auth login -h github.com` when GitHub operations are needed. The Codex ChatGPT session uses separate authentication. |

Run `./scripts/doctor.sh` or `./scripts/doctor.ps1` for a host diagnostic, then rerun `./scripts/verify.sh` or `./scripts/verify.ps1`. Keep credentials and raw logs that may contain secrets local.

ECC's browser MCP searches for Google Chrome by default. On this Ubuntu host, the installed Brave executable passed a browser smoke with an isolated profile. Use this session-only override to keep the existing plugin and use a separate browser profile. If you use a different local executable, substitute its path. [Chrome DevTools MCP options](https://github.com/ChromeDevTools/chrome-devtools-mcp)

```bash
codex \
  -c 'mcp_servers.chrome-devtools.command="npx"' \
  -c 'mcp_servers.chrome-devtools.args=["-y","chrome-devtools-mcp@1.10.1","--headless","--isolated","--no-usage-statistics","--no-performance-crux","--executablePath","/usr/bin/brave-browser"]'
```

## Update, repair, or uninstall

- **Codex and uv.** Use the original official install channel, then rerun verification. Restore a saved config by reviewing the diff and recovering only the relevant settings. Keep credentials and unrelated preferences intact. [Codex CLI](https://developers.openai.com/codex/cli) · [uv installation](https://docs.astral.sh/uv/getting-started/installation/)
- **Spec Kit.** Preview with `specify self upgrade --dry-run`. Update the CLI using its original install method, back up local Spec Kit files, then run `specify integration upgrade codex` for integration files. Review the diff and resolve any locally modified-file conflict before proceeding. For a complete upgrade of the shared assets, follow the upstream guide. To uninstall the CLI from a uv setup, run `uv tool uninstall specify-cli`. To remove only the Codex integration from a project, review `specify integration uninstall codex --help` and preserve the specs and constitution. [Spec Kit upgrade guide](https://github.github.com/spec-kit/upgrade.html)
- **Native plugins.** Review the target source and run `codex plugin marketplace upgrade ecc` or `codex plugin marketplace upgrade claude-code-workflows`, then rerun `codex plugin add` for the intended plugin IDs and restart Codex. Uninstall a selected plugin with `codex plugin remove PLUGIN@MARKETPLACE`, such as `codex plugin remove ecc@ecc`. Retain any marketplace still used by another plugin. [ECC plugin lifecycle](https://github.com/affaan-m/ECC#codex-app-and-cli) · [wshobson Codex setup](https://github.com/wshobson/agents#codex-cli)
- **Project skills and agents.** Repair repository-managed files from a known-good Git revision. The reviewed skills CLI can restore Vercel skills using `npx --yes skills@1.9.0 install --frozen-lockfile`. For an update, review the incoming skill instructions and scripts, then run `npx skills update SKILL_NAME` with the reviewed CLI. Keep the skill changes and `skills-lock.json` together. [Vercel Agent Skills](https://github.com/vercel-labs/agent-skills) · [Vercel skills CLI](https://github.com/vercel-labs/skills)
