# Optional CLI skill setup for Codex

First install/update Codex CLI separately via supported method. Then in shell:

```bash
uv tool install specify-cli
# In this existing repository, first check whether .specify files will be overwritten:
# specify init --here --integration codex
# Codex CLI support documented in github/spec-kit.
codex plugin marketplace add wshobson/agents
codex plugin add backend-development@claude-code-workflows
codex plugin add llm-application-dev@claude-code-workflows
codex plugin add security-scanning@claude-code-workflows
codex plugin add python-development@claude-code-workflows
```

Check CLI availability and plugin name changes. Source material: `docs/SKILLS.md`. For Chrome skill, read the linked SKILL.md and its referenced context-menu/permissions/side-panel docs, and optionally install its standalone skill with a trusted installer; code review before granting tool access.

Use `codex/PROMPT.md` as first instruction for implementation. It includes acceptance gates; do not merely say “use spec kit and build an MVP.”
