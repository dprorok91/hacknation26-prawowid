# Skills and coding workflow (verified repo references)

## Sources requested
1. GitHub Spec Kit provides the specification, planning, tasks, and verification workflow. Invoke `$speckit-*` in Codex. [Development workflow](DEVELOPMENT_WORKFLOW.md) · [GitHub Spec Kit](https://github.com/github/spec-kit)
2. The installed native wshobson plugins cover backend architecture, API design, security, Python, JavaScript, debugging, and testing. Consult the upstream RAG and LLM evaluation skills as references when working on retrieval. [Installation manifest](INSTALLATION_MANIFEST.md) · [wshobson/agents](https://github.com/wshobson/agents)
3. https://github.com/GoogleChrome/modern-web-guidance/blob/main/skills/chrome-extensions/SKILL.md — Chrome Manifest V3, `contextMenus` selection text, `activeTab`, side panel trigger, CSP and minimum permissions. Fetch and read the original skill + `references/extensions/{context-menus,permissions,side-panel}.md` before implementation. Do not copy broad `<all_urls>` permission by default.

## Application
Spec Kit dictates acceptance first. Agents/skills guide code, interface boundaries, RAG, and security tests. Chrome skill is normative for extension implementation, not license to overcomplicate backend.

## Codex checkpoint discipline
One commit/task, tests green, note deviations, refresh contract/README, no unreviewed auto-merge. Test with fixture data and actual external APIs only with optional credentials; offline tests MUST NOT require live secrets.
