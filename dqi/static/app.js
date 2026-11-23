"use strict";

const $ = (selector) => document.querySelector(selector);
const attributes = ["helpfulness", "correctness", "coherence", "complexity", "verbosity"];
const sharedLabels = {response: "Response", evaluation_id: "Evaluation ID", explanation: "Explanation"};
const state = {bootstrap: null, upload: null, result: null, shared: {}, unique: false, rules: [], audit: null, ruleOrigin: "custom", busy: false, step: "upload", utility: null, answerReviewRunning: false};
let workspacePosition = null;
const number = (value) => value == null ? "—" : Number.isFinite(Number(value)) ? Number(value).toLocaleString(undefined, {maximumFractionDigits: 2}) : String(value);
const scoreText = (value) => value == null ? "—" : String(value);

function element(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}
function showError(error) {
  $("#error").textContent = error.message || String(error);
  $("#error").hidden = false;
}
function clearError() { $("#error").hidden = true; }
function updateNavigation() {
  document.querySelectorAll("[data-step]").forEach((button) => {
    const step = button.dataset.step;
    button.disabled = state.busy || (state.answerReviewRunning && step !== "report") || (step === "checks" && !state.upload) || (step === "report" && !state.result);
    button.classList.toggle("active", state.step === step);
    if (state.step === step) button.setAttribute("aria-current", "step");
    else button.removeAttribute("aria-current");
  });
  for (const page of ["settings", "walkthrough"]) {
    const button = $(`#open-${page}`);
    button.disabled = state.busy;
    if (state.utility === page) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  }
}
function renderPage() {
  const page = state.utility || state.step;
  const dataset = ["upload", "checks", "report"].includes(page);
  for (const name of ["upload", "checks", "report", "single-task", "settings", "walkthrough"]) {
    $(`#${name}-panel`).hidden = name !== page;
  }
  $(".steps").hidden = !dataset;
  $(".page-heading").hidden = !dataset;
  $("#new-file").hidden = !dataset || !state.upload;
  const restored = $("main > #semantic-panel");
  if (restored) restored.hidden = !dataset || !(semanticState.jobId || semanticState.requestId);
  const hash = dataset ? "" : "#" + page;
  if (window.location.hash !== hash) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search + hash);
  }
  updateNavigation();
}
function showStep(step, restoringReview = false) {
  if (state.answerReviewRunning && step !== state.step && !restoringReview) return;
  if ((step === "checks" && !state.upload) || (step === "report" && !state.result)) return;
  if (step !== "single-task" && state.step === "single-task") leaveSingleTask();
  if (step === "report" && semanticState.mode === "single_task") initializeSemanticReview();
  state.step = step;
  renderPage();
}
function openUtility(page, trigger = null) {
  if (!state.bootstrap || state.busy || state.utility === page) return;
  if (!state.utility) workspacePosition = {trigger, scroll: window.scrollY};
  if (state.utility === "settings") leaveCredentialSettings();
  clearError();
  state.utility = page;
  renderPage();
  if (page === "settings") {
    clearReviewCredentials();
    enterCredentialSettings(state.step);
  }
  window.scrollTo(0, 0);
  $(`#${page}-title`).focus({preventScroll: true});
}
function closeUtility() {
  if (!state.utility || state.busy) return;
  const page = state.utility;
  if (page === "settings") leaveCredentialSettings();
  state.utility = null;
  clearError(); renderPage();
  const trigger = workspacePosition?.trigger;
  const focus = trigger?.isConnected && trigger.getClientRects().length ? trigger : $(`#open-${page}`);
  focus.focus({preventScroll: true});
  window.scrollTo(0, workspacePosition?.scroll || 0);
  workspacePosition = null;
}
function setBusy(value, message = "Working…") {
  state.busy = value;
  $("#busy").hidden = !value;
  $("#busy-text").textContent = message;
  // Freeze the visible configuration while its request is being processed.
  document.querySelectorAll(".panel, #new-file, #close-app, #desktop-shortcut").forEach((node) => { node.inert = value; });
  if (!value && state.answerReviewRunning) {
    $("#upload-panel").inert = true; $("#checks-panel").inert = true;
  }
  updateNavigation();
}
async function api(url, options = {}) {
  if (window.location.pathname === "/tour-preview") throw new Error("The tour uses a read-only example.");
  let response;
  try { response = await fetch(url, options); }
  catch { throw new Error("The local inspector is not responding. Reopen Data Quality Inspector and reload this page."); }
  const data = window.DQIParseJSON(await response.text());
  if (!response.ok) { const error = new Error(data.error || "The request could not be completed."); error.status = response.status; throw error; }
  return data;
}
function valueText(value, present = true) {
  if (!present) return "(field absent)";
  if (value === null) return "null";
  if (typeof value === "object") return JSON.stringify(value);
  if (value === "") return "(blank)";
  return String(value);
}
function column(candidates) {
  return candidates.map((name) => state.upload.columns.find((field) => field.toLowerCase() === name)).find(Boolean) || "";
}
function isHelpSteer() { return [...attributes, "response"].every((name) => column([name])); }

async function uploadFile(file, format = $("#format-select").value) {
  if (!file || state.busy || state.answerReviewRunning) return;
  clearError();
  if (!state.bootstrap) { showError(new Error("The inspector is still connecting. Reload this page if it does not connect.")); return; }
  if (file.size > state.bootstrap.max_upload_bytes) { showError(new Error("This file exceeds the 25 MB upload limit.")); return; }
  setBusy(true, `Reading ${file.name}…`);
  try {
    const data = await api(`/api/upload${format ? `?format=${encodeURIComponent(format)}` : ""}`, {
      method: "POST", headers: {"Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name), "X-DQI-Token": state.bootstrap.csrf_token}, body: file
    });
    const firstDataset = !state.upload;
    state.upload = data;
    state.result = null;
    $("#new-file").hidden = false;
    $("#file-name").textContent = data.filename;
    $("#file-meta").textContent = `${number(data.record_count)} records · ${number(data.columns.length)} columns · ${data.input_format.toUpperCase()}`;
    renderPreview();
    applyPreset(isHelpSteer() ? "helpsteer2" : "standard");
    rubricDatasetChanged(firstDataset);
    showStep("checks");
  } catch (error) { showError(error); }
  finally { setBusy(false); $("#file-input").value = ""; }
}
function renderPreview() {
  const table = $("#preview-table");
  table.tHead.replaceChildren();
  table.tBodies[0].replaceChildren();
  const header = element("tr");
  ["Record", ...state.upload.columns].forEach((field) => header.append(element("th", field)));
  table.tHead.append(header);
  state.upload.preview.forEach((record) => {
    const row = element("tr");
    row.append(element("td", record.record));
    state.upload.columns.forEach((field) => {
      const cell = element("td");
      const text = valueText(record.values[field], Object.hasOwn(record.values, field));
      const span = element("span", text, "preview-cell");
      span.title = text;
      cell.append(span); row.append(cell);
    });
    table.tBodies[0].append(row);
  });
  $("#preview-note").textContent = `${state.upload.preview.length} shown${state.upload.preview_truncated ? " · Preview shortened; checks use the complete file" : ""}`;
}
function makeRule(field = "", helpSteer = false) {
  return {field, required: true, numeric: true, integer: helpSteer, rangeEnabled: true, min: helpSteer ? "0" : "1", max: helpSteer ? "4" : "5", presetField: helpSteer ? field : null};
}
function applyPreset(name) {
  clearError();
  if (name === "custom") return;
  if (name === "helpsteer2" && !isHelpSteer()) {
    $("#preset-select").value = "custom";
    showError(new Error("The HelpSteer2 preset needs response, helpfulness, correctness, coherence, complexity, and verbosity columns. Add the available fields as custom checks instead."));
    return;
  }
  const helpSteer = name === "helpsteer2";
  state.shared = {
    response: {field: column(["response", "answer", "completion", "output"]), required: true},
    evaluation_id: {field: column(["evaluation_id", "eval_id", "id", "review_id"]), required: false},
    explanation: {field: column(["explanation", "rationale", "notes"]), required: false}
  };
  state.shared.response.required = Boolean(state.shared.response.field);
  state.shared.evaluation_id.required = !helpSteer && Boolean(state.shared.evaluation_id.field);
  state.unique = !helpSteer && Boolean(state.shared.evaluation_id.field);
  const rating = column(["rating", "score", "helpfulness"]);
  state.rules = helpSteer ? attributes.map((name) => makeRule(column([name]), true)) : rating ? [makeRule(rating)] : [];
  state.result = null;
  state.ruleOrigin = name;
  $("#preset-select").value = name;
  initializeAuditSettings(name);
  renderShared(); renderRules(); updateCount(); updateNavigation();
  refreshRubricImport();
}
function edited() {
  state.result = null;
  state.ruleOrigin = "custom";
  $("#preset-select").value = "custom";
  renderAuditRuleSource();
  refreshRubricImport();
  clearError(); updateCount(); updateNavigation();
}
function fieldSelect(value, label, onChange) {
  const select = element("select");
  select.setAttribute("aria-label", label);
  const blank = element("option", "Choose a column"); blank.value = ""; select.append(blank);
  state.upload.columns.forEach((field) => { const option = element("option", field); option.value = field; select.append(option); });
  select.value = value;
  select.addEventListener("change", () => { onChange(select.value); edited(); });
  return select;
}
function checkbox(label, checked, onChange, className = "checkbox-label") {
  const wrapper = element("label", null, className);
  const input = element("input"); input.type = "checkbox"; input.checked = checked; input.setAttribute("aria-label", label);
  const span = element("span", label, className === "rule-toggle" ? "mobile-label" : "");
  wrapper.append(input, span);
  input.addEventListener("change", () => { onChange(input.checked); edited(); });
  return wrapper;
}
function renderShared() {
  const container = $("#shared-fields"); container.replaceChildren();
  Object.entries(sharedLabels).forEach(([key, label]) => {
    const row = element("div", null, "shared-row");
    const fieldLabel = element("label", label); fieldLabel.htmlFor = `shared-${key}`;
    const select = fieldSelect(state.shared[key].field, `${label} column`, (value) => { state.shared[key].field = value; });
    select.id = `shared-${key}`;
    row.append(fieldLabel, select, checkbox(`Required ${label.toLowerCase()}`, state.shared[key].required, (checked) => { state.shared[key].required = checked; }));
    if (key === "evaluation_id") row.append(checkbox("Unique IDs", state.unique, (checked) => { state.unique = checked; }));
    container.append(row);
  });
}
function renderRules() {
  const container = $("#rating-rules"); container.replaceChildren();
  if (!state.rules.length) container.append(element("p", "No rating fields selected. Add a field to check its scores.", "empty-rules"));
  state.rules.forEach((rule, index) => {
    const row = element("div", null, "rating-row");
    row.append(fieldSelect(rule.field, `Rating field ${index + 1}`, (value) => {
      rule.field = value;
      rule.presetField = null;
      delete rule.importedRubric;
      renderAuditThresholds();
    }));
    [["required", "Required"], ["numeric", "Number"], ["integer", "Whole"], ["rangeEnabled", "Range"]].forEach(([key, label]) => {
      const toggle = checkbox(label, rule[key], (checked) => {
        rule[key] = checked;
        if (key === "integer" || key === "rangeEnabled") delete rule.importedRubric;
        row.querySelectorAll("input[type=number]").forEach((input) => { input.disabled = !rule.rangeEnabled; });
        renderAuditThresholds();
      }, "rule-toggle");
      toggle.querySelector("input").setAttribute("aria-label", `${label} for rating ${index + 1}`);
      row.append(toggle);
    });
    [["min", "Minimum"], ["max", "Maximum"]].forEach(([key, label]) => {
      const wrapper = element("label", null, "bound-label");
      const input = element("input"); input.type = "number"; input.step = "any"; input.value = rule[key]; input.disabled = !rule.rangeEnabled;
      input.setAttribute("aria-label", `${label} for rating ${index + 1}`);
      input.addEventListener("input", () => { rule[key] = input.value; delete rule.importedRubric; edited(); });
      wrapper.append(element("span", label, "mobile-label"), input); row.append(wrapper);
    });
    const remove = element("button", "×", "remove-rule"); remove.type = "button"; remove.setAttribute("aria-label", `Remove rating field ${index + 1}`);
    remove.addEventListener("click", () => { state.rules.splice(index, 1); renderRules(); edited(); });
    row.append(remove); container.append(row);
  });
  renderAuditThresholds();
}
function checkCount() {
  return Object.values(state.shared).filter((field) => field.required).length + Number(state.unique) + state.rules.reduce((total, rule) => total + [rule.required, rule.numeric, rule.integer, rule.rangeEnabled].filter(Boolean).length, 0);
}
function updateCount() { $("#check-count").textContent = `${checkCount()} validation checks across ${state.rules.length} rating ${state.rules.length === 1 ? "field" : "fields"}.${state.audit?.enabled ? " Deeper audit included." : ""}`; }
function decimalLiteral(value) {
  // Keep the typed decimal digits intact when sending numeric JSON tokens.
  const match = value.trim().match(/^([+-]?)(\d*)(?:\.(\d*))?([eE][+-]?\d+)?$/);
  if (!match || (!match[2] && !match[3])) throw new Error("Enter a valid numeric range.");
  return `${match[1] === "-" ? "-" : ""}${(match[2] || "0").replace(/^0+(?=\d)/, "")}${match[3] ? `.${match[3]}` : ""}${match[4] || ""}`;
}
function inspectionBody(uploadId, config, audit = null) {
  const ratings = Object.entries(config.ratings).map(([field, rule]) => {
    const fields = Object.entries(rule).map(([key, value]) => `${JSON.stringify(key)}:${key === "range" && value ? `[${value.join(",")}]` : JSON.stringify(value)}`);
    return `${JSON.stringify(field)}:{${fields.join(",")}}`;
  });
  return `{"upload_id":${JSON.stringify(uploadId)},"config":{"columns":${JSON.stringify(config.columns)},"required":${JSON.stringify(config.required)},"unique_id":${JSON.stringify(config.unique_id)},"ratings":{${ratings.join(",")}}}${audit ? `,"audit":${serializeAuditOptions(audit)}` : ""}}`;
}
function configuration() {
  const config = {columns: {}, required: [], unique_id: state.unique, ratings: {}};
  Object.entries(state.shared).forEach(([key, value]) => {
    if (value.field) config.columns[key] = value.field;
    if ((value.required || (key === "evaluation_id" && state.unique)) && !value.field) throw new Error(`Choose a column for ${sharedLabels[key].toLowerCase()} or turn off its checks.`);
    if (value.required) config.required.push(key);
  });
  state.rules.forEach((rule, index) => {
    if (![rule.required, rule.numeric, rule.integer, rule.rangeEnabled].some(Boolean)) return;
    if (!rule.field) throw new Error(`Choose a source column for rating field ${index + 1}.`);
    if (Object.hasOwn(config.ratings, rule.field)) throw new Error(`The column “${rule.field}” is selected more than once. Keep one set of rules for each rating field.`);
    let range = null;
    if (rule.rangeEnabled) {
      const low = Number(rule.min), high = Number(rule.max);
      if (!rule.min.trim() || !rule.max.trim() || !Number.isFinite(low) || !Number.isFinite(high) || low > high) throw new Error(`Enter a valid minimum and maximum for “${rule.field}”. The minimum must not exceed the maximum.`);
      range = [decimalLiteral(rule.min), decimalLiteral(rule.max)];
    }
    // Define an own property even for unusual source names such as __proto__.
    Object.defineProperty(config.ratings, rule.field, {value: {required: rule.required, numeric: rule.numeric, integer: rule.integer, range}, enumerable: true});
  });
  if (!checkCount()) throw new Error("Select at least one check before running the inspection.");
  return config;
}
async function runInspection() {
  if (state.busy || state.answerReviewRunning || !state.upload) return;
  clearError();
  let config, audit;
  try {
    const rubricIssue = validateRubricImportForInspection();
    if (rubricIssue) throw new Error(rubricIssue);
    config = configuration(); audit = auditConfiguration();
  } catch (error) { showError(error); return; }
  setBusy(true, `Inspecting ${number(state.upload.record_count)} records…`);
  try {
    state.result = await api("/api/inspect", {method: "POST", headers: {"Content-Type": "application/json", "X-DQI-Token": state.bootstrap.csrf_token}, body: inspectionBody(state.upload.upload_id, config, audit)});
    renderReport(); showStep("report"); window.scrollTo({top: 0, behavior: "instant"});
  } catch (error) { showError(error); }
  finally { setBusy(false); }
}
function rulesText(rules) {
  return [rules.required ? "Required" : "Optional", rules.numeric ? "numeric" : null, rules.integer ? "whole number" : null, rules.range ? `${rules.range[0]}–${rules.range[1]}` : null].filter(Boolean).join(" · ");
}
function renderReportHighlights(report) {
  const {audit} = report;
  const list = $("#report-highlights-list");
  const add = (text) => list.append(element("li", text));
  list.replaceChildren();
  const finding = report.findings[0];
  if (finding) add(`Record ${number(finding.record)} · ${finding.field} — ${finding.reason}`);
  if (report.skipped_checks.length) {
    const skipped = report.skipped_checks[0];
    add(`Skipped check on record ${number(skipped.record)} · ${skipped.field} — ${skipped.reason}`);
  }
  if (audit) {
    const counts = new Map();
    for (const record of audit.review_queue) {
      for (const reason of record.reasons) {
        if (reason.kind === "low_rating") counts.set(reason.field, (counts.get(reason.field) || 0) + 1);
      }
    }
    const thresholds = audit.options.review_thresholds;
    for (const [field, count] of [...counts].sort((a, b) => b[1] - a[1]).slice(0, 3)) {
      add(`${number(count)} ${count === 1 ? "record has" : "records have"} a source rating of ${scoreText(thresholds[field])} or lower for ${field}.`);
    }
    if (audit.duplicate_group_count) add(`Exact repeated question–answer pairs occur in ${number(audit.duplicate_record_count)} records.`);
    if (audit.conflict_group_count) add(`Conflicting source ratings occur in ${number(audit.conflict_record_count)} records with repeated question–answer pairs.`);
    if (audit.coverage?.gaps.length) add(`${number(audit.coverage.gaps.length)} ${audit.coverage.gaps.length === 1 ? "category falls" : "categories fall"} below the selected coverage target.`);
  }
  $("#browse-review-queue").hidden = !audit?.review_record_count;
  $("#browse-findings").hidden = !report.finding_count;
  $("#browse-skipped").hidden = !report.skipped_check_count;
  $("#report-highlights").hidden = !list.childElementCount && !audit?.review_record_count;
}

function openReportDetail(selector) {
  $("#report-view-detailed").checked = true;
  const target = $(selector);
  target.focus({preventScroll: true});
  target.scrollIntoView({block: "center"});
}

function renderReport() {
  const {report, urls, filename} = state.result;
  $("#report-view-simple").checked = true;
  renderReportHighlights(report);
  const summaries = report.rating_summaries;
  $("#report-filename").textContent = filename;
  const statuses = {
    passed: ["Data checks passed", "All selected validation rules passed. This does not grade the AI answers.", ""],
    issues_found: ["Needs review", `${number(report.affected_records)} ${report.affected_records === 1 ? "record has" : "records have"} one or more findings.`, "attention"],
    incomplete: ["Incomplete", "Some selected checks could not be completed.", "attention"],
    empty_dataset: ["Empty dataset", "No records were available to inspect.", "neutral"],
    no_checks_selected: ["No checks selected", "Choose checks and run the inspection again.", "neutral"]
  };
  const status = statuses[report.status] || [report.status, "", "neutral"];
  $("#status-badge").textContent = status[0]; $("#status-badge").className = `status-badge ${status[2]}`;
  $("#status-text").textContent = status[1];
  if (report.audit?.review_record_count) $("#status-text").textContent += ` ${number(report.audit.review_record_count)} records also meet the audit's review criteria.`;
  const metrics = $("#report-metrics"); metrics.replaceChildren();
  [["Records inspected", report.total_records], ["Rating values", summaries.reduce((sum, item) => sum + item.counts.total, 0)], ["Records with validation findings", report.affected_records], ["Findings", report.finding_count], ["Skipped checks", report.skipped_check_count]].forEach(([label, value]) => {
    const metric = element("div", null, "metric"); metric.append(element("span", label, "metric-label"), element("strong", number(value), "metric-value")); metrics.append(metric);
  });
  [["#full-report", "html"], ["#download-bundle", "zip"], ["#download-findings", "findings"], ["#download-json", "json"], ["#download-text", "text"]].forEach(([id, key]) => { $(id).href = urls[key]; });
  const sharedChecks = Object.entries(state.shared).filter(([, rule]) => rule.required).map(([key]) => `required ${sharedLabels[key].toLowerCase()}`);
  if (state.unique) sharedChecks.push("unique evaluation IDs");
  $("#common-summary").textContent = sharedChecks.length ? `Shared checks: ${sharedChecks.join(" · ")}.` : "No shared field checks selected.";
  const body = $("#attribute-table tbody"); body.replaceChildren();
  summaries.forEach((item) => {
    const row = element("tr"), field = element("td");
    field.append(element("span", item.field, "attribute-field"), element("span", rulesText(item.rules), "field-rules")); row.append(field);
    [`${number(item.counts.valid)} / ${number(item.counts.total)}`, ...["missing", "invalid_numeric", "non_integer", "out_of_range", "findings"].map((key) => number(item.counts[key])), number(item.statistics.mean)].forEach((value) => row.append(element("td", value, "number")));
    body.append(row);
  });
  $("#attribute-table").hidden = !summaries.length; $("#no-attributes").hidden = !!summaries.length;
  $(".distribution-heading").hidden = !summaries.length;
  renderDistributions(summaries);
  const filter = $("#findings-filter"); filter.replaceChildren();
  const all = element("option", "All fields"); all.value = ""; filter.append(all);
  [...new Set(report.findings.map((finding) => finding.field))].forEach((field) => { const option = element("option", field); option.value = field; filter.append(option); });
  renderFindings();
  $("#skipped-card").hidden = !report.skipped_check_count;
  $("#skipped-title").textContent = `${number(report.skipped_check_count)} skipped ${report.skipped_check_count === 1 ? "check" : "checks"}${report.skipped_check_count > 100 ? " · First 100 shown; full list in report bundle" : ""}`;
  const skippedBody = $("#skipped-table tbody"); skippedBody.replaceChildren();
  report.skipped_checks.slice(0, 100).forEach((finding) => { const row = element("tr"); [finding.record, finding.field, finding.rule, finding.reason].forEach((value) => row.append(element("td", value))); skippedBody.append(row); });
  $("#attribute-note").hidden = !summaries.some((item) => ["complexity", "verbosity"].includes(item.field.toLowerCase()));
  renderAuditReport(report.audit, urls);
  initializeSemanticReview();
}
function renderDistributions(summaries) {
  const container = $("#distributions"); container.replaceChildren();
  summaries.forEach((item) => {
    const card = element("div", null, "card distribution-card"); card.append(element("h3", item.field), element("p", `${number(item.counts.valid)} valid scores of ${number(item.counts.total)}`, "small subtle"));
    const stats = element("div", null, "distribution-stats");
    [["Mean", item.statistics.mean], ["Minimum", item.statistics.minimum], ["Maximum", item.statistics.maximum]].forEach(([label, value]) => { const stat = element("div", label); stat.append(element("strong", label === "Mean" ? number(value) : scoreText(value))); stats.append(stat); });
    card.append(stats);
    // Continuous ratings can have thousands of distinct values; keep this view readable.
    const distribution = item.distribution.length > 12 ? [...item.distribution].sort((a, b) => b.count - a.count).slice(0, 12) : item.distribution;
    distribution.forEach((bin) => {
      const share = item.counts.valid ? bin.count / item.counts.valid * 100 : 0;
      const row = element("div", null, "bar-row"), track = element("div", null, "bar-track"), bar = element("div", null, "bar");
      bar.style.width = `${share}%`; track.append(bar); track.setAttribute("aria-hidden", "true");
      row.append(element("span", scoreText(bin.value), "bar-value"), track, element("span", number(bin.count), "bar-count"), element("span", `${share.toFixed(1)}%`, "bar-share")); card.append(row);
    });
    if (!item.counts.valid) card.append(element("p", "No valid numeric scores to summarize.", "small subtle"));
    if (item.distribution.length > 12) card.append(element("p", "12 most frequent values shown. The complete distribution is in the JSON report.", "small subtle"));
    container.append(card);
  });
}
function renderFindings() {
  if (!state.result) return;
  const report = state.result.report;
  const field = $("#findings-filter").value;
  const findings = report.findings.filter((finding) => !field || finding.field === field);
  $("#findings-count").textContent = `${number(findings.length)} ${field ? "matching" : "total"} ${findings.length === 1 ? "finding" : "findings"} · ${number(report.affected_records)} affected ${report.affected_records === 1 ? "record" : "records"} overall`;
  $("#findings-wrap").hidden = !findings.length; $("#findings-empty").hidden = !!findings.length;
  $("#findings-empty").textContent = report.status === "passed" ? "No findings. All selected checks passed." : report.skipped_check_count ? "No findings in this view. Review the skipped checks before drawing conclusions." : "No findings in this view.";
  $("#findings-truncated").hidden = findings.length <= 100;
  const body = $("#findings-table tbody"); body.replaceChildren();
  findings.slice(0, 100).forEach((finding) => {
    const row = element("tr"); [finding.record, finding.field, finding.rule].forEach((value) => row.append(element("td", value)));
    const cell = element("td"), value = valueText(finding.value, finding.field_present !== false);
    cell.append(element("span", value.length > 180 ? `${value.slice(0, 179)}…` : value, "finding-value"));
    row.append(cell, element("td", finding.reason)); body.append(row);
  });
}
function startOver() {
  if (state.answerReviewRunning) return;
  resetSemanticReview();
  state.upload = null; state.result = null; state.shared = {}; state.rules = []; state.unique = false; state.audit = null;
  resetRubricImport();
  resetSingleTask();
  clearError(); $("#new-file").hidden = true; $("#file-input").value = ""; showStep("upload");
}
function renderDesktopShortcut(shortcut, announce = false) {
  const button = $("#desktop-shortcut");
  button.hidden = !shortcut?.supported;
  button.disabled = Boolean(shortcut?.installed);
  button.textContent = shortcut?.installed ? "Desktop shortcut added" : "Add desktop shortcut";
  button.title = shortcut?.installed ? "Open Data Quality Inspector from your desktop anytime." : "Create a shortcut that starts the inspector from your desktop.";
  const status = $("#shortcut-status");
  status.hidden = !(announce && shortcut?.installed);
  if (!status.hidden) status.textContent = "Desktop shortcut added. Double-click Data Quality Inspector on your desktop to open the app, even when it isn’t running.";
}
async function addDesktopShortcut() {
  if (state.busy || !state.bootstrap) return;
  clearError(); $("#shortcut-status").hidden = true;
  setBusy(true, "Adding your desktop shortcut…");
  try {
    const shortcut = await api("/api/desktop-shortcut", {method: "POST", headers: {"Content-Type": "application/json", "X-DQI-Token": state.bootstrap.csrf_token}, body: "{}"});
    state.bootstrap.desktop_shortcut = shortcut;
    renderDesktopShortcut(shortcut, true);
  } catch (error) { showError(error); }
  finally { setBusy(false); }
}
async function closeInspector() {
  if (state.busy || !state.bootstrap) return;
  $("#close-dialog").close();
  clearError(); setBusy(true, "Closing the inspector…");
  try {
    await api("/api/close", {method: "POST", headers: {"Content-Type": "application/json", "X-DQI-Token": state.bootstrap.csrf_token}, body: "{}"});
    leaveCredentialSettings();
    state.utility = null;
    workspacePosition = null;
    resetSemanticReview();
    startOver(); state.bootstrap = null;
    $("#upload-panel").hidden = true; $(".steps").hidden = true; $("#closed-panel").hidden = false;
    $("#close-app").hidden = true; $("#desktop-shortcut").hidden = true; $("#shortcut-status").hidden = true; $("#connection").textContent = "Session closed";
    $("#open-settings").hidden = true; $("#open-walkthrough").hidden = true;
  } catch (error) { showError(error); }
  finally { setBusy(false); }
}

$("#file-input").addEventListener("change", (event) => uploadFile(event.target.files[0]));
$("#load-demo").addEventListener("click", () => {
  const csv = "evaluation_id,prompt,response,rating,explanation,category\neval-001,What is 2 + 2?,4,5,Complete response,math\neval-002,What is 3 + 3?,,3,Response is missing,math\neval-003,Name the capital of France,Paris,4,First source rating,geography\neval-003,Name the capital of France,Paris,2,Same pair with a different rating,geography\neval-005,Name a primary color,Blue,7,Rating exceeds the allowed maximum,art\neval-006,Explain photosynthesis,Plants use light to make sugars,high,Rating is not numeric,science\n";
  uploadFile(new File([csv], "demo-evaluations.csv", {type: "text/csv"}), "csv");
});
$("#preset-select").addEventListener("change", (event) => applyPreset(event.target.value));
$("#add-rating").addEventListener("click", () => {
  const used = new Set([...state.rules.map((rule) => rule.field), ...Object.values(state.shared).map((field) => field.field)]);
  state.rules.push(makeRule(state.upload.columns.find((field) => !used.has(field)) || "")); renderRules(); edited();
});
$("#run-inspection").addEventListener("click", runInspection);
$("#findings-filter").addEventListener("change", renderFindings);
$("#edit-checks").addEventListener("click", () => showStep("checks"));
$("#browse-review-queue").addEventListener("click", () => openReportDetail("#audit-kind-filter"));
$("#browse-findings").addEventListener("click", () => openReportDetail("#findings-filter"));
$("#browse-skipped").addEventListener("click", () => {
  $("#skipped-card").open = true;
  openReportDetail("#skipped-title");
});
$("#new-file").addEventListener("click", startOver);
$("#desktop-shortcut").addEventListener("click", addDesktopShortcut);
for (const page of ["settings", "walkthrough"]) {
  $(`#open-${page}`).addEventListener("click", (event) => openUtility(page, event.currentTarget));
  $(`#${page}-back`).addEventListener("click", closeUtility);
}
$("main").addEventListener("click", (event) => {
  const link = event.target.closest("[data-open-settings], [data-open-walkthrough]");
  if (link) openUtility(link.hasAttribute("data-open-settings") ? "settings" : "walkthrough", link);
});
$("#close-app").addEventListener("click", () => { if (!state.busy && state.bootstrap) $("#close-dialog").showModal(); });
$("#cancel-close").addEventListener("click", () => $("#close-dialog").close());
$("#confirm-close").addEventListener("click", closeInspector);
$("#replace-file").addEventListener("click", () => { clearError(); showStep("upload"); });
document.querySelectorAll("[data-step]").forEach((button) => button.addEventListener("click", () => { clearError(); showStep(button.dataset.step); }));
const dropzone = $("#dropzone");
["dragenter", "dragover"].forEach((name) => dropzone.addEventListener(name, (event) => { event.preventDefault(); dropzone.classList.add("dragging"); }));
dropzone.addEventListener("dragleave", (event) => { if (!dropzone.contains(event.relatedTarget)) dropzone.classList.remove("dragging"); });
dropzone.addEventListener("drop", (event) => {
  event.preventDefault(); dropzone.classList.remove("dragging");
  if (event.dataTransfer.files.length !== 1) { showError(new Error("Choose one dataset file at a time.")); return; }
  uploadFile(event.dataTransfer.files[0]);
});
if (window.location.protocol === "file:") {
  $(".app-header").hidden = true;
  $("main").hidden = true;
  $("#file-help").hidden = false;
  // The launcher records its actual local address, including an alternate port.
  const targetScript = document.createElement("script");
  targetScript.src = "./launch-target.js";
  targetScript.onload = () => {
    try {
      const target = new URL(window.DQI_APP_URL);
      const port = Number(target.port || 80);
      if (target.protocol !== "http:" || target.hostname !== "127.0.0.1" || !Number.isInteger(port) || port < 1 || port > 65535 || target.username || target.password || target.pathname !== "/" || target.search || target.hash) return;
      $("#open-running-inspector").href = target.href;
      $("#open-running-inspector").hidden = false;
    } catch { /* The desktop shortcut remains the startup path. */ }
  };
  document.head.append(targetScript);
} else if (window.location.pathname !== "/tour-preview") {
  api("/api/bootstrap").then((data) => {
    state.bootstrap = data; $("#connection").textContent = "Local session";
    renderDesktopShortcut(data.desktop_shortcut);
    const restore = () => {
      const entry = window.location.hash.slice(1);
      if (entry === "single-task") showStep("single-task");
      initializeCredentialSettings();
      recoverSemanticReview(data.semantic);
      if (entry === "settings" || entry === "walkthrough") openUtility(entry);
      if (entry === "tour") openLinkedTour();
    };
    if (document.readyState === "loading" || typeof startGuidedTour !== "function") document.addEventListener("DOMContentLoaded", restore, {once: true});
    else restore();
    $("#upload-limit").textContent = `Up to ${data.max_upload_bytes / (1024 * 1024)} MB per file`;
  }).catch((error) => { $("#connection").textContent = "Unable to connect"; showError(error); });
}
