/*
 * Local scenario storage (PRD FR10, FR12; ADR "Local-state compatibility").
 *
 * The scenario lives only in this browser. State carries an integer schema_version. A value that
 * cannot be read as a supported, fully valid scenario is never partly loaded: the original text is
 * copied to a backup key and the caller offers JSON export and an explicit reset.
 */
(function () {
  "use strict";

  const KEY = "lifescape.scenario";
  const BACKUP_KEY = "lifescape.scenario.backup";
  const SCHEMA_VERSION = 1;
  const DECISIONS = ["keep", "reject", "unsure"];
  const SOURCES = ["recommendation", "manual"];
  // Breaking shape changes add a function here that upgrades version N to N + 1.
  const MIGRATIONS = {};

  const isObject = (value) => value !== null && typeof value === "object" && !Array.isArray(value);
  const isText = (value) => typeof value === "string" && value.length > 0;

  function emptyScenario() {
    return {
      schema_version: SCHEMA_VERSION,
      profile: { exemplars: [], targets: {}, priorities: {}, limits: {}, regions: [], states: [] },
      result: null,
      decisions: {},
      shortlist: [],
      previous_ranks: {},
      saved_at: null,
    };
  }

  function validateProfile(profile) {
    if (!isObject(profile)) throw new Error("missing profile");
    const { exemplars, targets, priorities, limits, regions, states } = profile;
    if (!Array.isArray(exemplars) || exemplars.length > 2) throw new Error("invalid exemplars");
    for (const exemplar of exemplars) {
      if (!isObject(exemplar) || !isText(exemplar.place_id) || !isText(exemplar.label)) {
        throw new Error("invalid exemplar");
      }
    }
    if (![targets, priorities, limits].every(isObject)) {
      throw new Error("invalid targets, priorities, or limits");
    }
    if (!Array.isArray(regions) || !Array.isArray(states)) throw new Error("invalid filters");
  }

  const ARRAY_FIELDS = [
    "components",
    "reasons",
    "differences",
    "fields",
    "missing_fields",
    "unknown_constraints",
  ];

  function validateRecommendation(item) {
    const numbers = ["match_percent", "rank", "component_count", "profile_target_count"];
    const texts = ["place_id", "label", "name", "state", "catalog_version", "data_date"];
    if (
      !isObject(item) ||
      !texts.every((key) => isText(item[key])) ||
      !ARRAY_FIELDS.every((key) => Array.isArray(item[key])) ||
      !numbers.every((key) => Number.isFinite(item[key]))
    ) {
      throw new Error("invalid recommendation snapshot");
    }
  }

  function validateResult(result) {
    if (result === null) return;
    const texts = ["catalog_version", "algorithm_version", "normalization_version"];
    if (
      !isObject(result) ||
      !Array.isArray(result.recommendations) ||
      !texts.every((key) => isText(result[key])) ||
      !isObject(result.diagnostics) ||
      !isObject(result.profile)
    ) {
      throw new Error("invalid result snapshot");
    }
    result.recommendations.forEach(validateRecommendation);
  }

  function validateDecisions(decisions) {
    if (!isObject(decisions)) throw new Error("invalid decisions");
    if (!Object.values(decisions).every((decision) => DECISIONS.includes(decision))) {
      throw new Error("invalid decision value");
    }
  }

  function validateShortlist(shortlist) {
    if (!Array.isArray(shortlist)) throw new Error("invalid shortlist");
    const valid = (entry) =>
      isObject(entry) &&
      isText(entry.place_id) &&
      isText(entry.label) &&
      SOURCES.includes(entry.source);
    if (!shortlist.every(valid)) throw new Error("invalid shortlist entry");
    for (const entry of shortlist) {
      if (entry.source === "recommendation") validateRecommendation(entry.recommendation);
      else if (!isText(entry.name) || !isText(entry.state)) {
        throw new Error("invalid manual shortlist entry");
      }
    }
  }

  function validate(value) {
    if (!isObject(value)) throw new Error("the saved search is not an object");
    if (value.schema_version !== SCHEMA_VERSION) throw new Error("unsupported schema version");
    validateProfile(value.profile);
    validateResult(value.result);
    validateDecisions(value.decisions);
    validateShortlist(value.shortlist);
    if (!isObject(value.previous_ranks)) throw new Error("invalid previous ranks");
    return value;
  }

  function migrate(value) {
    let current = value;
    while (isObject(current) && Number.isInteger(current.schema_version)) {
      if (current.schema_version === SCHEMA_VERSION) return current;
      const step = MIGRATIONS[current.schema_version];
      if (!step) break;
      current = step(current);
    }
    throw new Error("unsupported schema version");
  }

  function readStorage(storage) {
    try {
      return { raw: storage.getItem(KEY), error: null };
    } catch (error) {
      return { raw: null, error: `browser storage is unavailable: ${error.message}` };
    }
  }

  /** Returns {scenario, problem, rawText}. A problem means nothing was loaded. */
  function load(storage) {
    const { raw, error } = readStorage(storage);
    if (error) return { scenario: emptyScenario(), problem: error, rawText: null, blocked: true };
    if (raw === null) return { scenario: emptyScenario(), problem: null, rawText: null };
    try {
      return { scenario: validate(migrate(JSON.parse(raw))), problem: null, rawText: null };
    } catch (failure) {
      try {
        storage.setItem(BACKUP_KEY, raw);
      } catch (backupFailure) {
        return {
          scenario: emptyScenario(),
          problem: `${failure.message}; the backup also failed: ${backupFailure.message}`,
          rawText: raw,
        };
      }
      return { scenario: emptyScenario(), problem: failure.message, rawText: raw };
    }
  }

  function save(storage, scenario) {
    scenario.saved_at = new Date().toISOString();
    storage.setItem(KEY, JSON.stringify(scenario));
  }

  function reset(storage) {
    storage.removeItem(KEY);
  }

  function backupText(storage) {
    try {
      return storage.getItem(BACKUP_KEY);
    } catch {
      return null;
    }
  }

  window.LifescapeScenario = {
    KEY,
    BACKUP_KEY,
    SCHEMA_VERSION,
    emptyScenario,
    validate,
    load,
    save,
    reset,
    backupText,
  };
})();
