// Opens the right-hand Chrome Side Panel when the user clicks the extension icon
chrome.sidePanel
  .setPanelBehavior({ openPanelOnActionClick: true })
  .catch((error) => console.error("SidePanel behavior error:", error));