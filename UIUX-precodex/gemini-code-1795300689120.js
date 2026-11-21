// Clears previous VeriFrame spans safely without breaking page event listeners
function clearHighlights() {
  document.querySelectorAll(".veriframe-hl").forEach((span) => {
    const parent = span.parentNode;
    if (!parent) return;
    parent.replaceChild(document.createTextNode(span.textContent), span);
    parent.normalize();
  });
}

// Finds best matching sentence/phrase in visible text nodes and wraps it
function applyClaimHighlights(claims) {
  clearHighlights();
  if (!claims || !claims.length) return;

  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      const tag = node.parentElement?.tagName;
      if (!tag || ["SCRIPT", "STYLE", "NOSCRIPT", "TEXTAREA", "INPUT"].includes(tag)) {
        return NodeFilter.FILTER_REJECT;
      }
      if (node.nodeValue.trim().length < 25) return NodeFilter.FILTER_SKIP;
      return NodeFilter.FILTER_ACCEPT;
    }
  });

  const textNodes = [];
  let n;
  while ((n = walker.nextNode())) textNodes.push(n);

  claims.forEach((claim) => {
    // Prefer exact verbatim quote_span if provided by backend, fallback to claim.text
    const target = (claim.quote_span || claim.text || "").trim();
    if (!target) return;

    // 1. Exact substring pass
    for (const node of textNodes) {
      const idx = node.nodeValue.indexOf(target);
      if (idx !== -1) {
        wrapRange(node, idx, idx + target.length, claim);
        return;
      }
    }

    // 2. Token-overlap fallback for slightly normalized LLM claims
    const targetWords = new Set(target.toLowerCase().split(/\W+/).filter((w) => w.length > 3));
    if (targetWords.size < 3) return;

    let bestNode = null;
    let bestScore = 0;
    for (const node of textNodes) {
      const words = node.nodeValue.toLowerCase().split(/\W+/);
      let hits = 0;
      words.forEach((w) => { if (targetWords.has(w)) hits++; });
      const ratio = hits / targetWords.size;
      if (ratio > 0.65 && ratio > bestScore) {
        bestScore = ratio;
        bestNode = node;
      }
    }
    if (bestNode) {
      wrapRange(bestNode, 0, bestNode.nodeValue.length, claim);
    }
  });
}

function wrapRange(textNode, start, end, claim) {
  const range = document.createRange();
  range.setStart(textNode, start);
  range.setEnd(textNode, end);

  const span = document.createElement("span");
  span.className = `veriframe-hl veriframe-hl-${claim.score}`;
  span.dataset.veriframeClaimId = claim.id;
  span.addEventListener("click", (e) => {
    e.stopPropagation();
    setActiveSpan(claim.id);
    chrome.runtime.sendMessage({ type: "FOCUS_CLAIM", claimId: claim.id });
  });

  try {
    range.surroundContents(span);
  } catch (_) {
    // Skip cross-element ranges safely
  }
}

function setActiveSpan(claimId) {
  document.querySelectorAll(".veriframe-hl").forEach((el) => {
    el.classList.toggle("veriframe-active", el.dataset.veriframeClaimId === claimId);
  });
}

// Listen for commands from the Side Panel
chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "EXTRACT_PAGE_TEXT") {
    const articleEl = document.querySelector("article") || document.body;
    sendResponse({
      url: window.location.href,
      title: document.title,
      text: articleEl.innerText.slice(0, 15000)
    });
  } else if (msg.type === "APPLY_HIGHLIGHTS") {
    applyClaimHighlights(msg.claims);
  } else if (msg.type === "CLEAR_HIGHLIGHTS") {
    clearHighlights();
  } else if (msg.type === "SCROLL_TO_CLAIM") {
    setActiveSpan(msg.claimId);
    const el = document.querySelector(`[data-veriframe-claim-id="${msg.claimId}"]`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }
});