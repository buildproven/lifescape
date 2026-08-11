const state = {
  step: "profile",
  places: [],
  selected: new Set(),
  metricCount: 0,
  metrics: [],
  evidenceToken: null,
  researchPacket: null,
  researchPacketSelected: false,
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
const escapeHtml = (value) =>
  String(value).replace(
    /[&<>'"]/g,
    (character) =>
      ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        "'": "&#39;",
        '"': "&quot;",
      })[character]
  );
const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

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
  $$(".stage").forEach((element) =>
    element.classList.toggle("is-active", element.dataset.stage === step)
  );
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
  const percentage =
    ((Number(input.value) - Number(input.min)) / (Number(input.max) - Number(input.min))) * 100;
  input.style.setProperty("--range-progress", `${percentage}%`);
  $("#budget-output").textContent = money.format(Number(input.value));
}

function renderTowns() {
  const search = $("#town-search").value.trim().toLowerCase();
  const visible = state.places.filter((place) =>
    `${place.name} ${place.state}`.toLowerCase().includes(search)
  );
  $("#town-list").innerHTML =
    visible
      .map((place) => {
        const percentage = Math.round((place.complete_metrics / place.total_metrics) * 100);
        return `<label class="town-row">
      <input type="checkbox" value="${escapeHtml(place.place_id)}" ${state.selected.has(place.place_id) ? "checked" : ""}>
      <span class="checkmark" aria-hidden="true"></span>
      <span class="town-name"><strong>${escapeHtml(place.name)}</strong><span>${escapeHtml(place.state)}</span></span>
      <span class="town-source">${state.researchPacket ? "Generated lead" : state.evidenceToken ? "Imported evidence" : "Demo evidence"}</span>
      <span class="town-readiness">${percentage}% ready</span>
    </label>`;
      })
      .join("") || "<p>No towns match this search.</p>";
  $$("#town-list input[type=checkbox]").forEach((input) =>
    input.addEventListener("change", () => {
      if (input.checked) state.selected.add(input.value);
      else state.selected.delete(input.value);
      updateSelection();
    })
  );
  updateSelection();
}

function updateSelection() {
  $("#selected-count").textContent = state.selected.size;
  $("#toggle-all").textContent =
    state.selected.size === state.places.length ? "Clear all" : "Select all";
  $("#next-button").disabled = state.step === "towns" && state.selected.size < 2;
}

function renderEvidence() {
  const selectedPlaces = state.places.filter((place) => state.selected.has(place.place_id));
  const available = selectedPlaces.reduce((total, place) => total + place.complete_metrics, 0);
  const possible = selectedPlaces.length * state.metricCount;
  const readiness = possible ? Math.round((available / possible) * 100) : 0;
  $("#readiness-value").textContent = `${readiness}%`;
  $("#readiness-progress").style.strokeDashoffset = String(125.66 * (1 - readiness / 100));
  $("#evidence-list").innerHTML = selectedPlaces
    .map((place) => {
      const percentage = Math.round((place.complete_metrics / place.total_metrics) * 100);
      return `<div class="evidence-row">
      <strong>${escapeHtml(place.name)}, ${escapeHtml(place.state)}</strong>
      <span class="evidence-bar"><i style="width:${percentage}%"></i></span>
      <span>${place.complete_metrics}/${place.total_metrics}</span>
    </div>`;
    })
    .join("");
}

function renderInspector(place) {
  $("#result-inspector").innerHTML = `<div class="inspector-heading">
    <h3>${escapeHtml(place.name)}, ${escapeHtml(place.state)}</h3>
    <span>${place.top_three_frequency}% top-three</span>
  </div>
  <div class="criterion-list">
    ${place.criteria
      .slice(0, 8)
      .map(
        (criterion) => `<div class="criterion-row">
      <span>${escapeHtml(criterion.name)}</span><strong>${criterion.score}</strong>
      <span class="criterion-track"><i style="width:${Math.max(0, Math.min(100, criterion.score))}%"></i></span>
    </div>`
      )
      .join("")}
  </div>
  <div class="gate-summary"><h4>Hard gates</h4>
    ${place.gates.map((gate) => `<div class="gate-chip"><span>${escapeHtml(gate.name)}</span><b>${escapeHtml(gate.state)}</b></div>`).join("")}
  </div>`;
  $$(".ranking-row").forEach((row) =>
    row.classList.toggle("is-selected", row.dataset.placeId === place.place_id)
  );
}

function renderResults(result) {
  state.result = result;
  $("#results-empty").hidden = true;
  $("#results-content").hidden = false;
  const lead = result.rankings[0];
  $("#result-lead").innerHTML = lead
    ? `<h2>${escapeHtml(lead.name)} leads this field.</h2><p>${result.rankings.length} towns cleared every hard gate; ${result.blocked.length} remain visible but unranked.</p>`
    : `<h2>No town cleared every hard gate.</h2><p>Review the blocked evidence below before changing constraints.</p>`;
  $("#ranking-list").innerHTML = result.rankings
    .map(
      (
        place,
        index
      ) => `<button class="ranking-row ${index === 0 ? "is-selected" : ""}" data-place-id="${escapeHtml(place.place_id)}" style="animation-delay:${index * 55}ms" type="button">
    <span class="rank">0${place.rank}</span>
    <span><strong>${escapeHtml(place.name)}</strong><small>${escapeHtml(place.state)} · ${place.fragile ? "Fragile" : "Stable"}</small></span>
    <span class="score">${place.score}</span>
    <span class="stability">${place.top_three_frequency}%<br>top 3</span>
  </button>`
    )
    .join("");
  $$(".ranking-row").forEach((row) =>
    row.addEventListener("click", () => {
      renderInspector(result.rankings.find((place) => place.place_id === row.dataset.placeId));
    })
  );
  if (lead) renderInspector(lead);
  else $("#result-inspector").innerHTML = "";
  $("#blocked-section").innerHTML = result.blocked.length
    ? `<h3>Blocked, not hidden</h3>${result.blocked
        .map(
          (place) => `<div class="blocked-row">
    <strong>${escapeHtml(place.name)}, ${escapeHtml(place.state)}</strong>
    <p>${place.gates.map((gate) => `${escapeHtml(gate.name)}: ${escapeHtml(gate.state)}`).join(" · ")}</p>
  </div>`
        )
        .join("")}`
    : "";
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
    $("#synthetic-notice p").textContent =
      "This run contains synthetic values. Treat its results as test output, not purchase research.";
  }
  $("#back-button").hidden = true;
  $("#action-hint").textContent =
    "This run is saved locally. Adjust the inputs to explore another field.";
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
        research_packet_id: state.researchPacket ? state.researchPacket.packet_id : null,
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
  state.researchPacket = null;
  state.researchPacketSelected = false;
  state.places = payload.places;
  state.metricCount = payload.metric_count;
  state.selected = new Set(state.places.map((place) => place.place_id));
  $("#dataset-label").textContent = file.name;
  $("#dataset-meta").textContent =
    `${state.places.length} towns · ${payload.evidence_kind} evidence`;
  $("#synthetic-notice").classList.toggle("is-real", payload.evidence_kind === "real");
  $("#synthetic-notice span").textContent =
    payload.evidence_kind === "real" ? "Imported evidence" : `${payload.evidence_kind} evidence`;
  $("#synthetic-notice p").textContent =
    payload.evidence_kind === "real"
      ? "The engine will validate source policy, dates, ranges, and geography before scoring."
      : "This import contains synthetic values. Treat its results as test output, not purchase research.";
  renderTowns();
  toast(`Imported ${state.places.length} towns from ${file.name}`);
}

function renderDiscovery(packet) {
  state.researchPacket = packet;
  const selectedPacket = state.researchPacketSelected;
  const results = $("#discovery-results");
  const evidenceByPlace = new Map();
  packet.evidence.forEach((item) => {
    const items = evidenceByPlace.get(item.place.place_id) || [];
    items.push(item);
    evidenceByPlace.set(item.place.place_id, items);
  });
  const cards = packet.leads
    .map((lead) => {
      const observations = evidenceByPlace.get(lead.place_id) || [];
      const errors = packet.fetch_errors[lead.place_id] || [];
      const evidence = observations.length
        ? observations
            .map((item) => {
              const status =
                packet.evidence_status[item.place.place_id]?.[item.metric_id] || "awaiting_review";
              const reviewActions =
                status === "awaiting_review"
                  ? '<div class="review-actions"><label>Reviewer<input name="reviewer" autocomplete="name" placeholder="Your name" required /></label><button class="secondary-button" data-fetched-action="approve" type="button">Approve fetched record</button><button class="text-button" data-fetched-action="reject" type="button">Reject</button></div>'
                  : `<p class="evidence-status">Decision: ${escapeHtml(status)}</p>`;
              return `<div class="fetched-evidence" data-metric-id="${escapeHtml(item.metric_id)}">
                <div><strong>${escapeHtml(item.metric_id.replaceAll("_", " "))}</strong><span>${escapeHtml(String(item.raw_value))}</span></div>
                <small>${escapeHtml(item.source.title)} · ${escapeHtml(item.source.geography)} · observed ${escapeHtml(item.observed_at)} · <a href="${escapeHtml(item.source.url)}" target="_blank" rel="noreferrer">source</a></small>
                ${reviewActions}
              </div>`;
            })
            .join("")
        : `<p class="evidence-empty">No adapter observation is available yet.</p>${errors
            .map((error) => `<p class="evidence-error">${escapeHtml(error)}</p>`)
            .join("")}`;
      const finalistMetrics = Object.entries(packet.evidence_status[lead.place_id] || {})
        .filter(([, status]) => status === "finalist_verification")
        .map(([metric]) => metric.replaceAll("_", " "));
      return `<article class="discovery-card" data-place-id="${escapeHtml(lead.place_id)}">
        <label class="lead-select"><input type="checkbox" data-lead-select checked /><span>Select for evidence review</span></label>
        <h3>${escapeHtml(lead.name)}, ${escapeHtml(lead.state)}</h3>
        <p>${escapeHtml(lead.rationale)}</p>
        <small>${lead.unresolved_critical_metrics.length} critical facts still need verification</small>
        ${finalistMetrics.length ? `<p class="finalist-note">Finalist verification later: ${escapeHtml(finalistMetrics.join(", "))}</p>` : ""}
        <details class="research-review" open><summary>Fetched evidence review</summary>
          <p>Values below came from a public-source adapter. Approve or reject the record; do not retype its value.</p>
          ${evidence}
        </details>
      </article>`;
    })
    .join("");
  results.innerHTML = `<p class="discovery-disclosure">${escapeHtml(packet.disclosure)}</p>
    <div class="discovery-actions"><button class="secondary-button" data-research-action="fetch" type="button" ${selectedPacket ? "" : "disabled"}>Fetch available public evidence</button><button class="secondary-button" data-research-action="select" type="button">${selectedPacket ? "Continue with selected leads" : "Review selected leads"}</button></div>
    ${selectedPacket ? "" : '<p class="evidence-empty">Select the leads you want to compare before fetching public evidence.</p>'}
    ${cards}<section class="review-ledger"><h3>Review decisions</h3>${packet.reviews.length ? packet.reviews.map((review) => `<p><strong>${escapeHtml(review.decision)}</strong> · ${escapeHtml(review.metric_id.replaceAll("_", " "))} · ${escapeHtml(review.reviewer)}${review.reason ? ` — ${escapeHtml(review.reason)}` : ""}</p>`).join("") : "<p>No source records reviewed yet.</p>"}</section>`;
  $$(`[data-fetched-action]`).forEach((button) =>
    button.addEventListener("click", () => submitFetchedReview(button, packet))
  );
  $("[data-research-action=fetch]").addEventListener("click", () => fetchPacketEvidence(packet));
  $("[data-research-action=select]").addEventListener("click", () =>
    selectedPacket ? setStep("towns") : selectResearchLeads(packet)
  );
}

async function fetchPacketEvidence(packet) {
  const button = $("[data-research-action=fetch]");
  button.disabled = true;
  try {
    const response = await fetch("/api/research/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        packet_id: packet.packet_id,
        place_ids: packet.leads.map((lead) => lead.place_id),
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Public evidence could not be fetched.");
    renderDiscovery(payload);
    toast("Available public evidence fetched. Review each record before running.");
  } catch (error) {
    toast(error.message);
    button.disabled = false;
  }
}

async function selectResearchLeads(packet) {
  const placeIds = $$(`[data-lead-select]:checked`).map(
    (input) => input.closest(".discovery-card").dataset.placeId
  );
  if (placeIds.length < 2) {
    toast("Select at least two leads for evidence review.");
    return;
  }
  try {
    const response = await fetch("/api/research/select", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ packet_id: packet.packet_id, place_ids: placeIds }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "The selected leads could not be opened.");
    state.researchPacketSelected = true;
    const fetchResponse = await fetch("/api/research/fetch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        packet_id: payload.packet_id,
        place_ids: payload.leads.map((lead) => lead.place_id),
      }),
    });
    const fetched = await fetchResponse.json();
    if (!fetchResponse.ok) {
      throw new Error(fetched.detail || "Public evidence could not be fetched.");
    }
    state.researchPacket = fetched;
    state.evidenceToken = null;
    state.places = fetched.leads.map((lead) => ({
      place_id: lead.place_id,
      name: lead.name,
      state: lead.state,
      complete_metrics: Object.values(fetched.evidence_status[lead.place_id] || {}).filter(
        (status) => status === "approved"
      ).length,
      total_metrics: state.metricCount,
    }));
    state.selected = new Set(state.places.map((place) => place.place_id));
    $("#dataset-label").textContent = "Generated town leads";
    $("#dataset-meta").textContent = `${state.places.length} towns · evidence review`;
    renderTowns();
    renderDiscovery(fetched);
    toast("Selected leads opened. Review each fetched record before continuing.");
  } catch (error) {
    toast(error.message);
  }
}

async function submitFetchedReview(button, packet) {
  const card = button.closest(".discovery-card");
  const evidence = button.closest(".fetched-evidence");
  const reviewer = evidence.querySelector("[name=reviewer]").value.trim();
  const metricId = evidence.dataset.metricId;
  const rejecting = button.dataset.fetchedAction === "reject";
  const payload = rejecting
    ? {
        packet_id: packet.packet_id,
        reviewer,
        place: packet.leads.find((lead) => lead.place_id === card.dataset.placeId),
        metric_id: metricId,
        reason: "Reviewer rejected the fetched record after source review.",
      }
    : {
        packet_id: packet.packet_id,
        place_id: card.dataset.placeId,
        metric_id: metricId,
        reviewer,
      };
  button.disabled = true;
  try {
    const response = await fetch(
      rejecting ? "/api/research/reject" : "/api/research/approve-fetched",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }
    );
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "Review could not be recorded.");
    renderDiscovery(result);
    toast(
      rejecting ? "Rejection recorded. The metric remains unknown." : "Fetched evidence approved."
    );
  } catch (error) {
    toast(error.message);
    button.disabled = false;
  }
}

async function discoverCandidates() {
  const preferences = $("#discovery-preferences").value.trim();
  const examples = [
    $("#discovery-example-one").value.trim(),
    $("#discovery-example-two").value.trim(),
  ].filter(Boolean);
  if (preferences.length < 20) {
    toast("Describe the retirement life you want in at least a sentence.");
    return;
  }
  const button = $("#discover-button");
  button.disabled = true;
  button.textContent = "Finding research leads…";
  try {
    const response = await fetch("/api/research/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        preferences,
        exemplar_towns: examples,
        hard_constraints: [`Maximum purchase budget: ${money.format(Number($("#budget").value))}`],
        exclusions: [],
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.detail || "Discovery could not run.");
    state.researchPacketSelected = false;
    renderDiscovery(payload);
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.innerHTML = "Find research leads <span>↗</span>";
  }
}

async function initialize() {
  try {
    const response = await fetch("/api/bootstrap");
    if (!response.ok) throw new Error("The local engine did not start correctly.");
    const payload = await response.json();
    state.places = payload.places;
    state.metricCount = payload.metric_count;
    state.metrics = payload.metrics;
    state.selected = new Set(state.places.map((place) => place.place_id));
    $("#budget").value = payload.defaults.purchase_budget_max;
    $("#dataset-meta").textContent = `${state.places.length} towns · ${state.metricCount} metrics`;
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
    if (state.selected.size < 2) toast("Select at least two towns.");
    else setStep("evidence");
  } else if (state.step === "evidence") runComparison();
  else if (state.step === "results") setStep("profile");
});
$("#back-button").addEventListener("click", () =>
  setStep(stepOrder[stepOrder.indexOf(state.step) - 1])
);
$$(".step-link").forEach((button) =>
  button.addEventListener("click", () => setStep(button.dataset.stepTarget))
);
$("#import-button").addEventListener("click", () => $("#evidence-file").click());
$("#evidence-file").addEventListener("change", async (event) => {
  const [file] = event.target.files;
  if (!file) return;
  if (file.size > 5000000) {
    toast("Evidence CSV exceeds the 5 MB local-app limit.");
    event.target.value = "";
    return;
  }
  try {
    await importEvidence(file);
  } catch (error) {
    toast(error.message);
  }
  event.target.value = "";
});
$("#discover-button").addEventListener("click", discoverCandidates);

initialize();
