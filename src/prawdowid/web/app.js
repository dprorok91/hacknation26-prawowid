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

function sourceReference(entry) {
  const link = element("a", entry.source.title || entry.source.publisher || "Źródło");
  link.href = "#" + entry.anchor;
  return link;
}

function appendList(title, items, render) {
  if (!items.length) return;
  reportContent.append(element("h3", title));
  const list = element("ul", null, "report-list");
  list.role = "list";
  list.append(...items.map(render));
  reportContent.append(list);
}

function showReport(data, shareCandidate = false) {
  const sources = rows(data.sources).map((source, index) => ({ source, anchor: "source-" + index }));
  const byId = new Map(sources.map((entry) => [entry.source.source_id, entry]));
  reportContent.replaceChildren();

  appendList("Twierdzenia", rows(data.claims), (claim) => {
    const item = element("li");
    const heading = element("div", null, "claim-heading");
    const badge = element("span", verdictLabels[claim.verdict] || "Brak oceny", "verdict");
    badge.dataset.kind = claim.verdict;
    heading.append(element("h4", claim.text), badge);
    item.append(heading);
    if (claim.rationale) item.append(element("p", claim.rationale));
    const references = rows(claim.evidence_source_ids).map((id) => byId.get(id)).filter(Boolean);
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
    const headline = rows(data.articles).find((article) => article.source_id === frame.source_id);
    if (headline?.headline) item.append(element("h4", headline.headline));
    if (entry) item.append(sourceReference(entry));
    for (const emphasis of rows(frame.emphasis)) item.append(element("blockquote", emphasis));
    if (frame.caveat) item.append(element("p", frame.caveat, "minor"));
    return item;
  });

  appendList("Źródła", sources, (entry) => {
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
    if (source.publisher) item.append(element("p", source.publisher));
    item.append(timestamp("Publikacja", source.published_at), timestamp("Pobranie", source.fetched_at));
    if (source.exact_quote) item.append(element("blockquote", source.exact_quote));
    return item;
  });

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
  const deadline = Date.now() + 45_000;
  for (let count = 0; pendingStates.has(result.status) && count < 30; count += 1) {
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
  clearFieldError();
}

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
      body: JSON.stringify({ kind, content, language: "pl" }),
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
    if (!deleting) setStatus("Raport jest gotowy.");
    showReport(result.report, kind === "url");
  } catch (error) {
    if (!deleting && (!submittedId || currentAnalysisId === submittedId)) {
      showError(error.name === "TimeoutError" ? "timeout" : error.message, input);
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
