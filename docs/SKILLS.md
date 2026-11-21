# Skills and coding workflow (verified repo references)

## Sources requested
1. https://github.com/github/spec-kit — specifications, constitution, plan, tasks, convergence. Install CLI: `uv tool install specify-cli`; in existing project root, **review existing .specify contents before initializing**, then `specify init --here --integration codex` (do not overwrite files without checking). Commands are invoked in Codex, not pasted into shell.
2. https://github.com/wshobson/agents — choose limited relevant skills rather than installing all agents: `backend-development` (`api-design-principles`, `architecture-patterns`); `llm-application-dev` (`rag-implementation`, `llm-evaluation`, `hybrid-search-implementation`); `security-scanning` (`stride-analysis-patterns`); `python-development` (`python-testing-patterns`); `developer-essentials` (`code-review-excellence`). Their Codex install route in README: `codex plugin marketplace add wshobson/agents` then `codex plugin add backend-development@claude-code-workflows` (and individually relevant plugins). Verify Codex plugin CLI version.
3. https://github.com/GoogleChrome/modern-web-guidance/blob/main/skills/chrome-extensions/SKILL.md — Chrome Manifest V3, `contextMenus` selection text, `activeTab`, side panel trigger, CSP and minimum permissions. Fetch and read the original skill + `references/extensions/{context-menus,permissions,side-panel}.md` before implementation. Do not copy broad `<all_urls>` permission by default.

## Application
Spec Kit dictates acceptance first. Agents/skills guide code, interface boundaries, RAG, and security tests. Chrome skill is normative for extension implementation, not license to overcomplicate backend.

## Codex checkpoint discipline
One commit/task, tests green, note deviations, refresh contract/README, no unreviewed auto-merge. Test with fixture data and actual external APIs only with optional credentials; offline tests MUST NOT require live secrets.
