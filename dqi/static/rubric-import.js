"use strict";

// A file chosen on Upload belongs to the next successful dataset upload.
// A file chosen on Choose checks belongs to the dataset already on screen.
let nextRubricFile = null;
let currentRubricDraft = null;
let lastAppliedRubric = null;
let rubricReadVersion = 0;

function resetRubricImport() {
  rubricReadVersion++;
  nextRubricFile = null;
  currentRubricDraft = null;
  lastAppliedRubric = null;
  refreshRubricImport();
}
function rubricDatasetChanged(keepPending) {
  rubricReadVersion++;
  currentRubricDraft = nextRubricFile || (keepPending ? currentRubricDraft : null);
  nextRubricFile = null;
  lastAppliedRubric = null;
  refreshRubricImport();
}
function validateRubricImportForInspection() {
  return currentRubricDraft ? "Apply the pending rubric or discard it before running the inspection." : "";
}

async function readRubricFile(file, forNextDataset) {
  if (!file || state.busy || state.answerReviewRunning) return;
  clearError();
  if (!state.bootstrap) { showError(new Error("The inspector is still connecting. Try again once it connects.")); return; }
  if (file.size > 256 * 1024) { showError(new Error("Choose a rubric file up to 256 KiB.")); return; }
  const version = ++rubricReadVersion;
  setBusy(true, `Reading ${file.name}…`);
  try {
    const data = await api("/api/rubric-import", {
      method: "POST", body: file,
      headers: {"Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name), "X-DQI-Token": state.bootstrap.csrf_token}
    });
    if (version !== rubricReadVersion) return;
    const draft = {...data, choices: []};
    if (forNextDataset) nextRubricFile = draft;
    else currentRubricDraft = draft;
    refreshRubricImport();
  } catch (error) {
    if (version === rubricReadVersion) showError(error);
  } finally {
    setBusy(false);
  }
}

function normalizedRubricName(field) {
  return field.normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
}
function rubricMatch(field, columns) {
  if (columns.includes(field)) return {target: field, note: "Exact column name."};
  const key = normalizedRubricName(field);
  const matches = key ? columns.filter((name) => normalizedRubricName(name) === key) : [];
  if (matches.length === 1) return {target: matches[0], note: "Matched after ignoring case, spaces, and punctuation."};
  if (matches.length > 1) return {target: "", note: `Several columns match: ${matches.join(", ")}. Choose one or skip this entry.`};
  return {target: "", note: "No matching name. Choose a score column or skip this entry."};
}
function reservedRubricFields() {
  const reserved = new Set();
  Object.entries(state.shared).forEach(([role, setting]) => {
    if (setting.required || (role === "evaluation_id" && state.unique)) reserved.add(setting.field);
  });
  if (state.audit?.enabled) {
    reserved.add(state.audit.prompt);
    reserved.add(state.shared.response?.field);
  }
  return reserved;
}

// Answer-review bounds support 1,000 decimal places. A fixed-point BigInt
// compares those bounds exactly, including differences lost by Number().
function rubricEndpoint(text) {
  const [mantissa, exponent = "0"] = decimalLiteral(String(text)).toLowerCase().split("e");
  const [whole, fraction = ""] = mantissa.replace("-", "").split(".");
  const digits = (whole + fraction).replace(/^0+/, "") || "0";
  const shift = Number(exponent) - fraction.length;
  if (digits.length > 1000 || Math.abs(shift) > 1000) throw new Error("Use at most 1,000 digits or decimal places.");
  const value = BigInt(digits) * 10n ** BigInt(shift + 1000);
  if (value > 10n ** 1012n) throw new Error("Use bounds between −1 trillion and 1 trillion.");
  return mantissa.startsWith("-") ? -value : value;
}
function rubricScale(entry, rule) {
  const fallback = rule?.rangeEnabled ? rule : makeRule();
  return {min: entry.min ?? fallback.min, max: entry.max ?? fallback.max, integer: entry.integer ?? rule?.integer ?? fallback.integer};
}
function rubricScaleError(scale) {
  try {
    const low = rubricEndpoint(scale.min), high = rubricEndpoint(scale.max);
    if (low >= high) return "The minimum must be below the maximum.";
    const unit = 10n ** 1000n;
    if (scale.integer && (low % unit || high % unit)) return "Whole-number scores need whole-number bounds.";
  } catch (error) {
    return error.message;
  }
  return "";
}
function describeRubricScale(scale) {
  return `${scale.min} to ${scale.max}, ${scale.integer ? "whole numbers" : "decimals allowed"}`;
}
function planRubricMappings(draft) {
  const columns = state.upload.columns;
  const reserved = reservedRubricFields();
  const mappings = draft.entries.map((entry, index) => {
    const match = rubricMatch(entry.field, columns);
    const choice = draft.choices[index];
    const field = choice === undefined ? match.target : choice;
    const row = {entry, field, note: choice === undefined ? match.note : "Manually selected.", error: "", scale: null, oldRule: null};
    if (field === null) { row.note = "Skipped."; return row; }
    if (!field) { row.error = "Choose a column or skip this entry."; return row; }
    if (!columns.includes(field)) { row.error = "Choose a column in this dataset."; return row; }
    if (reserved.has(field)) { row.error = "This column is in use by a shared field or the dataset audit. Choose another score column."; return row; }
    const existing = state.rules.filter((rule) => rule.field === field);
    if (existing.length > 1) { row.error = "This column has multiple rating rules. Keep one in Rating attributes."; return row; }
    row.oldRule = existing[0];
    row.scale = rubricScale(entry, row.oldRule);
    row.error = rubricScaleError(row.scale);
    return row;
  });
  const used = new Set();
  const repeated = new Set();
  for (const row of mappings) {
    if (!row.field) continue;
    if (used.has(row.field)) repeated.add(row.field);
    used.add(row.field);
  }
  for (const row of mappings) {
    if (repeated.has(row.field)) row.error = "Another rubric entry uses this column. Choose distinct score columns.";
  }
  return mappings;
}
function applyRubricDraft() {
  if (!currentRubricDraft || !state.upload) return;
  const mappings = planRubricMappings(currentRubricDraft);
  const selected = mappings.filter((row) => row.field !== null);
  if (!selected.length || selected.some((row) => row.error)) { refreshRubricImport(); return; }
  // Build the complete replacement before changing the inspection settings.
  const updated = state.rules.map((rule) => {
    const copy = {...rule};
    delete copy.importedRubric;
    return copy;
  });
  for (const {entry, field, scale} of selected) {
    let rule = updated.find((item) => item.field === field);
    if (!rule) { rule = makeRule(field); updated.push(rule); }
    Object.assign(rule, scale, {rangeEnabled: true});
    rule.importedRubric = {filename: currentRubricDraft.filename, sourceField: entry.field, field, description: entry.description, ...scale};
  }
  state.rules = updated;
  lastAppliedRubric = currentRubricDraft;
  currentRubricDraft = null;
  edited();
  renderRules();
  refreshRubricImport();
}

function renderRubricMapping(index, row, reserved) {
  const {entry, field, note, scale, oldRule, error} = row;
  const container = element("div", null, "rubric-mapping");
  const details = element("div");
  details.append(element("strong", entry.field), element("p", entry.description, "rubric-criteria"));
  const criteriaScale = entry.min === null ? "This entry uses the selected column’s scale." : `Rubric scale: ${describeRubricScale(entry)}.`;
  details.append(element("p", criteriaScale, "small subtle"));
  const label = element("label", "Score column");
  const select = element("select");
  select.id = `rubric-target-${index}`;
  select.setAttribute("aria-label", `Score column for ${entry.field}`);
  const option = (value, title) => { const item = element("option", title); item.value = value; select.append(item); return item; };
  option("", "Choose a column");
  option("skip", "Skip this entry");
  state.upload.columns.forEach((name, colIndex) => {
    const blocked = reserved.has(name);
    option(String(colIndex), blocked ? `${name} (in use)` : name).disabled = blocked;
  });
  select.value = field === null ? "skip" : field ? String(state.upload.columns.indexOf(field)) : "";
  select.addEventListener("change", () => {
    const value = select.value;
    currentRubricDraft.choices[index] = value === "skip" ? null : value === "" ? "" : state.upload.columns[Number(value)];
    refreshRubricImport();
    $(`#rubric-target-${index}`).focus();
  });
  label.append(select);
  const hint = element("p", note, "small subtle");
  hint.id = `rubric-hint-${index}`;
  if (scale) {
    const previous = oldRule?.rangeEnabled ? describeRubricScale(oldRule) : "";
    const proposed = describeRubricScale(scale);
    const text = previous === proposed ? ` Keeps ${proposed}.` : previous ? ` Changes ${previous} to ${proposed}.` : ` Adds a range of ${proposed}.`;
    hint.append(text);
  }
  if (error && field) hint.append(" ", error);
  if (error) hint.classList.add("rubric-attention");
  select.setAttribute("aria-describedby", hint.id);
  const controls = element("div"); controls.append(label, hint);
  container.append(details, controls);
  return container;
}
function refreshRubricImport() {
  $("#upload-rubric-status").textContent = nextRubricFile ? `${nextRubricFile.filename} is ready. Choose your dataset to match the score columns.` : "";
  $("#remove-staged-rubric").hidden = !nextRubricFile;
  $("#rubric-applied").hidden = !lastAppliedRubric;
  $("#reopen-rubric").hidden = Boolean(currentRubricDraft);
  if (lastAppliedRubric) {
    const fields = state.rules.filter((rule) => rule.importedRubric?.field === rule.field).map((rule) => rule.field);
    $("#applied-rubric-status").textContent = fields.length
      ? `Applied ${lastAppliedRubric.filename} to ${fields.join(", ")}.`
      : `The current checks have no criteria from ${lastAppliedRubric.filename}. Review its matches to apply it again.`;
  }
  $("#rubric-draft").hidden = !currentRubricDraft || !state.upload;
  if (!currentRubricDraft || !state.upload) return;
  $("#rubric-filename").textContent = `Match ${currentRubricDraft.filename}`;
  const mappings = planRubricMappings(currentRubricDraft);
  const reserved = reservedRubricFields();
  $("#rubric-mappings").replaceChildren(...mappings.map((row, index) => renderRubricMapping(index, row, reserved)));
  const ready = mappings.filter((row) => row.field && !row.error).length;
  const pending = mappings.filter((row) => row.error).length;
  const skipped = mappings.filter((row) => row.field === null).length;
  const parts = [`${ready} ready to apply`, `${pending} ${pending === 1 ? "needs" : "need"} attention`, `${skipped} skipped`];
  $("#rubric-mapping-status").textContent = parts.join(" · ") + (lastAppliedRubric ? ". Applying replaces the previously imported criteria." : ".");
  $("#apply-rubric").disabled = !ready || pending > 0;
}

for (const [id, forNextDataset] of [["upload-rubric-file", true], ["checks-rubric-file", false]]) {
  $(`#${id}`).addEventListener("change", (event) => {
    const input = event.target;
    const file = input.files[0];
    input.value = "";
    readRubricFile(file, forNextDataset);
  });
}
$("#remove-staged-rubric").addEventListener("click", () => { nextRubricFile = null; clearError(); refreshRubricImport(); });
$("#discard-rubric").addEventListener("click", () => { currentRubricDraft = null; clearError(); refreshRubricImport(); });
$("#reopen-rubric").addEventListener("click", () => {
  if (lastAppliedRubric) currentRubricDraft = {...lastAppliedRubric, choices: [...lastAppliedRubric.choices]};
  refreshRubricImport();
});
$("#apply-rubric").addEventListener("click", applyRubricDraft);
