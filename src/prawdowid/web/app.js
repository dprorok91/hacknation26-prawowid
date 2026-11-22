"use strict";

const form = document.querySelector("#analysis-form");
const submit = document.querySelector("#submit-analysis");
const status = document.querySelector("#analysis-status");
const fieldError = document.querySelector("#field-error");
const report = document.querySelector("#report");
const reportTitle = document.querySelector("#report-title");
const reportContent = document.querySelector("#report-content");
const actions = document.querySelector("#report-actions");
const share = document.querySelector("#share-report");
const publicLink = document.querySelector("#public-link");
const deleteButton = document.querySelector("#delete-analysis");
const deleteDialog = document.querySelector("#delete-dialog");
const rowText = document.querySelector("#field-text");
const rowUrl = document.querySelector("#field-url");
const inputText = document.querySelector("#content-text");
const inputUrl = document.querySelector("#content-url");
const webSearchChoice = document.querySelector("#web-search-choice");
const webSearch = document.querySelector("#enable-web-search");
const pasteFallback = document.querySelector("#paste-article-fallback");

const analysisIdPattern = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;
const dateFormat = new Intl.DateTimeFormat("pl-PL", { dateStyle: "medium", timeStyle: "short" });
const verdictLabels = {
  confirmed: "Potwierdzone",
  false: "Fałszywe",
  misleading: "Mylące",
  insufficient_evidence: "Za mało dowodów",
  not_verifiable: "Nie da się zweryfikować",
};
const errorLabels = {
  invalid_input: "Sprawdź format danych i wklej pełny tekst lub poprawny adres artykułu.",
  needs_clarification: "Podaj pełne twierdzenie, które można sprawdzić w źródłach.",
  unsupported_media: "Podaj tekst lub adres artykułu.",
  source_unavailable: "Nie udało się pobrać materiału źródłowego. Spróbuj ponownie lub wklej fragment.",
  provider_rate_limited: "Przekroczono limit zapytań. Odczekaj chwilę i spróbuj ponownie.",
  model_unavailable: "Usługa analizy jest chwilowo niedostępna. Spróbuj ponownie za chwilę.",
  timeout: "Czas oczekiwania minął. Spróbuj ponownie za chwilę.",
  not_found: "Raport jest niedostępny lub wygasł.",
};
const fieldCodes = new Set(["invalid_input", "needs_clarification", "unsupported_media"]);
const pendingStates = new Set(["queued", "processing"]);
const deleteHeader = "X-Analysis-Delete-Token";
let currentAnalysisId = null;
let deletionGrant = null;
let submitting = false;
let deleting = false;

function element(tag, value, className) {
  const node = document.createElement(tag);
  if (value) node.textContent = value;
  if (className) node.className = className;
  return node;
}

function rows(value) {
  return Array.isArray(value) ? value : [];
}

function setStatus(message, isError = false) {
  status.textContent = message;
  status.classList.toggle("error", isError);
}

function setSubmitting(pending) {
  submitting = pending;
  submit.disabled = submitting || deleting;
}

function setDeletionGrant(grant) {
  deletionGrant = grant;
  deleteButton.hidden = grant === null;
}

function showError(code, input = null) {
  const message = errorLabels[code] || "Analiza się nie udała. Spróbuj ponownie.";
  if (input && fieldCodes.has(code)) {
    setStatus("");
    fieldError.textContent = message;
    input.setAttribute("aria-invalid", "true");
    input.focus();
  } else {
    setStatus(message, true);
  }
}

function clearFieldError() {
  fieldError.textContent = "";
  inputText.removeAttribute("aria-invalid");
  inputUrl.removeAttribute("aria-invalid");
}

function safeLink(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    if (["http:", "https:"].includes(url.protocol) && !url.username && !url.password) return url;
  } catch {
    // Invalid source URLs remain plain labels.
  }
  return null;
}

function timestamp(label, value) {
  const time = new Date(value);
  if (!value || Number.isNaN(time.getTime())) return element("p", label + " — brak daty", "minor");
  const line = element("p", label + " — ", "minor");
  const date = element("time", dateFormat.format(time));
  date.dateTime = time.toISOString();
  line.append(date);
  return line;
}

function sourceFlagsLabel(source) {
  const categories = {
    official: "Źródło urzędowe",
    major_media: "Duże medium",
    fact_check: "Serwis fact-checkingowy",
    other_media: "Inne medium",
  };
  const role = source.source_role === "core" ? "źródło główne" : "perspektywa dodatkowa";
  return (categories[source.source_category] || "Inne źródło") + " · " + role;
}

function sourceBadge(source) {
  const badge = element("span", sourceFlagsLabel(source), "source-flag");
  badge.dataset.category = source.source_category || "other_media";
  badge.dataset.role = source.source_role || "perspective";
  return badge;
}

function sourceReference(entry) {
  const container = element("span", null, "citation-reference");
  const link = element("a", entry.source.title || entry.source.publisher || "Źródło");
  link.href = "#" + entry.anchor;
  container.append(link, sourceBadge(entry.source));
  return container;
}

function appendList(title, items, render) {
  if (!items.length) return;
  reportContent.append(element("h3", title));
  const list = element("ul", null, "report-list");
  list.role = "list";
  list.append(...items.map(render));
  reportContent.append(list);
}

// Highlight only text spans returned by our extractor, never model-generated
// paraphrases. The claim card contains the full evidence and caveats.
function renderAnnotations(data) {
  if (typeof data.annotated_text !== "string" || !data.annotated_text) return;
  const original = data.annotated_text;
  const heading = element("h3", "Zaznaczone twierdzenia");
  const intro = element("p", "Najedź na zaznaczony fragment lub użyj klawiatury. Kliknij, aby przejść do analizy.", "minor");
  const block = element("div", null, "annotated-source");
  const spans = rows(data.claims)
    .filter((claim) => Number.isInteger(claim.span_start) && Number.isInteger(claim.span_end))
    .sort((a, b) => a.span_start - b.span_start);
  let cursor = 0;
  for (const claim of spans) {
    const start = claim.span_start;
    const end = claim.span_end;
    if (start < cursor || end > original.length || end <= start) continue;
    if (original.slice(start, end) !== claim.text) continue;
    block.append(document.createTextNode(original.slice(cursor, start)));
    const button = element("button", null, "inline-claim");
    button.type = "button";
    button.dataset.kind = claim.claim_type === "opinion" ? "opinion" :
      claim.claim_type === "uncertain" ? "uncertain" : claim.verdict;
    button.append(document.createTextNode(claim.text));
    const tooltip = element("span", null, "claim-tooltip");
    const kindLabel = claim.claim_type === "opinion" ? "Opinia" :
      claim.claim_type === "uncertain" ? "Niejasna klasyfikacja" :
      verdictLabels[claim.verdict] || "Nieokreślone";
    const explanation = [kindLabel,
      claim.evidence_scope === "private" ? "Wymaga prywatnych danych" : "",
      claim.rationale]
      .filter(Boolean).join(" — ");
    tooltip.textContent = explanation;
    tooltip.setAttribute("role", "tooltip");
    button.append(tooltip);
    button.setAttribute("aria-label", claim.text + ". " + explanation);
    button.addEventListener("click", () => {
      const target = document.getElementById("claim-detail-" + claim.claim_id);
      if (target) { target.scrollIntoView({ behavior: "smooth", block: "center" }); target.focus(); }
    });
    block.append(button);
    cursor = end;
  }
  block.append(document.createTextNode(original.slice(cursor)));
  reportContent.append(heading, intro, block);
}

function renderExecutiveSummary(data) {
  const claims = rows(data.claims);
  if (!claims.length) return;
  const card = element("section", null, "result-summary");
  card.append(element("h3", "Wniosek z analizy"));
  const sources = new Map(rows(data.sources).map((source) => [source.source_id, source]));
  const labels = {
    confirmed: "Potwierdzone w dostępnych źródłach",
    false: "Sprzeczne z dostępnym dowodem",
    misleading: "Mylące / brak istotnego kontekstu",
    insufficient_evidence: "Nie udało się rozstrzygnąć",
    not_verifiable: "Opinia lub twierdzenie nieweryfikowalne",
  };
  for (const claim of claims.slice(0, 3)) {
    const row = element("div", null, "summary-claim");
    const label = element("strong", labels[claim.verdict] || "Wynik nierozstrzygnięty");
    const statement = element("p", claim.text);
    row.append(label, statement);
    const source = rows(claim.evidence_source_ids).map((id) => sources.get(id)).find(Boolean);
    if (source) {
      const url = safeLink(source.url);
      if (url) {
        const link = element("a", "Zobacz cytowane źródło");
        link.href = url.href;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        row.append(link);
      }
      if (source.exact_quote) {
        row.append(element("blockquote", source.exact_quote));
      }
    } else {
      row.append(element("p", "Nie znaleziono pasującego cytatu potwierdzającego lub "
        + "obalającego tę tezę.", "minor"));
    }
    card.append(row);
  }
  if (claims.length > 3) {
    card.append(element("p", `Pozostałe ${claims.length - 3} twierdzenia opisano poniżej.`, "minor"));
  }
  reportContent.append(card);
}

function renderDiscovery(data) {
  const search = data.search;
  if (!search || typeof search !== "object") return;
  const header = element("h3", "Wyszukiwanie źródeł online");
  const line = element("p", "", "minor");
  const labels = {
    disabled: "Nie włączono wyszukiwania online.",
    skipped_no_public_claims: "Nie wyszukiwano: brak twierdzeń nadających się do publicznego sprawdzenia.",
    sufficient_local_sources: "Znaleziono co najmniej dwa lokalne źródła; pominięto nową kwerendę.",
    providers_partial: "Wyszukiwanie uruchomiono; część dostawców zgłosiła błąd.",
    completed: "Wyszukiwanie wykonane.",
  };
  line.textContent = (labels[search.status] || "Stan wyszukiwania nieznany.")
    + (search.attempted ? " Otrzymane publikacje: " + (search.candidates_found || 0) + "." : "")
    + (search.live_attempted ? " Sprawdzono oficjalne strony: "
      + search.live_attempted + "; z treścią: " + search.live_usable + "." : "");
  reportContent.append(header, line);
  const errors = rows(search.provider_errors);
  if (errors.length) {
    const list = element("ul", null, "limitations");
    list.append(...errors.map((error) => element("li", error.provider + ": " + error.code)));
    reportContent.append(list);
  }
}

function renderCoverage(data) {
  const coverage = data.coverage;
  if (!coverage || typeof coverage !== "object") return;
  const progress = element("p", `Pokrycie materiału: ${coverage.input_chars || 0} znaków, `
    + `${coverage.input_segments || 0} segmentów, `
    + `${coverage.extracted_claims || 0} twierdzeń, `
    + `${coverage.cited_claims || 0} twierdzeń z cytowanym dowodem.`, "minor");
  reportContent.append(element("h3", "Pokrycie analizy"), progress);
  if (coverage.partial_analysis) {
    reportContent.append(element("p", "ANALIZA CZĘŚCIOWA — nie wszystkie tezy zostały "
      + "sprawdzone lub brak dowodów. Znalezione publikacje nie są potwierdzeniem.", "minor"));
  }
  const header = element("h3", "Przegląd relacji mediów");
  const card = element("section", null, "coverage-overview");
  const evidence = rows(coverage.evidence_publishers);
  const topic = rows(coverage.topic_publishers);
  const stats = element("div", null, "coverage-stats");
  for (const [value, label] of [
    [evidence.length, "Wydawcy z zacytowanym dowodem"],
    [topic.length, "Wydawcy znalezieni dla tematu"],
  ]) {
    const cell = element("div", null, "coverage-stat");
    cell.append(element("strong", String(value)), element("span", label));
    stats.append(cell);
  }
  card.append(stats);
  const categories = [
    ["official", "Źródła urzędowe"],
    ["major_media", "Duże media"],
    ["fact_check", "Fact-check"],
    ["other_media", "Inne media — dodatkowa perspektywa"],
  ];
  const groups = coverage.evidence_by_category || {};
  const groupList = element("ul", null, "category-coverage");
  for (const [key, label] of categories) {
    const publishers = rows(groups[key]);
    const item = element("li");
    const badge = sourceBadge({
      source_category: key, source_role: key === "other_media" ? "perspective" : "core",
    });
    item.append(badge, element("span", publishers.length
      ? `${publishers.length} wydawców z cytatami: ${publishers.join(", ")}`
      : `${label}: brak cytowalnego dowodu w dostępnej bazie.`, "minor"));
    groupList.append(item);
  }
  card.append(groupList);
  if (coverage.note) card.append(element("p", coverage.note, "minor"));
  const citedFactChecks = rows(coverage.fact_check_evidence_publishers);
  if (citedFactChecks.length) {
    card.append(element("p", "Zacytowane serwisy fact-checkingowe: "
      + citedFactChecks.join(", ") + ". Każdą ocenę należy zestawić z oryginalnym dowodem.", "minor"));
  }
  const factChecks = rows(coverage.fact_check_candidates);
  if (factChecks.length) {
    card.append(element("h4", "Znalezione materiały fact-checkingowe (jeszcze nie zweryfikowane)"));
    const list = element("ul", null, "limitations");
    for (const entry of factChecks) {
      const li = element("li");
      const url = safeLink(entry.url);
      if (url) {
        const link = element("a", entry.title || entry.publisher);
        link.href = url.href;
        link.rel = "noreferrer";
        li.append(link);
      } else li.textContent = entry.title || entry.publisher;
      list.append(li);
    }
    card.append(list);
  }
  reportContent.append(header, card);
}

function renderPriorFactChecks(data) {
  const reviews = rows(data.fact_checks);
  const errors = rows(data.fact_check_errors);
  if (!reviews.length && !errors.length) return;
  reportContent.append(element("h3", "Opublikowane analizy fact-checkingowe"));
  reportContent.append(element("p", "Wyszukane oceny innych redakcji. Nie ustalono jeszcze, czy dotyczą dokładnie tej samej tezy. Nie są automatycznym dowodem ani oceną PRAWDOWID.", "minor"));
  const claims = new Map(rows(data.claims).map((c) => [c.claim_id, c.text]));
  const list = element("ul", null, "report-list");
  for (const entry of reviews) {
    const item = element("li");
    const url = safeLink(entry.url);
    if (url) {
      const anchor = element("a", entry.title || entry.publisher);
      anchor.href = url.href;
      anchor.rel = "noopener noreferrer";
      item.append(anchor);
    } else item.append(element("span", entry.title || entry.publisher));
    item.append(element("p", "Dotyczy wyszukania dla: " + (claims.get(entry.claim_id) || entry.claim_id), "minor"));
    if (entry.reviewed_claim) item.append(element("p", "Teza oceniana przez źródło: " + entry.reviewed_claim));
    if (entry.published_rating) item.append(element("p", "Ocena wydawcy: " + entry.published_rating, "minor"));
    item.append(element("p", entry.publisher + " · dopasowanie do tezy niepotwierdzone", "minor"));
    item.append(element("p", sourceFlagsLabel(entry), "minor"));
    list.append(item);
  }
  if (reviews.length) reportContent.append(list);
  if (errors.length) reportContent.append(element("p", "Część usług fact-checkingowych była niedostępna.", "minor"));
}

function showReport(data, shareCandidate = false) {
  const sources = rows(data.sources).map((source, index) => ({ source, anchor: "source-" + index }));
  const byId = new Map(sources.map((entry) => [entry.source.source_id, entry]));
  reportContent.replaceChildren();
  renderExecutiveSummary(data);
  renderAnnotations(data);
  renderDiscovery(data);

  appendList("Twierdzenia", rows(data.claims), (claim) => {
    const item = element("li");
    item.id = "claim-detail-" + claim.claim_id;
    item.tabIndex = -1;
    const heading = element("div", null, "claim-heading");
    const verdictLabel = claim.claim_type === "opinion" ? "Opinia" :
      claim.claim_type === "uncertain" ? "Klasyfikacja niepewna" :
      verdictLabels[claim.verdict] || "Brak oceny";
    const badge = element("span", verdictLabel, "verdict");
    badge.dataset.kind = claim.claim_type === "opinion" ? "opinion" :
      claim.claim_type === "uncertain" ? "uncertain" : claim.verdict;
    heading.append(element("h4", claim.text), badge);
    item.append(heading);
    if (claim.rationale) item.append(element("p", claim.rationale));
    const typeText = {
      factual: "Faktograficzne", opinion: "Opinia", fragment: "Fragment / brak tezy",
      uncertain: "Wymaga przeglądu klasyfikacji",
    }[claim.claim_type] || "Nieustalone";
    item.append(element("p", "Rodzaj: " + typeText, "minor"));
    if (claim.empirical_test) item.append(element("p", "Możliwy test: " + claim.empirical_test, "minor"));
    if (claim.classification_reason) item.append(element("p", "Uzasadnienie rodzaju: " + claim.classification_reason, "minor"));
    if (claim.classification_reviewed) item.append(element("p", "Klasyfikację poddano drugiej ocenie semantycznej.", "minor"));
    if (claim.evidence_scope === "private") {
      item.append(element("p", "Zakres: prywatny przypadek — brak publicznej weryfikacji.", "minor"));
    }
    if (rows(claim.aliases).length) {
      item.append(element("p", "Powiązane nazwy: " + claim.aliases.join(" · "), "minor"));
    }
    for (const assumption of rows(claim.assumptions)) {
      item.append(element("p", "Założenie / ograniczenie: " + assumption, "minor"));
    }
    const references = rows(claim.evidence_source_ids).map((id) => byId.get(id)).filter(Boolean);
    item.append(element("p", "Liczba zacytowanych fragmentów: " + references.length, "minor"));
    const discovered = Number(claim.topic_links_count) || 0;
    if (discovered) item.append(element("p", "Publikacje znalezione na podobny temat: "
      + discovered + " (nie są automatycznie dowodem).", "minor"));
    const searchState = rows(data.search?.per_claim).find((row) => row.claim_id === claim.claim_id);
    const stateLabels = {
      attempted: "Wykonano kwerendę online dla tej tezy.",
      sufficient_local_sources: "Wykorzystano dostępne źródła lokalne.",
      budget_exhausted: "Pominięto wyszukanie z powodu limitu kwerend.",
    };
    if (searchState) item.append(element("p", stateLabels[searchState.status] || "", "minor"));
    if (references.length) {
      const list = element("ul", null, "references");
      for (const entry of references) {
        const row = element("li");
        row.append(sourceReference(entry));
        list.append(row);
      }
      item.append(list);
    }
    return item;
  });

  appendList("Różnice w relacjach", rows(data.narratives), (frame) => {
    const item = element("li");
    const entry = byId.get(frame.source_id);
    const relatedClaim = rows(data.claims).find((claim) => claim.claim_id === frame.claim_id);
    if (relatedClaim) item.append(element("p", "Dotyczy tezy: " + relatedClaim.text, "minor"));
    const headline = rows(data.articles).find((article) => article.source_id === frame.source_id);
    if (headline?.headline) item.append(element("h4", headline.headline));
    if (entry) item.append(sourceReference(entry));
    for (const emphasis of rows(frame.emphasis)) item.append(element("blockquote", emphasis));
    if (frame.caveat) item.append(element("p", frame.caveat, "minor"));
    return item;
  });

  renderCoverage(data);
  renderPriorFactChecks(data);

  const foundCore = rows(data.discovered_links).filter((found) => found.source_role === "core");
  const foundOther = rows(data.discovered_links).filter((found) => found.source_role !== "core");
  const renderDiscovered = (found) => {
    const item = element("li");
    const title = found.title || found.publisher || "Artykuł";
    const url = safeLink(found.url);
    if (url) {
      const link = element("a", title);
      link.href = url.href;
      link.rel = "noreferrer";
      item.append(link);
    } else {
      item.append(element("span", title));
    }
    if (found.publisher) {
      const label = found.category === "fact_check" ? " — serwis fact-checkingowy" : "";
      item.append(element("p", found.publisher + label, "minor"));
      item.append(sourceBadge(found));
    }
    return item;
  };
  appendList("Wyszukane źródła główne (metadane, nie dowody)", foundCore, renderDiscovered);
  appendList("Inne media — dodatkowe perspektywy", foundOther, renderDiscovered);

  const renderSource = (entry) => {
    const source = entry.source;
    const item = element("li");
    item.id = entry.anchor;
    item.tabIndex = -1;
    const heading = element("h4");
    const url = safeLink(source.url);
    const title = source.title || source.publisher || "Źródło";
    if (url) {
      const link = element("a", title);
      link.href = url.href;
      link.rel = "noreferrer";
      heading.append(link);
    } else {
      heading.textContent = title;
    }
    item.append(heading);
    if (source.publisher) {
      const tag = source.source_type === "factcheck" ? " — fact-checking"
        : source.source_type === "primary" ? " — źródło pierwotne" : "";
      item.append(element("p", source.publisher + tag));
      item.append(sourceBadge(source));
    }
    item.append(timestamp("Publikacja", source.published_at), timestamp("Pobranie", source.fetched_at));
    if (source.exact_quote) item.append(element("blockquote", source.exact_quote));
    return item;
  };
  appendList("Źródła główne — cytaty", sources.filter((entry) => entry.source.source_role === "core"), renderSource);
  appendList("Inne media — cytaty i perspektywy", sources.filter((entry) => entry.source.source_role !== "core"), renderSource);

  const limitations = rows(data.limitations);
  if (limitations.length) {
    const list = element("ul", null, "limitations");
    list.append(...limitations.map((item) => element("li", item)));
    reportContent.append(element("h3", "Ograniczenia"), list);
  }
  if (!reportContent.childElementCount) {
    reportContent.append(element("p", "Raport nie zawiera twierdzeń ani źródeł."));
  }

  actions.hidden = !shareCandidate;
  share.hidden = false;
  publicLink.hidden = true;
  report.hidden = false;
  reportTitle.focus();
}

async function fetchJson(path, options = {}, receiveHeaders) {
  const response = await fetch(path, {
    ...options,
    signal: AbortSignal.timeout(options.timeout || 8000),
    cache: "no-store",
    redirect: "error",
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data?.detail?.error_code || "unavailable");
  if (!data || typeof data !== "object") throw new Error("unavailable");
  if (receiveHeaders) receiveHeaders(response.headers);
  return data;
}

async function poll(id, result) {
  const deadline = Date.now() + 240_000;
  for (let count = 0; pendingStates.has(result.status) && count < 90; count += 1) {
    if (currentAnalysisId !== id) return null;
    const delay = Math.min(4000, Math.max(500, (Number(result.retry_after_seconds) || 1) * 1000));
    if (Date.now() + delay >= deadline) break;
    if (!deleting) {
      setStatus(result.status === "queued" ? "Analiza czeka w kolejce…" : "Sprawdzanie źródeł…");
    }
    await new Promise((resolve) => setTimeout(resolve, delay));
    if (currentAnalysisId !== id) return null;
    result = await fetchJson("/api/v1/analyses/" + id, { timeout: Math.max(1, Math.min(8000, deadline - Date.now())) });
  }
  if (pendingStates.has(result.status)) throw new Error("timeout");
  return result;
}

function syncInput() {
  const isUrl = new FormData(form).get("kind") === "url";
  rowText.hidden = isUrl;
  rowUrl.hidden = !isUrl;
  inputText.disabled = isUrl;
  inputUrl.disabled = !isUrl;
  webSearchChoice.hidden = isUrl;
  webSearch.disabled = isUrl;
  clearFieldError();
}

pasteFallback.addEventListener("click", () => {
  const textMode = form.querySelector('input[name="kind"][value="text"]');
  textMode.checked = true;
  webSearch.checked = false; // Web search of pasted content requires fresh, explicit consent.
  syncInput();
  pasteFallback.hidden = true;
  inputText.focus();
  setStatus("Wklej treść artykułu. Bez jej pobrania adres nie jest dowodem.");
});

form.addEventListener("change", (event) => {
  if (event.target.name === "kind") syncInput();
});
form.addEventListener("input", clearFieldError);
form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (submit.disabled) return;
  const kind = new FormData(form).get("kind");
  const input = kind === "url" ? inputUrl : inputText;
  const content = input.value.trim();
  clearFieldError();
  if (!content || (kind === "url" && !safeLink(content))) {
    showError("invalid_input", input);
    return;
  }

  setSubmitting(true);
  pasteFallback.hidden = true;
  report.hidden = true;
  currentAnalysisId = null;
  setDeletionGrant(null);
  setStatus("Przesyłanie do analizy…");
  let submittedId = null;
  let deleteToken = null;
  try {
    let result = await fetchJson("/api/v1/analyses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        kind, content, language: "pl", web_search: kind === "text" && webSearch.checked,
      }),
    }, (headers) => { deleteToken = headers.get(deleteHeader); });
    if (typeof result.analysis_id !== "string" || !analysisIdPattern.test(result.analysis_id)) {
      throw new Error("unavailable");
    }
    submittedId = result.analysis_id;
    currentAnalysisId = submittedId;
    if (deleteToken?.trim()) setDeletionGrant({ id: submittedId, token: deleteToken });
    result = await poll(submittedId, result);
    if (currentAnalysisId !== submittedId || !result) return;
    if (result.status !== "completed" || !result.report) {
      throw new Error(result.error_code || result.status);
    }
    if (!deleting) setStatus(result.report.coverage?.partial_analysis
      ? "Analiza częściowa — sprawdź pokrycie i brakujące dowody."
      : "Raport jest gotowy.");
    showReport(result.report, kind === "url");
  } catch (error) {
    if (!deleting && (!submittedId || currentAnalysisId === submittedId)) {
      const code = error.name === "TimeoutError" ? "timeout" : error.message;
      showError(code, input);
      pasteFallback.hidden = !(kind === "url" && code === "source_unavailable");
    }
  } finally {
    setSubmitting(false);
  }
});

deleteButton.addEventListener("click", () => {
  if (!deletionGrant || deleting) return;
  deleteDialog.returnValue = "";
  deleteDialog.showModal();
});

deleteDialog.addEventListener("close", async () => {
  if (deleteDialog.returnValue !== "delete" || !deletionGrant || deleting) return;
  const { id, token } = deletionGrant;
  deleting = true;
  submit.disabled = true;
  deleteButton.disabled = true;
  setStatus("Usuwanie analizy…");
  try {
    const response = await fetch("/api/v1/analyses/" + id, {
      method: "DELETE",
      headers: { [deleteHeader]: token },
      mode: "same-origin",
      redirect: "error",
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) throw new Error("delete_failed");
    currentAnalysisId = null;
    setDeletionGrant(null);
    reportContent.replaceChildren();
    publicLink.removeAttribute("href");
    report.hidden = true;
    setStatus("Analiza została usunięta.");
    const input = inputUrl.disabled ? inputText : inputUrl;
    input.focus();
  } catch {
    setStatus("Nie udało się usunąć analizy. Spróbuj ponownie.", true);
  } finally {
    deleting = false;
    submit.disabled = submitting;
    deleteButton.disabled = false;
  }
});

share.addEventListener("click", async () => {
  if (!currentAnalysisId || deleting) return;
  const id = currentAnalysisId;
  share.disabled = true;
  setStatus("Sprawdzanie dostępności publicznego raportu…");
  try {
    await fetchJson("/api/v1/public-reports/" + id);
    if (id !== currentAnalysisId) return;
    publicLink.href = "/?report=" + id;
    publicLink.hidden = false;
    share.hidden = true;
    setStatus("Publiczny raport jest dostępny.");
    publicLink.focus();
  } catch {
    if (id === currentAnalysisId) setStatus("Ten raport nie jest dostępny publicznie.", true);
  } finally {
    share.disabled = false;
  }
});

async function loadPublicReport(id) {
  if (!analysisIdPattern.test(id)) {
    showError("not_found");
    return;
  }
  setSubmitting(true);
  setStatus("Otwieranie publicznego raportu…");
  try {
    const data = await fetchJson("/api/v1/public-reports/" + id);
    setStatus("Publiczny raport jest gotowy.");
    showReport(data.report || data);
  } catch (error) {
    showError(error.name === "TimeoutError" ? "timeout" : error.message);
  } finally {
    setSubmitting(false);
  }
}

syncInput();
const requestedReport = new URLSearchParams(location.search).get("report");
if (requestedReport !== null) loadPublicReport(requestedReport);
