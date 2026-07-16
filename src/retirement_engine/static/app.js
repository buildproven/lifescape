const state = {
  step: "profile",
  places: [],
  selected: new Set(),
  metricCount: 0,
  evidenceToken: null,
  hostedDemo: false,
  result: null,
};

const stepOrder = ["profile", "towns", "evidence", "results"];
const stepCopy = {
  profile: ["Step 1 of 4", "Shape the decision"],
  towns: ["Step 2 of 4", "Set the comparison field"],
  evidence: ["Step 3 of 4", "Review evidence readiness"],
  results: ["Step 4 of 4", "Read the decision"],
};

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) => String(value).replace(/[&<>'"]/g, (character) => ({
  "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;",
}[character]));
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("is-visible");
  window.clearTimeout(toast.timeout);
  toast.timeout = window.setTimeout(() => element.classList.remove("is-visible"), 3600);
}

function setStep(step) {
  const requestedIndex = stepOrder.indexOf(step);
  const currentIndex = stepOrder.indexOf(state.step);
  if (requestedIndex > currentIndex + 1 && !state.result) return;
  state.step = step;
  $$(".stage").forEach((element) => element.classList.toggle("is-active", element.dataset.stage === step));
  $$(".step-link").forEach((element, index) => {
    element.classList.toggle("is-active", element.dataset.stepTarget === step);
    element.classList.toggle("is-complete", index < requestedIndex);
    element.disabled = index > requestedIndex && !(step === "results" && state.result);
  });
  const [eyebrow, title] = stepCopy[step];
  $("#step-eyebrow").textContent = eyebrow;
  $("#step-title").textContent = title;
  $("#back-button").hidden = step === "profile" || (step === "results" && state.result);
  const next = $("#next-button");
  next.hidden = step === "results" && Boolean(state.result);
  next.disabled = false;
  if (step === "profile") next.innerHTML = "Choose towns <span>→</span>";
  if (step === "towns") next.innerHTML = "Review evidence <span>→</span>";
  if (step === "evidence") next.innerHTML = "Run comparison <span>→</span>";
  if (step === "towns") renderTowns();
  if (step === "evidence") renderEvidence();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function updateBudget() {
  const input = $("#budget");
  const percentage = ((Number(input.value) - Number(input.min)) / (Number(input.max) - Number(input.min))) * 100;
  input.style.setProperty("--range-progress", `${percentage}%`);
  $("#budget-output").textContent = money.format(Number(input.value));
}

function renderTowns() {
  const search = $("#town-search").value.trim().toLowerCase();
  const visible = state.places.filter((place) => `${place.name} ${place.state}`.toLowerCase().includes(search));
  $("#town-list").innerHTML = visible.map((place) => {
    const percentage = Math.round((place.complete_metrics / place.total_metrics) * 100);
    return `<label class="town-row">
      <input type="checkbox" value="${escapeHtml(place.place_id)}" ${state.selected.has(place.place_id) ? "checked" : ""}>
      <span class="checkmark" aria-hidden="true"></span>
      <span class="town-name"><strong>${escapeHtml(place.name)}</strong><span>${escapeHtml(place.state)}</span></span>
      <span class="town-source">${state.evidenceToken ? "Imported evidence" : "Demo evidence"}</span>
      <span class="town-readiness">${percentage}% ready</span>
    </label>`;
  }).join("") || "<p>No towns match this search.</p>";
  $$("#town-list input[type=checkbox]").forEach((input) => input.addEventListener("change", () => {
    if (input.checked) state.selected.add(input.value); else state.selected.delete(input.value);
    updateSelection();
  }));
  updateSelection();
}

function updateSelection() {
  $("#selected-count").textContent = state.selected.size;
  $("#toggle-all").textContent = state.selected.size === state.places.length ? "Clear all" : "Select all";
  $("#next-button").disabled = state.step === "towns" && state.selected.size < 2;
}

function renderEvidence() {
  const selectedPlaces = state.places.filter((place) => state.selected.has(place.place_id));
  const available = selectedPlaces.reduce((total, place) => total + place.complete_metrics, 0);
  const possible = selectedPlaces.length * state.metricCount;
  const readiness = possible ? Math.round((available / possible) * 100) : 0;
  $("#readiness-value").textContent = `${readiness}%`;
  $("#readiness-progress").style.strokeDashoffset = String(125.66 * (1 - readiness / 100));
  $("#evidence-list").innerHTML = selectedPlaces.map((place) => {
    const percentage = Math.round((place.complete_metrics / place.total_metrics) * 100);
    return `<div class="evidence-row">
      <strong>${escapeHtml(place.name)}, ${escapeHtml(place.state)}</strong>
      <span class="evidence-bar"><i style="width:${percentage}%"></i></span>
      <span>${place.complete_metrics}/${place.total_metrics}</span>
    </div>`;
  }).join("");
}

function renderInspector(place) {
  $("#result-inspector").innerHTML = `<div class="inspector-heading">
    <h3>${escapeHtml(place.name)}, ${escapeHtml(place.state)}</h3>
    <span>${place.top_three_frequency}% top-three</span>
  </div>
  <div class="criterion-list">
    ${place.criteria.slice(0, 8).map((criterion) => `<div class="criterion-row">
      <span>${escapeHtml(criterion.name)}</span><strong>${criterion.score}</strong>
      <span class="criterion-track"><i style="width:${Math.max(0, Math.min(100, criterion.score))}%"></i></span>
    </div>`).join("")}
  </div>
  <div class="gate-summary"><h4>Hard gates</h4>
    ${place.gates.map((gate) => `<div class="gate-chip"><span>${escapeHtml(gate.name)}</span><b>${escapeHtml(gate.state)}</b></div>`).join("")}
  </div>`;
  $$(".ranking-row").forEach((row) => row.classList.toggle("is-selected", row.dataset.placeId === place.place_id));
}

function renderResults(result) {
  state.result = result;
  $("#results-empty").hidden = true;
  $("#results-content").hidden = false;
  const lead = result.rankings[0];
  $("#result-lead").innerHTML = lead
    ? `<h2>${escapeHtml(lead.name)} leads this field.</h2><p>${result.rankings.length} towns cleared every hard gate; ${result.blocked.length} remain visible but unranked.</p>`
    : `<h2>No town cleared every hard gate.</h2><p>Review the blocked evidence below before changing constraints.</p>`;
  $("#ranking-list").innerHTML = result.rankings.map((place, index) => `<button class="ranking-row ${index === 0 ? "is-selected" : ""}" data-place-id="${escapeHtml(place.place_id)}" style="animation-delay:${index * 55}ms" type="button">
    <span class="rank">0${place.rank}</span>
    <span><strong>${escapeHtml(place.name)}</strong><small>${escapeHtml(place.state)} · ${place.fragile ? "Fragile" : "Stable"}</small></span>
    <span class="score">${place.score}</span>
    <span class="stability">${place.top_three_frequency}%<br>top 3</span>
  </button>`).join("");
  $$(".ranking-row").forEach((row) => row.addEventListener("click", () => {
    renderInspector(result.rankings.find((place) => place.place_id === row.dataset.placeId));
  }));
  if (lead) renderInspector(lead); else $("#result-inspector").innerHTML = "";
  $("#blocked-section").innerHTML = result.blocked.length ? `<h3>Blocked, not hidden</h3>${result.blocked.map((place) => `<div class="blocked-row">
    <strong>${escapeHtml(place.name)}, ${escapeHtml(place.state)}</strong>
    <p>${place.gates.map((gate) => `${escapeHtml(gate.name)}: ${escapeHtml(gate.state)}`).join(" · ")}</p>
  </div>`).join("")}` : "";
  $("#download-strip").innerHTML = Object.keys(result.downloads).length
    ? `<strong>Run ${escapeHtml(result.run_id)}</strong>
      <a href="${result.downloads["comparison.md"]}">Markdown report</a>
      <a href="${result.downloads["comparison.csv"]}">Ranking CSV</a>
      <a href="${result.downloads["sensitivity.csv"]}">Sensitivity CSV</a>
      <a href="${result.downloads["lifescape.sqlite"]}">SQLite provenance</a>`
    : `<strong>Hosted demonstration</strong>
      <span>Install Lifescape locally to import private evidence and save provenance.</span>`;
  const hasSynthetic = result.evidence_kind !== "real";
  $("#synthetic-notice").style.display = hasSynthetic ? "flex" : "none";
  if (hasSynthetic) {
    $("#synthetic-notice span").textContent = `${result.evidence_kind} evidence`;
    $("#synthetic-notice p").textContent = "This run contains synthetic values. Treat its results as test output, not purchase research.";
  }
  $("#back-button").hidden = true;
  $("#action-hint").textContent = state.hostedDemo
    ? "This synthetic run is temporary. Adjust the inputs to explore another field."
    : "This run is saved locally. Adjust the inputs to explore another field.";
  $("#next-button").hidden = false;
  $("#next-button").disabled = false;
  $("#next-button").innerHTML = "Adjust comparison <span>↺</span>";
  window.scrollTo({ top: 0, left: 0, behavior: "smooth" });
}

async function runComparison() {
  setStep("results");
  $("#next-button").disabled = true;
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        selected_place_ids: [...state.selected],
        purchase_budget_max: Number($("#budget").value),
        future_self_age: Number($("input[name=age]:checked").value),
        household: $("input[name=household]:checked").value,
        evidence_token: state.evidenceToken,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "The comparison could not run.");
    renderResults(payload);
  } catch (error) {
    toast(error.message);
    setStep("evidence");
  }
}

async function importEvidence(file) {
  const response = await fetch("/api/evidence/inspect", {
    method: "POST",
    headers: { "Content-Type": "text/csv" },
    body: file,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "This CSV could not be read.");
  state.evidenceToken = payload.evidence_token;
  state.places = payload.places;
  state.metricCount = payload.metric_count;
  state.selected = new Set(state.places.map((place) => place.place_id));
  $("#dataset-label").textContent = file.name;
  $("#dataset-meta").textContent = `${state.places.length} towns · ${payload.evidence_kind} evidence`;
  $("#synthetic-notice").classList.toggle("is-real", payload.evidence_kind === "real");
  $("#synthetic-notice span").textContent = payload.evidence_kind === "real" ? "Imported evidence" : `${payload.evidence_kind} evidence`;
  $("#synthetic-notice p").textContent = payload.evidence_kind === "real"
    ? "The engine will validate source policy, dates, ranges, and geography before scoring."
    : "This import contains synthetic values. Treat its results as test output, not purchase research.";
  renderTowns();
  toast(`Imported ${state.places.length} towns from ${file.name}`);
}

async function initialize() {
  try {
    const response = await fetch("/api/bootstrap");
    if (!response.ok) throw new Error("The local engine did not start correctly.");
    const payload = await response.json();
    state.hostedDemo = payload.mode === "hosted-demo";
    state.places = payload.places;
    state.metricCount = payload.metric_count;
    state.selected = new Set(state.places.map((place) => place.place_id));
    $("#budget").value = payload.defaults.purchase_budget_max;
    $("#dataset-meta").textContent = `${state.places.length} towns · ${state.metricCount} metrics`;
    if (!payload.allow_imports) {
      $("#import-button").hidden = true;
      $("#rail-note-copy").innerHTML = "<strong>Public demo</strong>Synthetic evidence only. Nothing personal is accepted or retained.";
    }
    updateBudget();
    renderTowns();
    window.setTimeout(() => $("#loading-screen").classList.add("is-hidden"), 350);
  } catch (error) {
    $("#loading-screen p").textContent = error.message;
  }
}

$("#budget").addEventListener("input", updateBudget);
$("#town-search").addEventListener("input", renderTowns);
$("#toggle-all").addEventListener("click", () => {
  if (state.selected.size === state.places.length) state.selected.clear();
  else state.selected = new Set(state.places.map((place) => place.place_id));
  renderTowns();
});
$("#next-button").addEventListener("click", () => {
  if (state.step === "profile") setStep("towns");
  else if (state.step === "towns") {
    if (state.selected.size < 2) toast("Select at least two towns."); else setStep("evidence");
  } else if (state.step === "evidence") runComparison();
  else if (state.step === "results") setStep("profile");
});
$("#back-button").addEventListener("click", () => setStep(stepOrder[stepOrder.indexOf(state.step) - 1]));
$$(".step-link").forEach((button) => button.addEventListener("click", () => setStep(button.dataset.stepTarget)));
$("#import-button").addEventListener("click", () => $("#evidence-file").click());
$("#evidence-file").addEventListener("change", async (event) => {
  const [file] = event.target.files;
  if (!file) return;
  if (file.size > 5000000) {
    toast("Evidence CSV exceeds the 5 MB local-app limit.");
    event.target.value = "";
    return;
  }
  try { await importEvidence(file); } catch (error) { toast(error.message); }
  event.target.value = "";
});

initialize();
