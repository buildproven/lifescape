/*
 * Lifescape local app: find places, shape a shortlist, then verify finalists with evidence.
 * PRD: docs/prd/lifescape-place-discovery.md (FR1-FR15). Discovery data is advisory; only the
 * evidence run (/api/run) can clear a gate.
 */
const Scenario = window.LifescapeScenario;

const state = {
  step: "feel",
  discovery: { available: false, fields: [] },
  evidence: { places: [], metricCount: 0, metricDetails: [], token: null, kind: "synthetic" },
  scenario: Scenario.emptyScenario(),
  exemplarMatches: [],
  manualMatches: [],
  extraSelected: new Set(),
  advancedOpen: false,
  searching: false,
  stale: false,
  problem: null,
  storageBlocked: false,
  result: null,
};

const stepOrder = ["feel", "limits", "matches", "shortlist", "verify", "results"];
const stepCopy = {
  feel: ["Step 1 of 5", "Tell us what feels right"],
  limits: ["Step 2 of 5", "Set your boundaries"],
  matches: ["Step 3 of 5", "Explore your matches"],
  shortlist: ["Step 4 of 5", "Shape your shortlist"],
  verify: ["Step 5 of 5", "Verify your finalists"],
  results: ["Step 5 of 5", "Read the decision"],
};
const railStep = { results: "verify" };
const DECISION_LABELS = { keep: "Keep", reject: "Not for me", unsure: "Unsure" };
const PRIORITY_LABELS = [
  "",
  "1 · Nice to have",
  "2 · Somewhat",
  "3 · Matters",
  "4 · Important",
  "5 · Essential",
];

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHtml = (value) =>
  String(value).replace(
    /[&<>'"]/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" })[character]
  );
const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});
const whole = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

const scenario = () => state.scenario;
const fieldMeta = (field) => state.discovery.fields.find((item) => item.field === field);
const fold = (text) =>
  String(text).normalize("NFKD").replace(/[̀-ͯ]/g, "").replace(/\s+/g, " ").trim().toLowerCase();

function formatValue(unit, value) {
  if (value === null || value === undefined) return "Missing";
  if (unit === "USD") return money.format(value);
  if (unit.startsWith("percent")) return `${Number(value).toFixed(1)}%`;
  if (unit === "people per square mile") return `${whole.format(value)} people per sq mi`;
  return whole.format(value);
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("is-visible");
  window.clearTimeout(toast.timeout);
  toast.timeout = window.setTimeout(() => element.classList.remove("is-visible"), 4200);
}

function setHint(message) {
  $("#action-hint").textContent = message;
}

/* ---------- persistence ---------- */

function persist() {
  try {
    Scenario.save(window.localStorage, scenario());
  } catch (error) {
    if (!state.storageBlocked) {
      state.storageBlocked = true;
      showBanner(
        "Not saved",
        `Your browser blocked local storage, so this search will not survive a reload. ${error.message}`
      );
    }
  }
}

function showBanner(title, message, actions = []) {
  const banner = $("#scenario-banner");
  banner.hidden = false;
  banner.innerHTML = `<span>${escapeHtml(title)}</span><p>${escapeHtml(message)}</p>`;
  for (const action of actions) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "text-button";
    button.textContent = action.label;
    button.addEventListener("click", action.run);
    banner.append(button);
  }
}

function downloadJson(text, filename) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function restoreScenario() {
  const loaded = Scenario.load(window.localStorage);
  state.scenario = loaded.scenario;
  if (loaded.blocked) {
    state.storageBlocked = true;
    showBanner("Not saved", `This browser blocks local storage. ${loaded.problem}`);
    return;
  }
  if (loaded.problem) {
    state.problem = loaded.problem;
    showBanner(
      "Saved search could not be opened",
      `${loaded.problem}. Nothing was partly loaded and a backup copy was kept in this browser.`,
      [
        {
          label: "Download backup JSON",
          run: () => downloadJson(loaded.rawText ?? "", "lifescape-backup.json"),
        },
        { label: "Start fresh", run: resetEverything },
      ]
    );
  }
  const result = scenario().result;
  state.stale =
    Boolean(result) &&
    state.discovery.available &&
    result.catalog_version !== state.discovery.catalog_version;
}

function resetEverything() {
  Scenario.reset(window.localStorage);
  state.scenario = Scenario.emptyScenario();
  state.problem = null;
  state.stale = false;
  state.exemplarMatches = [];
  $("#scenario-banner").hidden = true;
  renderAll();
  setStep("feel");
  toast("Search cleared. Starting fresh.");
}

/* ---------- profile ---------- */

const profile = () => scenario().profile;
const hasExampleValue = (field) =>
  profile().exemplars.some((exemplar) => exemplar.values?.[field] != null);
const modeFor = (field) => {
  if (profile().modes?.[field] === "custom") return "custom";
  return hasExampleValue(field) ? "example" : "off";
};

function resolvedTargets() {
  return state.discovery.fields
    .filter((item) => modeFor(item.field) !== "off")
    .map((item) => ({
      field: item.field,
      source: modeFor(item.field) === "custom" ? "user" : "exemplar",
    }));
}

function buildRequest() {
  const body = {
    exemplars: profile().exemplars.map((item) => item.place_id),
    targets: {},
    priorities: {},
    hard_constraints: [],
  };
  for (const target of resolvedTargets()) {
    if (target.source === "user") body.targets[target.field] = profile().targets[target.field];
    body.priorities[target.field] = profile().priorities[target.field] ?? 3;
  }
  for (const item of state.discovery.fields) {
    const limit = profile().limits[item.field] ?? {};
    for (const operator of ["min", "max"]) {
      if (limit[operator] !== undefined && limit[operator] !== null) {
        body.hard_constraints.push({ field: item.field, operator, value: limit[operator] });
      }
    }
  }
  if (profile().regions.length) body.include_regions = [...profile().regions];
  if (profile().states.length) body.exclude_states = [...profile().states];
  const rejected = Object.entries(scenario().decisions)
    .filter(([, decision]) => decision === "reject")
    .map(([placeId]) => placeId);
  if (rejected.length) body.exclude_places = rejected;
  return body;
}

function updateTargetSummary() {
  const count = resolvedTargets().length;
  const ready = count >= 2;
  $("#target-summary").textContent = ready
    ? `Ready: searching on ${count} qualities.`
    : `Choose an example town or set at least two qualities to search. Currently ${count}.`;
  $("#target-summary").classList.toggle("is-ready", ready);
  if (state.step === "feel" || state.step === "limits") {
    $("#next-button").disabled = !ready;
    setHint(ready ? "" : "Search needs at least two qualities to compare.");
  }
  return ready;
}

/* ---------- lookup ---------- */

async function lookup(query, limit = 8) {
  const response = await fetch(`/api/places?query=${encodeURIComponent(query)}&limit=${limit}`);
  const payload = await response.json();
  if (!response.ok)
    throw new Error(typeof payload.detail === "string" ? payload.detail : "Lookup failed.");
  return payload.places;
}

function debounce(callback, delay = 220) {
  let timer = 0;
  return (...args) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => callback(...args), delay);
  };
}

function placeRow(place, actionLabel, disabledReason) {
  const population =
    place.population === null ? "population unknown" : `${whole.format(place.population)} people`;
  const note = disabledReason
    ? `<small class="lookup-note">${escapeHtml(disabledReason)}</small>`
    : "";
  return `<div class="lookup-row">
    <span><strong>${escapeHtml(place.label)}</strong><small>${escapeHtml(population)}</small>${note}</span>
    <button class="secondary-button" type="button" data-place-id="${escapeHtml(place.place_id)}" ${disabledReason ? "disabled" : ""}>${escapeHtml(actionLabel)}</button>
  </div>`;
}

async function searchExemplars() {
  const query = $("#exemplar-search").value.trim();
  const box = $("#exemplar-results");
  if (query.length < 2) {
    state.exemplarMatches = [];
    box.innerHTML = "";
    return;
  }
  try {
    state.exemplarMatches = await lookup(query);
  } catch (error) {
    box.innerHTML = `<p class="lookup-empty">${escapeHtml(error.message)}</p>`;
    return;
  }
  box.innerHTML = state.exemplarMatches.length
    ? state.exemplarMatches
        .map((place) =>
          placeRow(
            place,
            "Use as example",
            place.serving_eligible
              ? profile().exemplars.length >= 2
                ? "You already chose two examples."
                : ""
              : "Examples need a population of 2,500 or more."
          )
        )
        .join("")
    : `<p class="lookup-empty">No U.S. town matches “${escapeHtml(query)}”.</p>`;
  $$("#exemplar-results button").forEach((button) =>
    button.addEventListener("click", () => addExemplar(button.dataset.placeId))
  );
}

function addExemplar(placeId) {
  const place = state.exemplarMatches.find((item) => item.place_id === placeId);
  if (!place || profile().exemplars.length >= 2) return;
  if (profile().exemplars.some((item) => item.place_id === placeId)) return;
  profile().exemplars.push({ place_id: place.place_id, label: place.label, values: place.values });
  $("#exemplar-search").value = "";
  $("#exemplar-results").innerHTML = "";
  state.exemplarMatches = [];
  persist();
  renderFeel();
  toast(`${place.label} added as an example.`);
}

function removeExemplar(placeId) {
  profile().exemplars = profile().exemplars.filter((item) => item.place_id !== placeId);
  persist();
  renderFeel();
}

/* ---------- step 1 and 2 rendering ---------- */

function renderFeel() {
  $("#exemplar-chips").innerHTML = profile()
    .exemplars.map(
      (item) => `<span class="chip"><span>${escapeHtml(item.label)}</span>
      <button type="button" data-remove="${escapeHtml(item.place_id)}" aria-label="Remove ${escapeHtml(item.label)}">×</button></span>`
    )
    .join("");
  $$("#exemplar-chips button").forEach((button) =>
    button.addEventListener("click", () => removeExemplar(button.dataset.remove))
  );
  renderQualities();
  updateTargetSummary();
}

function qualityRow(item) {
  const field = item.field;
  const exemplarValues = profile().exemplars.filter((exemplar) => exemplar.values?.[field] != null);
  const mode = modeFor(field);
  const lower = item.lower;
  const upper = item.upper;
  const target = profile().targets[field] ?? (lower + upper) / 2;
  const step = Math.max((upper - lower) / 200, 0.01);
  const options = [];
  if (exemplarValues.length) options.push(["example", "Like my example"]);
  options.push(["custom", "A value I choose"]);
  if (!exemplarValues.length) options.unshift(["off", "Not part of my search"]);
  const exampleText = exemplarValues
    .map((exemplar) => `${exemplar.label}: ${formatValue(item.unit, exemplar.values[field])}`)
    .join(" · ");
  const active = mode !== "off";
  return `<div class="quality-row" data-field="${escapeHtml(field)}">
    <div class="quality-head"><strong>${escapeHtml(item.label)}</strong>
      <small>${escapeHtml(exampleText || item.unit)}</small></div>
    <label class="mini-field"><span>Aim for</span>
      <select data-role="mode">${options
        .map(
          ([value, label]) =>
            `<option value="${value}" ${mode === value ? "selected" : ""}>${label}</option>`
        )
        .join("")}</select></label>
    <div class="mini-field custom-target" ${mode === "custom" ? "" : "hidden"}>
      <label><span>Target</span>
        <input type="range" data-role="target" min="${lower}" max="${upper}" step="${step}" value="${target}"
          aria-label="${escapeHtml(item.label)} target"></label>
      <output data-role="target-output">${escapeHtml(formatValue(item.unit, Number(target)))}</output>
    </div>
    <label class="mini-field" ${active ? "" : "hidden"}><span>How much it matters</span>
      <select data-role="priority">${[1, 2, 3, 4, 5]
        .map(
          (weight) =>
            `<option value="${weight}" ${(profile().priorities[field] ?? 3) === weight ? "selected" : ""}>${PRIORITY_LABELS[weight]}</option>`
        )
        .join("")}</select></label>
  </div>`;
}

function renderQualities() {
  const list = $("#quality-list");
  list.innerHTML = state.discovery.fields.map(qualityRow).join("");
  $$("#quality-list .quality-row").forEach((row) => {
    const field = row.dataset.field;
    const item = fieldMeta(field);
    row.querySelector("[data-role=mode]").addEventListener("change", (event) => {
      const modes = { ...profile().modes };
      if (event.target.value === "custom") modes[field] = "custom";
      else delete modes[field];
      profile().modes = modes;
      if (event.target.value === "custom" && profile().targets[field] === undefined) {
        profile().targets[field] = (item.lower + item.upper) / 2;
      }
      persist();
      renderQualities();
      updateTargetSummary();
    });
    row.querySelector("[data-role=target]").addEventListener("input", (event) => {
      profile().targets[field] = Number(event.target.value);
      row.querySelector("[data-role=target-output]").textContent = formatValue(
        item.unit,
        Number(event.target.value)
      );
      persist();
    });
    row.querySelector("[data-role=priority]").addEventListener("change", (event) => {
      profile().priorities[field] = Number(event.target.value);
      persist();
    });
  });
}

function renderLimits() {
  $("#limit-list").innerHTML = state.discovery.fields
    .map((item) => {
      const limit = profile().limits[item.field] ?? {};
      const input = (operator, label) => `<label class="mini-field"><span>${label}</span>
        <input type="number" inputmode="decimal" min="0" step="any" data-field="${escapeHtml(item.field)}" data-operator="${operator}"
          value="${limit[operator] ?? ""}" aria-label="${escapeHtml(item.label)} ${label.toLowerCase()}"></label>`;
      return `<div class="limit-row"><strong>${escapeHtml(item.label)}<small>${escapeHtml(item.unit)}</small></strong>
        ${input("min", "At least")}${input("max", "No more than")}</div>`;
    })
    .join("");
  $$("#limit-list input").forEach((input) =>
    input.addEventListener("input", () => {
      const field = input.dataset.field;
      const next = { ...profile().limits[field] };
      if (input.value === "") delete next[input.dataset.operator];
      else next[input.dataset.operator] = Number(input.value);
      profile().limits[field] = next;
      persist();
    })
  );
  $("#region-choices").innerHTML = state.discovery.regions
    .map(
      (region) =>
        `<label><input type="checkbox" value="${escapeHtml(region)}" ${profile().regions.includes(region) ? "checked" : ""}><span>${escapeHtml(region)}</span></label>`
    )
    .join("");
  $$("#region-choices input").forEach((input) =>
    input.addEventListener("change", () => {
      profile().regions = $$("#region-choices input:checked").map((item) => item.value);
      persist();
    })
  );
  const select = $("#state-exclude");
  select.innerHTML = `<option value="">Choose a state to skip…</option>${state.discovery.states
    .filter((code) => !profile().states.includes(code))
    .map((code) => `<option value="${escapeHtml(code)}">${escapeHtml(code)}</option>`)
    .join("")}`;
  $("#state-chips").innerHTML = profile()
    .states.map(
      (code) => `<span class="chip"><span>${escapeHtml(code)}</span>
      <button type="button" data-remove="${escapeHtml(code)}" aria-label="Stop skipping ${escapeHtml(code)}">×</button></span>`
    )
    .join("");
  $$("#state-chips button").forEach((button) =>
    button.addEventListener("click", () => {
      profile().states = profile().states.filter((code) => code !== button.dataset.remove);
      persist();
      renderLimits();
    })
  );
}

/* ---------- search ---------- */

function describeApiError(payload) {
  if (typeof payload.detail === "string") return payload.detail;
  if (Array.isArray(payload.detail)) {
    return payload.detail
      .map((item) => `${(item.loc ?? []).slice(1).join(".")}: ${item.msg}`)
      .join("; ");
  }
  return "The search could not run.";
}

function changedInputs(previous, next) {
  if (!previous) return [];
  const changes = [];
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  if (!same(previous.exemplars, next.exemplars)) changes.push("example towns");
  for (const item of state.discovery.fields) {
    if (!same(previous.targets?.[item.field], next.targets?.[item.field]))
      changes.push(`${item.label} target`);
    if (!same(previous.priorities?.[item.field], next.priorities?.[item.field]))
      changes.push(`${item.label} priority`);
  }
  const constraintKey = (body) =>
    JSON.stringify((body.hard_constraints ?? []).map((c) => [c.field, c.operator, c.value]));
  if (constraintKey(previous) !== constraintKey(next)) changes.push("limits");
  if (
    !same(previous.include_regions, next.include_regions) ||
    !same(previous.exclude_states, next.exclude_states)
  ) {
    changes.push("regions or states");
  }
  if (!same(previous.exclude_places, next.exclude_places)) changes.push("towns marked Not for me");
  return changes;
}

async function runSearch() {
  if (state.searching || !updateTargetSummary()) return;
  const body = buildRequest();
  state.searching = true;
  $("#next-button").disabled = true;
  setHint("Finding places…");
  try {
    const response = await fetch("/api/place-recommendations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(describeApiError(payload));
    const previous = scenario().result;
    scenario().previous_ranks = Object.fromEntries(
      (previous?.recommendations ?? []).map((item) => [item.place_id, item.rank])
    );
    scenario().movement = {
      changes: changedInputs(scenario().last_request, body),
      had_previous: Boolean(previous),
    };
    scenario().last_request = body;
    scenario().result = payload;
    state.stale = false;
    persist();
    renderMatches();
    setStep("matches");
  } catch (error) {
    toast(error.message);
    setHint(error.message);
    showBanner("Search could not run", error.message);
  } finally {
    state.searching = false;
    if (state.step === "limits") $("#next-button").disabled = !updateTargetSummary();
  }
}

/* ---------- matches ---------- */

function decisionFor(placeId) {
  return scenario().decisions[placeId] ?? null;
}

function movementText(item) {
  const info = scenario().movement;
  if (!info?.had_previous) return "";
  const before = scenario().previous_ranks[item.place_id];
  const because = info.changes.length ? ` because you changed ${info.changes.join(", ")}` : "";
  if (before === undefined) return `New in this search${because}.`;
  if (before === item.rank) return "Same position as your last search.";
  const moved = Math.abs(before - item.rank);
  const direction = before > item.rank ? "up" : "down";
  return `Moved ${direction} ${moved} ${moved === 1 ? "place" : "places"}${because}.`;
}

function whyPanel(item) {
  const rows = item.fields
    .map((detail) => {
      const examples = detail.exemplar_values
        .map(
          (example) =>
            `${escapeHtml(example.name)}: ${escapeHtml(formatValue(detail.unit, example.value))}`
        )
        .join("<br>");
      const target =
        detail.user_target === null
          ? "—"
          : escapeHtml(formatValue(detail.unit, detail.user_target));
      return `<tr><th scope="row">${escapeHtml(detail.label)}</th>
        <td>${escapeHtml(formatValue(detail.unit, detail.candidate_value))}</td>
        <td>${examples || "—"}</td><td>${target}</td>
        <td><span class="tag ${detail.status === "missing" ? "is-missing" : ""}">${escapeHtml(detail.status === "missing" ? "Missing" : "Discovery data")}</span>
        <span class="tag">${escapeHtml(detail.evidence_status)}</span></td></tr>`;
    })
    .join("");
  const clipped = item.components
    .filter((component) => component.candidate_clipped || component.target_clipped)
    .map(
      (
        component
      ) => `<li>${escapeHtml(component.label)}: raw value ${escapeHtml(formatValue(component.unit, component.candidate_value))}
      against a catalog range of ${escapeHtml(formatValue(component.unit, component.lower_bound))} to
      ${escapeHtml(formatValue(component.unit, component.upper_bound))}. Similarity at the catalog edge is not an exact raw-value match.</li>`
    )
    .join("");
  const unknown = item.unknown_constraints.length
    ? `<p><strong>Needs verification:</strong> ${escapeHtml(item.unknown_constraints.join(", "))} cannot be checked because this value is unknown.</p>`
    : "";
  return `<div class="why-panel">
    <div class="table-wrap"><table><thead><tr><th scope="col">Quality</th><th scope="col">This town</th><th scope="col">Your examples</th>
      <th scope="col">Your target</th><th scope="col">Status</th></tr></thead><tbody>${rows}</tbody></table></div>
    ${clipped ? `<ul class="clip-notes">${clipped}</ul>` : ""}
    ${unknown}
    <p class="field-help">Catalog ${escapeHtml(item.catalog_version)}, data from ${escapeHtml(item.data_date)}.
      ${item.component_count} of ${item.profile_target_count} requested qualities had data.</p>
  </div>`;
}

function matchCard(item, { snapshot = false } = {}) {
  const decision = decisionFor(item.place_id);
  const reasons = item.reasons.map((reason) => `<li>${escapeHtml(reason.text)}</li>`).join("");
  const difference = item.differences[0];
  const unknown = item.unknown_constraints.length
    ? `<p class="tag-line"><span class="tag is-missing">Needs verification</span> ${escapeHtml(item.unknown_constraints.length)} limit${item.unknown_constraints.length === 1 ? "" : "s"} could not be checked.</p>`
    : "";
  const missing = item.missing_fields.length
    ? `<p class="field-help">Missing data: ${escapeHtml(item.missing_fields.map((field) => fieldMeta(field)?.label ?? field).join(", "))}.</p>`
    : "";
  const buttons = Object.entries(DECISION_LABELS)
    .map(
      ([value, label]) =>
        `<button type="button" class="decision-button" data-decision="${value}" aria-pressed="${decision === value}">${label}</button>`
    )
    .join("");
  return `<article class="match-card ${decision === "reject" ? "is-rejected" : ""}" data-place-id="${escapeHtml(item.place_id)}">
    <header><span class="rank">${snapshot ? "Kept" : String(item.rank).padStart(2, "0")}</span>
      <h3>${escapeHtml(item.label)}</h3>
      <span class="match-score" aria-label="${item.match_percent} percent discovery match">${item.match_percent}% match</span></header>
    <p class="card-label">Why it appeared</p>
    <ul class="reason-list">${reasons}</ul>
    <p class="card-label">Biggest trade-off</p>
    <p class="trade-off">${escapeHtml(difference?.text ?? "No trade-off was recorded.")}</p>
    ${unknown}${missing}
    <p class="movement">${escapeHtml(movementText(item))}</p>
    <div class="decision-row" role="group" aria-label="Your decision for ${escapeHtml(item.label)}">${buttons}
      <button type="button" class="text-button why-toggle" aria-expanded="false">Why this place?</button></div>
    <div class="why-slot" hidden>${whyPanel(item)}</div>
  </article>`;
}

function bindCard(container, items) {
  $$(`${container} .match-card`).forEach((card) => {
    const item = items.find((entry) => entry.place_id === card.dataset.placeId);
    if (!item) return;
    card
      .querySelectorAll(".decision-button")
      .forEach((button) =>
        button.addEventListener("click", () =>
          setDecision(item, button.dataset.decision, container)
        )
      );
    const toggle = card.querySelector(".why-toggle");
    toggle.addEventListener("click", () => {
      const slot = card.querySelector(".why-slot");
      slot.hidden = !slot.hidden;
      toggle.setAttribute("aria-expanded", String(!slot.hidden));
      toggle.textContent = slot.hidden ? "Why this place?" : "Hide details";
    });
  });
}

function syncDecisionButtons(item) {
  const decision = decisionFor(item.place_id);
  $$(`.match-card[data-place-id="${CSS.escape(item.place_id)}"]`).forEach((card) => {
    card.classList.toggle("is-rejected", decision === "reject");
    card
      .querySelectorAll(".decision-button")
      .forEach((button) =>
        button.setAttribute("aria-pressed", String(button.dataset.decision === decision))
      );
  });
}

function setDecision(item, decision, container = "#match-list") {
  const current = decisionFor(item.place_id);
  const next = current === decision ? null : decision;
  if (next === null) delete scenario().decisions[item.place_id];
  else scenario().decisions[item.place_id] = next;
  scenario().shortlist = scenario().shortlist.filter(
    (entry) => !(entry.place_id === item.place_id && entry.source === "recommendation")
  );
  if (next === "keep") {
    scenario().shortlist.push({
      place_id: item.place_id,
      label: item.label,
      name: item.name,
      state: item.state,
      source: "recommendation",
      added_at: new Date().toISOString(),
      recommendation: item,
    });
  }
  persist();
  if (container === "#match-list") {
    // Update in place so keyboard focus and any open "Why this place?" panel stay put.
    syncDecisionButtons(item);
    $("#kept-count").textContent = scenario().shortlist.length;
    updateNav();
  } else {
    renderMatches();
    renderShortlist();
    updateNav();
    $("#shortlist-heading").focus();
  }
  if (next === "reject") toast(`${item.label} will be left out of your next search.`);
}

function renderMatches() {
  const result = scenario().result;
  $("#kept-count").textContent = scenario().shortlist.length;
  $("#stale-banner").hidden = !state.stale;
  if (state.stale) {
    $("#stale-copy").textContent =
      `These results use catalog ${result.catalog_version}; this app now has ${state.discovery.catalog_version}. They stay readable and are not rescored.`;
  }
  if (!result) {
    $("#match-summary").textContent = "Run a search to see towns.";
    $("#match-list").innerHTML = "";
    return;
  }
  const d = result.diagnostics;
  const excluded = d.excluded_any_constraint_count;
  $("#match-summary").textContent =
    `${d.returned_count} of ${whole.format(d.recommendable_count)} towns that fit. ` +
    `${whole.format(excluded)} removed by your limits; ${whole.format(d.insufficient_match_data_count)} lacked enough data` +
    (d.returned_count < 10
      ? ". Fewer than 10 towns fit this search, so every fitting town is shown."
      : ".");
  const examples = result.profile.exemplars.map((item) => item.label).join(" and ");
  const target = result.profile.targets.length;
  $("#match-list").innerHTML =
    result.recommendations.map((item) => matchCard(item)).join("") ||
    `<div class="empty-state"><h3>No towns fit</h3><p>Loosen a limit or choose another example${examples ? ` than ${escapeHtml(examples)}` : ""}. ${target} qualities were compared.</p></div>`;
  bindCard("#match-list", result.recommendations);
}

/* ---------- shortlist ---------- */

function renderShortlist() {
  const entries = scenario().shortlist;
  $("#shortlist-count").textContent = entries.length;
  $("#kept-count").textContent = entries.length;
  $("#shortlist-hint").textContent =
    entries.length >= 3
      ? "Good. Your shortlist is ready for verification."
      : `Keep at least three towns that you would research further. ${3 - entries.length} to go.`;
  const unsure = Object.entries(scenario().decisions)
    .filter(([, decision]) => decision === "unsure")
    .map(([placeId]) =>
      scenario().result?.recommendations.find((item) => item.place_id === placeId)
    )
    .filter(Boolean);
  const kept = entries
    .map((entry) =>
      entry.source === "recommendation"
        ? matchCard(entry.recommendation, { snapshot: true })
        : `<article class="match-card" data-place-id="${escapeHtml(entry.place_id)}">
        <header><span class="rank">Added</span><h3>${escapeHtml(entry.label)}</h3></header>
        <p class="field-help">Added by hand. It has no discovery match score
        ${entry.serving_eligible === false ? " because its population is under 2,500 or unknown" : ""}.
        Verification still needs reviewed evidence for it.</p>
        <div class="decision-row"><button type="button" class="text-button" data-remove-manual="${escapeHtml(entry.place_id)}">Remove</button></div></article>`
    )
    .join("");
  $("#shortlist-list").innerHTML =
    kept +
      (unsure.length
        ? `<h3 class="subhead">Unsure</h3>${unsure.map((item) => matchCard(item)).join("")}`
        : "") ||
    `<div class="empty-state"><h3>Nothing kept yet</h3><p>Mark towns <strong>Keep</strong> on the Matches step.</p></div>`;
  if (state.step === "shortlist") {
    $("#next-button").disabled = entries.length < 2;
    setHint(entries.length < 2 ? "Keep at least two towns to verify them." : "");
  }
  const recommendations = entries
    .filter((entry) => entry.source === "recommendation")
    .map((entry) => entry.recommendation);
  bindCard("#shortlist-list", [...recommendations, ...unsure]);
  $$("#shortlist-list [data-remove-manual]").forEach((button) =>
    button.addEventListener("click", () => {
      scenario().shortlist = scenario().shortlist.filter(
        (entry) => entry.place_id !== button.dataset.removeManual
      );
      persist();
      renderShortlist();
      updateNav();
    })
  );
}

async function searchManual() {
  const query = $("#manual-search").value.trim();
  const box = $("#manual-results");
  if (query.length < 2) {
    box.innerHTML = "";
    return;
  }
  try {
    state.manualMatches = await lookup(query);
  } catch (error) {
    box.innerHTML = `<p class="lookup-empty">${escapeHtml(error.message)}</p>`;
    return;
  }
  const have = new Set(scenario().shortlist.map((entry) => entry.place_id));
  box.innerHTML = state.manualMatches.length
    ? state.manualMatches
        .map((place) =>
          placeRow(
            place,
            "Add manually",
            have.has(place.place_id) ? "Already on your shortlist." : ""
          )
        )
        .join("")
    : `<p class="lookup-empty">No U.S. town matches “${escapeHtml(query)}”.</p>`;
  $$("#manual-results button").forEach((button) =>
    button.addEventListener("click", () => {
      const place = state.manualMatches.find((item) => item.place_id === button.dataset.placeId);
      scenario().shortlist.push({
        place_id: place.place_id,
        label: place.label,
        name: place.name,
        state: place.state,
        source: "manual",
        serving_eligible: place.serving_eligible,
        added_at: new Date().toISOString(),
      });
      $("#manual-search").value = "";
      box.innerHTML = "";
      persist();
      renderShortlist();
      updateNav();
      toast(`${place.label} added to your shortlist.`);
    })
  );
}

/* ---------- verify (evidence handoff) ---------- */

function evidenceFor(entry) {
  return state.evidence.places.find(
    (place) => fold(place.name) === fold(entry.name) && place.state === entry.state
  );
}

function handoffRows() {
  const rows = scenario().shortlist.map((entry) => ({ entry, evidence: evidenceFor(entry) }));
  return rows;
}

function runnablePlaceIds() {
  const ids = new Set();
  for (const { evidence } of handoffRows()) {
    if (evidence && evidence.complete_metrics > 0) ids.add(evidence.place_id);
  }
  for (const placeId of state.extraSelected) ids.add(placeId);
  return [...ids];
}

function metricRows(evidence) {
  const present = new Set(evidence?.present_metrics ?? []);
  return state.evidence.metricDetails
    .map(
      (metric) => `<li class="${present.has(metric.id) ? "is-present" : "is-absent"}">
      <span>${escapeHtml(metric.name)}${metric.critical ? ' <b class="tag">Critical</b>' : ""}</span>
      <strong>${present.has(metric.id) ? "Provided" : "Missing"}</strong></li>`
    )
    .join("");
}

function renderHandoff() {
  const rows = handoffRows();
  const runnable = runnablePlaceIds();
  const known = rows.filter((row) => row.evidence && row.evidence.complete_metrics > 0).length;
  $("#handoff-count").textContent =
    `${runnable.length} of ${rows.length + state.extraSelected.size} ready to compare`;
  const criticalTotal = state.evidence.metricDetails.filter((metric) => metric.critical).length;
  let html = rows
    .map(({ entry, evidence }) => {
      const provided = evidence ? evidence.complete_metrics : 0;
      const missingCritical = state.evidence.metricDetails.filter(
        (metric) => metric.critical && !(evidence?.present_metrics ?? []).includes(metric.id)
      ).length;
      const status = evidence
        ? `${provided} of ${state.evidence.metricCount} metrics provided`
        : "No reviewed evidence for this town yet";
      return `<details class="handoff-row ${evidence ? "" : "is-empty"}">
        <summary><strong>${escapeHtml(entry.label)}</strong>
          <span>${escapeHtml(status)}</span>
          <span class="tag ${missingCritical ? "is-missing" : ""}">${missingCritical} of ${criticalTotal} critical missing</span></summary>
        <ul class="metric-list">${metricRows(evidence)}</ul></details>`;
    })
    .join("");
  const extras = state.evidence.places.filter(
    (place) => !rows.some((row) => row.evidence?.place_id === place.place_id)
  );
  if (state.evidence.token || !state.discovery.available) {
    html += extras.length
      ? `<h3 class="subhead">From your imported evidence</h3>${extras
          .map(
            (
              place
            ) => `<label class="town-row"><input type="checkbox" value="${escapeHtml(place.place_id)}" ${state.extraSelected.has(place.place_id) ? "checked" : ""}>
        <span class="checkmark" aria-hidden="true"></span>
        <span class="town-name"><strong>${escapeHtml(place.name)}</strong><span>${escapeHtml(place.state)}</span></span>
        <span class="town-readiness">${place.complete_metrics}/${place.total_metrics} metrics</span></label>`
          )
          .join("")}`
      : "";
  }
  $("#handoff-list").innerHTML =
    html ||
    `<div class="empty-state"><h3>No finalists yet</h3><p>Keep towns on the Matches step first.</p></div>`;
  $$("#handoff-list input[type=checkbox]").forEach((input) =>
    input.addEventListener("change", () => {
      if (input.checked) state.extraSelected.add(input.value);
      else state.extraSelected.delete(input.value);
      renderHandoff();
      updateVerifyAction();
    })
  );
  const none = known === 0 && state.extraSelected.size === 0;
  $("#budget-help").textContent = budgetFromSearch()
    ? "Starts from your home-value limit. Towns above this amount fail the purchase-feasibility gate."
    : "Towns above this amount fail the purchase-feasibility gate.";
  updateVerifyAction(none);
}

function budgetFromSearch() {
  const limit = profile().limits.median_home_value?.max;
  return typeof limit === "number" ? limit : null;
}

function updateVerifyAction(none = false) {
  if (state.step !== "verify") return;
  const ready = runnablePlaceIds().length >= 2;
  $("#next-button").disabled = !ready;
  setHint(
    ready
      ? "Discovery cannot clear a gate; the comparison uses reviewed evidence only."
      : none
        ? "No finalist has reviewed evidence yet. Use Advanced evidence import, or try the synthetic demo towns."
        : "Comparison needs reviewed evidence for at least two towns."
  );
}

/* ---------- navigation ---------- */

function canVisit(step) {
  switch (step) {
    case "feel":
    case "limits":
      return true;
    case "matches":
      return Boolean(scenario().result);
    case "shortlist":
      return Boolean(scenario().result) || scenario().shortlist.length > 0;
    case "verify":
    case "results":
      return (
        scenario().shortlist.length >= 2 || state.advancedOpen || Boolean(state.evidence.token)
      );
    default:
      return false;
  }
}

function updateNav() {
  const current = railStep[state.step] ?? state.step;
  const index = stepOrder.indexOf(current);
  $$(".step-link").forEach((element) => {
    const target = element.dataset.stepTarget;
    element.classList.toggle("is-active", target === current);
    element.classList.toggle("is-complete", stepOrder.indexOf(target) < index);
    element.disabled = !canVisit(target);
    if (target === current) element.setAttribute("aria-current", "step");
    else element.removeAttribute("aria-current");
  });
}

const nextLabel = {
  feel: "Find places",
  limits: "Find places",
  matches: "Review shortlist",
  shortlist: "Verify finalists",
  verify: "Run comparison",
};

function setStep(step) {
  if (!canVisit(step)) return;
  state.step = step;
  $$(".stage").forEach((element) =>
    element.classList.toggle("is-active", element.dataset.stage === step)
  );
  const [eyebrow, title] = stepCopy[step];
  $("#step-eyebrow").textContent = eyebrow;
  $("#step-title").textContent = title;
  $("#synthetic-notice").hidden = !["verify", "results"].includes(step);
  $("#back-button").hidden = step === "feel" || step === "results";
  const next = $("#next-button");
  next.disabled = false;
  next.hidden = false;
  setHint("");
  if (step === "results") {
    next.innerHTML = "Adjust comparison <span>↺</span>";
  } else {
    next.innerHTML = `${nextLabel[step]} <span>→</span>`;
  }
  if (step === "feel" || step === "limits") updateTargetSummary();
  if (step === "matches") renderMatches();
  if (step === "shortlist") renderShortlist();
  if (step === "verify") {
    const budget = budgetFromSearch();
    if (budget !== null && !state.budgetTouched) {
      const input = $("#budget");
      input.value = String(
        Math.min(Number(input.max), Math.max(Number(input.min), Math.round(budget / 25000) * 25000))
      );
      updateBudget();
    }
    renderHandoff();
  }
  updateNav();
  $("#step-title").focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function onNext() {
  switch (state.step) {
    case "feel":
    case "limits":
      runSearch();
      break;
    case "matches":
      setStep("shortlist");
      break;
    case "shortlist":
      setStep("verify");
      break;
    case "verify":
      runComparison();
      break;
    default:
      setStep("verify");
  }
}

function onBack() {
  const order = ["feel", "limits", "matches", "shortlist", "verify"];
  const index = order.indexOf(state.step);
  if (index > 0) setStep(order[index - 1]);
}

/* ---------- evidence run and results (existing engine flow) ---------- */

function updateBudget() {
  const input = $("#budget");
  const percentage =
    ((Number(input.value) - Number(input.min)) / (Number(input.max) - Number(input.min))) * 100;
  input.style.setProperty("--range-progress", `${percentage}%`);
  $("#budget-output").textContent = money.format(Number(input.value));
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
  setHint("This run is saved locally. Adjust the inputs to explore another field.");
  $("#next-button").hidden = false;
  $("#next-button").disabled = false;
  $("#next-button").innerHTML = "Adjust comparison <span>↺</span>";
  window.scrollTo({ top: 0, left: 0, behavior: "smooth" });
}

async function runComparison() {
  const ids = runnablePlaceIds();
  if (ids.length < 2) {
    toast("Comparison needs reviewed evidence for at least two towns.");
    return;
  }
  setStep("results");
  $("#next-button").disabled = true;
  try {
    const response = await fetch("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        selected_place_ids: ids,
        purchase_budget_max: Number($("#budget").value),
        future_self_age: Number($("input[name=age]:checked").value),
        household: $("input[name=household]:checked").value,
        evidence_token: state.evidence.token,
      }),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(describeApiError(payload));
    renderResults(payload);
  } catch (error) {
    toast(error.message);
    setStep("verify");
  }
}

async function importEvidence(file) {
  const response = await fetch("/api/evidence/inspect", {
    method: "POST",
    headers: { "Content-Type": "text/csv" },
    body: file,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(describeApiError(payload));
  state.evidence.token = payload.evidence_token;
  state.evidence.places = payload.places;
  state.evidence.metricCount = payload.metric_count;
  state.evidence.kind = payload.evidence_kind;
  state.extraSelected = new Set();
  $("#dataset-label").textContent = file.name;
  $("#dataset-meta").textContent =
    `${payload.places.length} towns · ${payload.evidence_kind} evidence`;
  $("#synthetic-notice").style.display = payload.evidence_kind === "real" ? "none" : "flex";
  $("#synthetic-notice").classList.toggle("is-real", payload.evidence_kind === "real");
  $("#synthetic-notice span").textContent =
    payload.evidence_kind === "real" ? "Imported evidence" : `${payload.evidence_kind} evidence`;
  $("#synthetic-notice p").textContent =
    payload.evidence_kind === "real"
      ? "The engine will validate source policy, dates, ranges, and geography before scoring."
      : "This import contains synthetic values. Treat its results as test output, not purchase research.";
  state.advancedOpen = true;
  renderHandoff();
  updateNav();
  toast(`Imported ${payload.places.length} towns from ${file.name}`);
}

/* ---------- startup ---------- */

function renderAll() {
  renderFeel();
  renderLimits();
  renderMatches();
  renderShortlist();
  updateNav();
}

function renderCatalogMeta() {
  const catalog = state.discovery;
  $("#catalog-meta").innerHTML = catalog.available
    ? `<div><span>U.S. place catalog</span><small>${whole.format(catalog.serving_places)} towns of 2,500+ people · Census ${escapeHtml(catalog.data_date.slice(0, 4))}</small></div>`
    : `<div><span>Discovery unavailable</span><small>${escapeHtml(catalog.error ?? "")}</small></div>`;
}

async function initialize() {
  try {
    const response = await fetch("/api/bootstrap");
    if (!response.ok) throw new Error("The local engine did not start correctly.");
    const payload = await response.json();
    state.discovery = payload.discovery;
    state.evidence = {
      places: payload.places,
      metricCount: payload.metric_count,
      metricDetails: payload.metric_details,
      token: null,
      kind: "synthetic",
    };
    $("#budget").value = payload.defaults.purchase_budget_max;
    $("#dataset-meta").textContent =
      `${payload.places.length} towns · ${payload.metric_count} metrics`;
    updateBudget();
    renderCatalogMeta();
    if (!state.discovery.available) {
      showBanner(
        "Discovery is unavailable",
        `${state.discovery.error}. The advanced evidence import still works.`
      );
      state.advancedOpen = true;
    } else {
      restoreScenario();
    }
    renderAll();
    window.setTimeout(() => $("#loading-screen").classList.add("is-hidden"), 250);
  } catch (error) {
    $("#loading-screen p").textContent = error.message;
  }
}

$("#budget").addEventListener("input", () => {
  state.budgetTouched = true;
  updateBudget();
});
$("#exemplar-search").addEventListener("input", debounce(searchExemplars));
$("#manual-search").addEventListener("input", debounce(searchManual));
$("#state-exclude").addEventListener("change", (event) => {
  if (!event.target.value) return;
  profile().states = [...profile().states, event.target.value].sort();
  persist();
  renderLimits();
});
$("#rerun-button").addEventListener("click", () => {
  setStep("limits");
  toast("Review your search, then choose Find places.");
});
$("#export-button").addEventListener("click", () =>
  downloadJson(JSON.stringify(scenario(), null, 2), "lifescape-search.json")
);
$("#reset-button").addEventListener("click", (event) => {
  const button = event.currentTarget;
  if (button.dataset.confirming === "true") {
    button.dataset.confirming = "false";
    button.textContent = "Start over";
    resetEverything();
    return;
  }
  button.dataset.confirming = "true";
  button.textContent = "Confirm: clear my search and shortlist";
  window.setTimeout(() => {
    button.dataset.confirming = "false";
    button.textContent = "Start over";
  }, 6000);
});
$("#limits-link").addEventListener("click", () => setStep("limits"));
$("#advanced-link").addEventListener("click", () => {
  state.advancedOpen = true;
  setStep("verify");
});
$("#next-button").addEventListener("click", onNext);
$("#back-button").addEventListener("click", onBack);
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
initialize();
