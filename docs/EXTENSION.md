# Chrome/Brave extension MVP contract (P1)

Manifest V3. Use permissions `contextMenus`, `activeTab`, `sidePanel` as needed; API origin only in `host_permissions`. Add `action` key; no default popup if using `chrome.action.onClicked` to open side panel. `chrome.contextMenus.create({ id:'analyze-selection', title:'PRAWDOWID: przeanalizuj zaznaczenie', contexts:['selection']})`; on clicked, `info.selectionText` is sent to backend after user gesture, with visible feedback. For page link, `chrome.action.onClicked` captures tab.url under user action. Avoid `tabs` permission unless genuinely required, and always test on unpacked extension.

On user click: send request to same `POST /api/v1/analyses`, receive `202`, poll `GET /api/v1/analyses/{id}` with cap, show report or explicit error. Side panel remains readable while browsing. Respect Chrome extension CSP: no eval/inline JavaScript, no remote code. Never send browsing history automatically.

Sources: https://github.com/GoogleChrome/modern-web-guidance/blob/main/skills/chrome-extensions/SKILL.md and related context menus/permissions/side panel reference files.
