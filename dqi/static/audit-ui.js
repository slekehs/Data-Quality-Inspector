"use strict";

const auditReasonNames = {data_error: "Validation error", skipped_check: "Skipped validation check", duplicate: "Exact repeated pair", conflicting_rating: "Different ratings for the same pair", low_rating: "Source rating meets review threshold"};
const auditCheckNames = {exact_duplicates: "Exact repeated pairs", rating_conflicts: "Conflicting ratings", review_thresholds: "Rating review thresholds", category_coverage: "Category coverage", annotator_agreement: "Reviewer agreement", split_leakage: "Train / test overlap"};
const auditQueuePageSize = 50;
let auditQueuePage = 0;

function auditEdited() {
  state.result = null;
  refreshRubricImport();
  clearError(); updateCount(); updateNavigation();
}
function auditColumnOptions(select, value, placeholder) {
  select.replaceChildren();
  const blank = element("option", placeholder); blank.value = ""; select.append(blank);
  state.upload.columns.forEach((field) => { const option = element("option", field); option.value = field; select.append(option); });
  select.value = value;
}
function initializeAuditSettings(preset) {
  const thresholds = Object.create(null);
  state.rules.forEach((rule) => {
    if (rule.field) thresholds[rule.field] = {enabled: preset === "helpsteer2" && ["helpfulness", "correctness", "coherence"].includes(rule.field.toLowerCase()), limit: "1"};
  });
  state.audit = {enabled: Boolean(state.shared.response.field), prompt: column(["prompt", "question", "input", "instruction"]), duplicates: true, conflicts: true, thresholds, group: "", minimum: "20", expected: ""};
  $("#audit-enabled").checked = state.audit.enabled;
  $("#audit-duplicates").checked = true; $("#audit-conflicts").checked = true;
  auditColumnOptions($("#audit-prompt"), state.audit.prompt, "No question column selected");
  auditColumnOptions($("#audit-group"), "", "Do not check categories");
  $("#audit-minimum").value = "20"; $("#audit-expected").value = "";
  renderAuditRuleSource(); renderAuditThresholds(); updateAuditControls();
}
function updateAuditControls() {
  if (!state.audit) return;
  $("#audit-options").inert = !state.audit.enabled;
  $("#audit-options").setAttribute("aria-disabled", String(!state.audit.enabled));
  $("#audit-minimum").disabled = !state.audit.group;
  $("#audit-expected").disabled = !state.audit.group;
}
function renderAuditRuleSource() {
  const sources = {helpsteer2: "Data rules: the HelpSteer2 preset (required whole-number ratings from 0 to 4).", standard: "Data rules: the inspector's AI evaluation preset, adjusted to the available columns.", custom: "Data rules: your custom check settings."};
  $("#audit-rule-source").textContent = `${sources[state.ruleOrigin] || sources.custom} Ratings come from the uploaded file. The review thresholds below are editable inspection criteria, not new grades.`;
}
function renderAuditThresholds() {
  if (!state.audit) return;
  const container = $("#audit-thresholds"); container.replaceChildren();
  const fields = [...new Set(state.rules.filter((rule) => rule.field && [rule.required, rule.numeric, rule.integer, rule.rangeEnabled].some(Boolean)).map((rule) => rule.field))];
  fields.forEach((field) => {
    if (!Object.hasOwn(state.audit.thresholds, field)) state.audit.thresholds[field] = {enabled: false, limit: "1"};
    const setting = state.audit.thresholds[field];
    const row = element("div", null, "audit-threshold"), toggle = element("label", null, "checkbox-label");
    const check = element("input"); check.type = "checkbox"; check.checked = setting.enabled; check.setAttribute("aria-label", `Review ${field} scores`);
    toggle.append(check, element("span", field));
    const bound = element("label", "At or below");
    const input = element("input"); input.type = "number"; input.step = "any"; input.value = setting.limit; input.disabled = !setting.enabled; input.setAttribute("aria-label", `${field} review threshold`);
    check.addEventListener("change", () => { setting.enabled = check.checked; input.disabled = !check.checked; auditEdited(); });
    input.addEventListener("input", () => { setting.limit = input.value; auditEdited(); });
    bound.append(input); row.append(toggle, bound); container.append(row);
  });
  if (!fields.length) container.append(element("p", "Add an active rating field above to set a review threshold.", "small subtle"));
}
function auditConfiguration() {
  if (!state.audit?.enabled) return null;
  const settings = state.audit;
  if (!state.shared.response.field) throw new Error("Choose a Response column above so the audit can show the original answers, or turn off Include audit.");
  const thresholds = Object.create(null);
  const active = new Set(state.rules.filter((rule) => [rule.required, rule.numeric, rule.integer, rule.rangeEnabled].some(Boolean)).map((rule) => rule.field));
  Object.entries(settings.thresholds).forEach(([field, setting]) => {
    if (!setting.enabled || !active.has(field)) return;
    if (!setting.limit.trim() || !Number.isFinite(Number(setting.limit))) throw new Error(`Enter a finite review threshold for “${field}”.`);
    thresholds[field] = decimalLiteral(setting.limit);
  });
  let coverage = null;
  if (settings.group) {
    const minimum = Number(settings.minimum);
    if (!Number.isSafeInteger(minimum) || minimum < 1) throw new Error("Coverage minimum must be a whole number of at least 1.");
    coverage = {field: settings.group, minimum, expected_values: [...new Set(settings.expected.split(/\r?\n/).filter((value) => value.trim()))]};
  }
  return {prompt_field: settings.prompt || null, response_field: state.shared.response.field, duplicates: settings.duplicates, conflicts: settings.conflicts, review_thresholds: thresholds, coverage, rule_origin: state.ruleOrigin};
}
function serializeAuditOptions(options) {
  const thresholds = Object.entries(options.review_thresholds).map(([field, literal]) => `${JSON.stringify(field)}:${literal}`).join(",");
  const fields = Object.entries(options).map(([key, value]) => `${JSON.stringify(key)}:${key === "review_thresholds" ? `{${thresholds}}` : JSON.stringify(value)}`);
  return `{${fields.join(",")}}`;
}
function auditMetric(label, value) {
  const metric = element("div", null, "metric");
  metric.append(element("span", label, "metric-label"), element("strong", value, "metric-value"));
  return metric;
}
function renderAuditReport(audit, urls) {
  $("#audit-report").hidden = !audit;
  if (!audit) return;
  const gaps = audit.coverage?.gaps?.length || 0;
  const completed = (name) => audit.check_status?.some((check) => check.check === name && check.status === "completed");
  const hasCompletedCheck = audit.check_status?.some((check) => check.status === "completed");
  $("#audit-outcome").textContent = audit.review_record_count ? `${number(audit.review_record_count)} records ready for review` : gaps ? "Category coverage needs review" : hasCompletedCheck ? "No records matched the selected review rules" : "No deeper audit checks were completed";
  $("#audit-outcome-note").textContent = "The queue combines validation findings and review candidates. Existing ratings are evidence supplied by the file; this audit does not independently grade answers or identify which conflicting label is correct.";
  const metrics = $("#audit-metrics"); metrics.replaceChildren();
  [["Records to review", number(audit.review_record_count)], ["Repeated-pair groups", completed("exact_duplicates") ? number(audit.duplicate_group_count) : "Not assessed"], ["Conflicting-rating groups", completed("rating_conflicts") ? number(audit.conflict_group_count) : "Not assessed"], ["Category gaps", audit.coverage ? number(gaps) : "Not assessed"]].forEach(([label, value]) => metrics.append(auditMetric(label, value)));
  const provenance = $("#audit-provenance"); provenance.replaceChildren();
  const originNames = {helpsteer2: "HelpSteer2 preset", standard: "Inspector AI evaluation preset", custom: "User-customized validation settings"};
  provenance.append(element("p", `Validation rule source: ${originNames[audit.options.rule_origin] || audit.options.rule_origin}. The complete selected rules are saved in the report.`));
  if (audit.source) {
    provenance.append(element("p", `Source: ${audit.source.filename} · Inspector ${audit.source.app_version} · ${audit.source.inspected_at}`));
    const fingerprint = element("p", "File fingerprint (SHA-256): "); fingerprint.append(element("code", audit.source.sha256)); provenance.append(fingerprint);
  }
  Object.entries(audit.provenance || {}).filter(([key]) => key !== "rule_origin").forEach(([, value]) => provenance.append(element("p", typeof value === "string" ? value : JSON.stringify(value))));
  const thresholds = Object.entries(audit.options.review_thresholds || {}).map(([field, limit]) => `${field} ≤ ${scoreText(limit)}`);
  provenance.append(element("p", thresholds.length ? `Selected review thresholds: ${thresholds.join("; ")}. These are review criteria, not failures of the rating scale.` : "No rating thresholds selected. The audit does not infer what score is good for an unfamiliar attribute."));
  if (audit.options.rule_origin === "helpsteer2") {
    const p = element("p", "Preset reference: "); const link = element("a", "NVIDIA HelpSteer2 dataset documentation"); link.href = "https://huggingface.co/datasets/nvidia/HelpSteer2"; link.target = "_blank"; link.rel = "noopener noreferrer"; p.append(link); provenance.append(p);
  }
  const checks = $("#audit-checks"); checks.replaceChildren();
  (audit.check_status || []).forEach((check) => {
    const row = element("div", null, "audit-check");
    const eligibility = check.status === "completed" ? [check.eligible_records != null ? `Eligible records: ${number(check.eligible_records)}; excluded: ${number(check.skipped_records || 0)}.` : "", check.eligible_values != null ? `Eligible rating values: ${number(check.eligible_values)}; excluded: ${number(check.skipped_values || 0)}.` : ""].filter(Boolean).join(" ") : "";
    row.append(element("strong", auditCheckNames[check.check] || check.check), element("span", {completed: "Completed", not_selected: "Not selected", not_assessed: "Not assessed"}[check.status] || check.status, `audit-state ${check.status}`), element("p", `${check.reason || ""} ${eligibility}`.trim(), "subtle")); checks.append(row);
  });
  renderAuditCoverage(audit.coverage);
  $("#download-review").href = urls.review || urls.json; $("#download-review").textContent = urls.review ? "Download full review queue" : "Download complete report";
  const kindSelect = $("#audit-kind-filter"), fieldSelect = $("#audit-field-filter"); kindSelect.replaceChildren(); fieldSelect.replaceChildren();
  const allKinds = element("option", "All reasons"); allKinds.value = ""; kindSelect.append(allKinds);
  const allFields = element("option", "All attributes"); allFields.value = ""; fieldSelect.append(allFields);
  const kinds = new Set(), fields = new Set();
  audit.review_queue.forEach((item) => item.reasons.forEach((reason) => { kinds.add(reason.kind); if (reason.field) fields.add(reason.field); }));
  kinds.forEach((kind) => { const option = element("option", auditReasonNames[kind] || kind); option.value = kind; kindSelect.append(option); });
  fields.forEach((field) => { const option = element("option", field); option.value = field; fieldSelect.append(option); });
  renderAuditQueue();
  const examples = $("#audit-examples"); examples.replaceChildren();
  $("#audit-positive-card").hidden = !audit.positively_rated_examples?.length;
  (audit.positively_rated_examples || []).forEach((item) => examples.append(auditRecordCard(item, `Record ${item.record} · ${item.field}: ${scoreText(item.value)}`, item.selection_reason)));
}
function renderAuditCoverage(coverage) {
  const container = $("#audit-coverage"); container.replaceChildren();
  if (!coverage) { container.append(element("p", "Category coverage was not assessed. Choose a category column and your coverage targets under Edit checks to assess it. Topic coverage is not guessed from answer text.", "coverage-note")); return; }
  container.append(element("h3", `Coverage of ${coverage.field}`));
  container.append(element("p", `Target: at least ${number(coverage.minimum)} records per category. ${number(coverage.eligible_records)} records had usable category labels; ${number(coverage.missing_records)} were missing and ${number(coverage.unsupported_records)} could not be grouped.`, "subtle"));
  if (!coverage.expected_values.length) container.append(element("p", "Only observed categories were checked. Add expected category labels to detect groups that are absent from this file.", "small subtle"));
  const scroll = element("div", null, "table-scroll audit-coverage-table"), table = element("table"), head = element("thead"), heading = element("tr"), body = element("tbody");
  ["Category", "Records", "Target", "Result"].forEach((label) => heading.append(element("th", label))); head.append(heading);
  const groups = [...coverage.groups];
  coverage.gaps.forEach((gap) => { if (!groups.some((group) => group.value === gap.value)) groups.push({value: gap.value, count: gap.count, expected: true, below_minimum: true}); });
  groups.slice(0, 100).forEach((group) => {
    const row = element("tr", null, group.below_minimum ? "audit-gap" : "");
    [group.value.trim() === group.value ? group.value : JSON.stringify(group.value), number(group.count), number(coverage.minimum), group.below_minimum ? "Below configured target" : "Meets configured target"].forEach((value) => row.append(element("td", value))); body.append(row);
  });
  table.append(head, body); scroll.append(table); container.append(scroll);
  if (groups.length > 100) container.append(element("p", "First 100 categories shown. The complete counts are in the JSON report.", "small subtle"));
  container.append(element("p", "Meeting these targets does not establish that the dataset represents every real-world use case.", "small subtle"));
}
function auditRecordCard(item, title, note = "") {
  const card = element("details", null, "review-record"), summary = element("summary", title);
  if (note) summary.append(element("span", note, "review-summary-note"));
  const content = element("div", null, "review-context");
  if (item.source_line != null) content.append(element("p", `Source line ${item.source_line}${item.evaluation_id != null ? ` · Evaluation ID: ${valueText(item.evaluation_id)}` : ""}`, "small subtle"));
  if (item.reasons?.length) {
    const reasons = element("ul", null, "review-reasons");
    item.reasons.forEach((reason) => {
      const entry = element("li");
      entry.append(element("strong", `${auditReasonNames[reason.kind] || reason.kind}${reason.field ? ` · ${reason.field}` : ""}`));
      entry.append(element("p", reason.reason));
      if (reason.kind === "conflicting_rating") {
        const group = state.result?.report.audit?.conflict_groups.find((group) => group.group_id === reason.group_id);
        const difference = group?.differences.find((difference) => difference.field === reason.field);
        (difference?.values || []).slice(0, 10).forEach((bucket) => {
          entry.append(element("p", `Source score ${scoreText(bucket.value)}: records ${bucket.records.slice(0, 10).join(", ")}${bucket.records.length > 10 ? `… (${number(bucket.records.length)} total)` : ""}`, "small"));
        });
        if (difference?.values.length > 10) entry.append(element("p", "First 10 distinct scores shown; all differences are in the JSON report.", "small subtle"));
      }
      if (reason.action) entry.append(element("p", `Next step: ${reason.action}`, "next-action"));
      if (reason.related_records?.length) entry.append(element("p", `Related records: ${reason.related_records.slice(0, 30).join(", ")}${(reason.related_record_count || reason.related_records.length) > Math.min(reason.related_records.length, 30) ? `… (${number(reason.related_record_count || reason.related_records.length)} total; full group in JSON)` : ""}`, "small subtle"));
      reasons.append(entry);
    }); content.append(reasons);
  }
  const ratings = element("div", null, "source-ratings");
  Object.entries(item.ratings || {}).forEach(([field, value]) => ratings.append(element("span", `${field}: ${valueText(value)}`, "source-rating")));
  content.append(element("h3", "Original ratings"), ratings);
  content.append(element("h3", "Original question / prompt"), element("pre", item.prompt == null ? "No question column was mapped or this record has no question." : valueText(item.prompt), "source-text"));
  content.append(element("h3", "Original response"), element("pre", item.response == null ? "Response is missing." : valueText(item.response), "source-text"));
  card.append(summary, content); return card;
}
function updateFilteredReviewDownload(kind, field, hasMatches) {
  const link = $("#download-filtered-review");
  const {review} = state.result.urls;
  const filtered = Boolean((kind || field) && hasMatches);
  const canDownload = state.bootstrap?.filtered_review_csv === true && Boolean(review);
  link.hidden = !filtered || !canDownload;
  $("#audit-download-upgrade").hidden = !filtered || canDownload || window.location.pathname === "/tour-preview";
  if (link.hidden) {
    link.removeAttribute("href");
    return;
  }
  const url = new URL(review, window.location.href);
  if (kind) url.searchParams.set("reason", kind);
  if (field) url.searchParams.set("attribute", field);
  link.href = url.href;
}
function renderAuditQueue(page = 0) {
  const audit = state.result?.report.audit;
  if (!audit) return;
  const kind = $("#audit-kind-filter").value, field = $("#audit-field-filter").value;
  const matches = (reason) => (!kind || reason.kind === kind) && (!field || reason.field === field);
  const records = kind || field ? audit.review_queue.filter((item) => item.reasons.some(matches)) : audit.review_queue;
  const pages = Math.ceil(records.length / auditQueuePageSize);
  auditQueuePage = Math.max(0, Math.min(page, pages - 1));
  const start = auditQueuePage * auditQueuePageSize;
  const end = Math.min(start + auditQueuePageSize, records.length);
  const recordLabel = records.length === 1 ? "record" : "records";
  const total = `${number(records.length)} ${kind || field ? "matching " : ""}${recordLabel}`;
  const range = end === start + 1 ? number(end) : `${number(start + 1)}–${number(end)}`;
  $("#audit-queue-count").textContent = records.length ? `Showing ${range} of ${total}` : total;
  $("#audit-queue-pager").hidden = pages <= 1;
  $("#audit-queue-previous").disabled = auditQueuePage === 0;
  $("#audit-queue-next").disabled = end === records.length;
  updateFilteredReviewDownload(kind, field, records.length > 0);
  const container = $("#audit-queue"); container.replaceChildren();
  if (!records.length) container.append(element("p", audit.review_record_count ? "No records match these filters." : "No records meet the selected review criteria. Check unavailable checks and any category gaps before drawing conclusions.", "empty-state"));
  records.slice(start, end).forEach((item, index) => {
    const firstReason = item.reasons.find(matches);
    const summary = `${item.priority === "high" ? "Validation issue" : "Review candidate"} · ${item.reasons.length} ${item.reasons.length === 1 ? "reason" : "reasons"}`;
    const card = auditRecordCard(item, `Record ${item.record} · ${summary}`, firstReason?.reason || "");
    card.open = index === 0; container.append(card);
  });
}
function changeAuditQueuePage(page) {
  renderAuditQueue(page);
  $("#audit-queue summary")?.focus({preventScroll: true});
  $("#audit-queue-title").scrollIntoView({block: "nearest"});
}

$("#audit-enabled").addEventListener("change", (event) => { if (!state.audit) return; state.audit.enabled = event.target.checked; updateAuditControls(); auditEdited(); });
$("#audit-prompt").addEventListener("change", (event) => { state.audit.prompt = event.target.value; auditEdited(); });
$("#audit-duplicates").addEventListener("change", (event) => { state.audit.duplicates = event.target.checked; auditEdited(); });
$("#audit-conflicts").addEventListener("change", (event) => { state.audit.conflicts = event.target.checked; auditEdited(); });
$("#audit-group").addEventListener("change", (event) => { state.audit.group = event.target.value; updateAuditControls(); auditEdited(); });
$("#audit-minimum").addEventListener("input", (event) => { state.audit.minimum = event.target.value; auditEdited(); });
$("#audit-expected").addEventListener("input", (event) => { state.audit.expected = event.target.value; auditEdited(); });
$("#audit-kind-filter").addEventListener("change", () => renderAuditQueue());
$("#audit-field-filter").addEventListener("change", () => renderAuditQueue());
$("#audit-queue-previous").addEventListener("click", () => changeAuditQueuePage(auditQueuePage - 1));
$("#audit-queue-next").addEventListener("click", () => changeAuditQueuePage(auditQueuePage + 1));
