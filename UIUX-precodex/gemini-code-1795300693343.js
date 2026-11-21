async function highlightInReader(claimId) {
  state.selectedClaimId = claimId;
  renderApp();
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (tab?.id) {
    chrome.tabs.sendMessage(tab.id, { type: "SCROLL_TO_CLAIM", claimId });
  }
}