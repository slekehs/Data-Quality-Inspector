"use strict";

const semanticState = {reportId: null, preview: null, options: null, rates: null, revision: 0, jobId: null, requestId: null, timer: null, rubrics: [], polling: false, primaryJobId: null, opinion: null, pendingParent: null, pendingMode: null, mode: "dataset"};
const semanticLabels = {supported: "Supported by this reviewer", close: "Close to the existing score", questionable: "Questionable rating", insufficient_evidence: "Insufficient evidence", unscored: "Existing rating cannot be compared"};

function chosenModel(prefix) {
  const selection = $(`#${prefix}-model`).value;
  return selection === "custom" ? $(`#${prefix}-custom-model`).value.trim() : selection;
}
function validModel(model) { return /^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/.test(model); }
function modelName(model) {
  const option = Array.from($("#semantic-model").options).find((item) => item.value !== "custom" && item.value === model);
  return option?.textContent || model || "Reviewer";
}
function opinionReady() {
  const model = chosenModel("opinion");
  const prior = semanticState.opinion?.models || [];
  return !state.answerReviewRunning && Boolean(semanticState.opinion) && validModel(model) &&
    !prior.some((name) => name.toLowerCase() === model.toLowerCase()) &&
    Boolean(reviewCredential("opinion")) && $("#opinion-consent").checked;
}
function openSecondOpinion(parentId, row, result) {
  if (state.answerReviewRunning || !parentId || parentId !== semanticState.primaryJobId) return;
  const models = [result.model, ...(result.actual_models || [])].filter(Boolean);
  semanticState.opinion = {parentId, record: row.record, models};
  const menu = $("#opinion-model");
  menu.replaceChildren(...Array.from($("#semantic-model").options, (option) => option.cloneNode(true))
    .filter((option) => option.value === "custom" || !models.includes(option.value)));
  $("#opinion-custom-model").value = ""; $("#opinion-custom-label").hidden = menu.value !== "custom";
  clearReviewCredential("opinion"); $("#opinion-start").disabled = true;
  $("#opinion-title").textContent = `Second opinion for record ${row.record}`;
  $("#opinion-scope").textContent = `First reviewer: ${modelName(result.model)}. The second reviewer gets the same question, answer, reference if supplied, and rating criteria. The scores from the dataset and the first reviewer stay hidden.`;
  if (result.review_mode === "single_task") {
    $("#opinion-title").textContent = "Second opinion on this task";
    $("#opinion-scope").textContent = `The second reviewer will evaluate the same prompt, response, and rubric independently of ${modelName(result.model)}.`;
  }
  $("#opinion-dialog").showModal();
}
function semanticMessage(text, error = false) {
  const node = $("#semantic-message"); node.textContent = text; node.hidden = !text; node.className = `message ${error ? "error" : "success"}`;
}
function semanticHeaders() { return {"Content-Type": "application/json", "X-DQI-Token": state.bootstrap.csrf_token}; }
function semanticBusy(running) {
  state.answerReviewRunning = running;
  $("#semantic-settings").disabled = running;
  $("#new-file").disabled = running; $("#edit-checks").disabled = running;
  $("#upload-panel").inert = running; $("#checks-panel").inert = running;
  singleTaskBusy(running);
  document.querySelectorAll(".opinion-trigger").forEach((button) => { button.disabled = running; });
  updateNavigation();
}
function resetSemanticReview() {
  clearTimeout(semanticState.timer); semanticState.timer = null; semanticState.jobId = null; semanticState.polling = false;
  semanticState.reportId = null; semanticState.primaryJobId = null;
  semanticInvalidate();
  semanticState.opinion = null;
  semanticState.mode = "dataset";
  if ($("#opinion-dialog").open) $("#opinion-dialog").close();
  $("#opinion-comparison").hidden = true; $("#opinion-comparison").replaceChildren();
  $("#semantic-result-pages").replaceChildren();
  $("#semantic-result-rows").replaceChildren();
  clearReviewCredential("semantic");
  $("#semantic-panel").hidden = true; $("#semantic-progress").hidden = true; $("#semantic-results").hidden = true;
  semanticBusy(false);
}
function semanticSelectionControls() {
  const all = $("#semantic-scope-all").checked;
  $("#semantic-selection-label").hidden = all;
  $("#semantic-limit-label").hidden = all;
  $("#semantic-records-label").hidden = all || $("#semantic-selection").value !== "records";
  const model = chosenModel("semantic");
  const catalog = state.bootstrap?.semantic?.model_rates;
  $("#semantic-custom-rates").hidden = !catalog || !model || Object.hasOwn(catalog, model);
}
function semanticInvalidate() {
  semanticState.revision++;
  semanticState.preview = null; semanticState.options = null; semanticState.rates = null;
  $("#semantic-preview-result").hidden = true; $("#semantic-consent").checked = false;
  $("#semantic-authorization").hidden = true;
  $("#semantic-preview").textContent = "Estimate review cost";
  $("#semantic-preview").disabled = false;
  semanticSelectionControls(); semanticStartEnabled();
}
function semanticStartEnabled() {
  const preview = semanticState.preview;
  $("#semantic-start").disabled = state.answerReviewRunning || !preview?.quote_id || !Number(preview.eligible_count) ||
    preview.estimate?.model !== chosenModel("semantic") || !reviewCredential("semantic") || !$("#semantic-consent").checked;
}
function semanticColumnOptions(id, selected, placeholder) {
  const node = $(id); node.replaceChildren();
  const blank = element("option", placeholder); blank.value = ""; node.append(blank);
  state.upload.columns.forEach((name) => { const option = element("option", name); option.value = name; node.append(option); });
  node.value = selected || "";
}
function semanticRubricForField(summary, rule, defaults) {
  const {field, rules} = summary;
  const imported = rule?.importedRubric;
  const compatible = rules.integer && Number(rules.range?.[0]) === 0 && Number(rules.range?.[1]) === 4;
  const preset = !imported && compatible && rule?.presetField === field && Object.hasOwn(defaults, field.toLowerCase()) ? defaults[field.toLowerCase()] : null;
  let description = "";
  if (imported) description = imported.description;
  else if (preset) description = [preset.description, Object.entries(preset.levels || {}).map(([score, text]) => `${score}: ${text}`).join("\n")].join("\n\n");
  return {
    field, enabled: Boolean(imported || preset), description,
    source: imported ? `${imported.filename} · ${imported.sourceField}` : preset ? "HelpSteer2" : "",
    fromPreset: Boolean(preset), min: scoreText(rules.range?.[0] ?? 0), max: scoreText(rules.range?.[1] ?? 4),
    integer: rules.integer, fixedRange: Boolean(rules.range)
  };
}
function initializeSemanticReview() {
  resetSemanticReview();
  if (!state.bootstrap?.semantic) return;
  const panel = $("#semantic-panel"); $("#report-panel").insertBefore(panel, $("#audit-report")); panel.hidden = false;
  semanticState.reportId = state.result.report_id;
  semanticMessage(""); $("#semantic-setup").hidden = false; $("#semantic-setup").open = true;
  $("#semantic-preview-result").hidden = true; $("#semantic-start").disabled = true;
  const defaultModel = state.bootstrap.semantic.default_model;
  const presetModel = Array.from($("#semantic-model").options).some((option) => option.value === defaultModel);
  $("#semantic-model").value = presetModel ? defaultModel : "custom";
  $("#semantic-custom-model").value = presetModel ? "" : defaultModel;
  $("#semantic-custom-label").hidden = presetModel;
  $("#semantic-input-rate").value = ""; $("#semantic-output-rate").value = "";
  $("#semantic-scope-selected").checked = true;
  $("#semantic-limit").value = "5"; $("#semantic-limit").max = state.bootstrap.semantic.max_records || 20;
  $("#semantic-tolerance").value = "1"; $("#semantic-selection").value = "first"; $("#semantic-records").value = "";
  semanticColumnOptions("#semantic-prompt", state.result.report.audit?.options.prompt_field || column(["prompt", "question", "input", "instruction"]), "Choose the question column");
  semanticColumnOptions("#semantic-response", state.shared.response.field, "Choose the answer column");
  semanticColumnOptions("#semantic-reference", "", "No reference available");
  semanticSelectionControls();
  const defaults = state.bootstrap.semantic.helpsteer_rubrics || {};
  semanticState.rubrics = state.result.report.rating_summaries.map((summary) =>
    semanticRubricForField(summary, state.rules.find((rule) => rule.field === summary.field), defaults));
  const note = $("#semantic-rubric-note");
  const imported = semanticState.rubrics.filter((rule) => rule.source && !rule.fromPreset).length;
  note.textContent = imported
    ? `Your rubric supplied criteria for ${imported} ${imported === 1 ? "field" : "fields"}. Expand a field to read or edit the criteria before review.`
    : "Expand a field to review its criteria or enter your own. You can also upload a rubric under Edit checks.";
  if (semanticState.rubrics.some((rule) => rule.fromPreset)) {
    const link = element("a", "HelpSteer2 annotation guidelines"); link.href = "https://arxiv.org/html/2406.08673v1#A7.SS3.SSS1"; link.target = "_blank"; link.rel = "noopener noreferrer";
    note.append(" The HelpSteer2 criteria describe complexity and verbosity in terms of depth and length. ", link);
  }
  renderSemanticRubrics();
}
function renderSemanticRubrics() {
  const parent = $("#semantic-rubrics"); parent.replaceChildren();
  semanticState.rubrics.forEach((rule) => {
    const card = element("details", null, "semantic-rubric"), title = element("summary", rule.field);
    const label = element("label", null, "checkbox-label"), enabled = element("input"); enabled.type = "checkbox"; enabled.checked = rule.enabled; enabled.setAttribute("aria-label", `Review answer attribute ${rule.field}`);
    label.append(enabled, element("span", "Include this attribute"));
    const descriptionLabel = element("label", "Criteria and score meanings"), description = element("textarea"); description.value = rule.description; description.setAttribute("aria-label", `${rule.field} answer rubric`); description.placeholder = "Explain what the attribute measures and what each score means."; descriptionLabel.append(description);
    const scales = element("div", null, "semantic-grid");
    ["min", "max"].forEach((key) => {
      const boundLabel = element("label", key === "min" ? "Minimum score" : "Maximum score"), input = element("input"); input.type = "number"; input.step = "any"; input.value = rule[key]; input.setAttribute("aria-label", `${rule.field} answer ${key}`);
      input.disabled = rule.fixedRange;
      input.addEventListener("input", () => { rule[key] = input.value; }); boundLabel.append(input); scales.append(boundLabel);
    });
    const integerLabel = element("label", null, "checkbox-label"), integer = element("input"); integer.type = "checkbox"; integer.checked = rule.integer; integer.disabled = true; integer.setAttribute("aria-label", `${rule.field} whole-number suggestions`); integerLabel.append(integer, element("span", "Whole-number suggestions")); scales.append(integerLabel);
    description.addEventListener("input", () => { rule.description = description.value; });
    integer.addEventListener("change", () => { rule.integer = integer.checked; });
    enabled.addEventListener("change", () => { rule.enabled = enabled.checked; });
    card.append(title, label);
    if (rule.source) card.append(element("p", `Criteria from ${rule.source}`, "small subtle"));
    card.append(descriptionLabel, scales, element("p", "The scale and whole-number setting follow the data inspection. To change configured rules, use Edit checks and rerun the inspection first.", "small subtle")); parent.append(card);
  });
  if (!semanticState.rubrics.length) parent.append(element("p", "Add a rating attribute under Edit checks and rerun the data inspection first.", "subtle"));
}
function semanticOptions() {
  const selection = $("#semantic-scope-all").checked ? "all" : $("#semantic-selection").value;
  const limit = Number($("#semantic-limit").value), tolerance = Number($("#semantic-tolerance").value);
  if (selection !== "all" && (!Number.isSafeInteger(limit) || limit < 1 || limit > 20)) throw new Error("Choose between 1 and 20 records for a selected batch, or choose Whole dataset.");
  if (!$("#semantic-tolerance").value.trim() || !Number.isFinite(tolerance) || tolerance < 0) throw new Error("Enter a nonnegative close-score gap.");
  const rubrics = semanticState.rubrics.filter((rule) => rule.enabled).map((rule) => {
    const min = Number(rule.min), max = Number(rule.max);
    if (!rule.description.trim()) throw new Error(`Enter the rating criteria for ${rule.field}.`);
    if (!rule.min.trim() || !rule.max.trim() || !Number.isFinite(min) || !Number.isFinite(max) || min > max) throw new Error(`Enter a valid rating scale for ${rule.field}.`);
    return {field: rule.field, description: rule.description, min: decimalLiteral(rule.min), max: decimalLiteral(rule.max), integer: Boolean(rule.integer)};
  });
  if (!rubrics.length) throw new Error("Select at least one answer-review attribute and provide its rubric.");
  const records = selection === "records" ? $("#semantic-records").value.split(/[\s,]+/).filter(Boolean).map((value) => {
    if (!/^\d+$/.test(value) || !Number.isSafeInteger(Number(value)) || Number(value) < 1) throw new Error("Record numbers must be positive whole numbers separated by commas."); return Number(value);
  }) : [];
  if (!$("#semantic-prompt").value || !$("#semantic-response").value) throw new Error("Choose both the question and answer columns.");
  const options = {prompt_field: $("#semantic-prompt").value, response_field: $("#semantic-response").value, reference_field: $("#semantic-reference").value || null, selection, records, rubrics, tolerance: decimalLiteral($("#semantic-tolerance").value)};
  if (selection !== "all") options.limit = limit;
  return options;
}
function serializeReviewRubrics(rubrics) {
  return rubrics.map((rule) => `{${Object.entries(rule).map(([key, value]) => `${JSON.stringify(key)}:${["min", "max"].includes(key) ? decimalLiteral(String(value)) : JSON.stringify(value)}`).join(",")}}`).join(",");
}
function semanticRequestBody(payload) {
  const options = payload.options;
  const rubrics = serializeReviewRubrics(options.rubrics);
  const serialized = `{${Object.entries(options).map(([key, value]) => `${JSON.stringify(key)}:${key === "tolerance" ? decimalLiteral(String(value)) : key === "rubrics" ? `[${rubrics}]` : JSON.stringify(value)}`).join(",")}}`;
  return `{${Object.entries(payload).map(([key, value]) => `${JSON.stringify(key)}:${key === "options" ? serialized : JSON.stringify(value)}`).join(",")}}`;
}
async function previewSemanticReview() {
  if (state.answerReviewRunning) return;
  semanticMessage(""); semanticInvalidate();
  const revision = semanticState.revision;
  try {
    const model = chosenModel("semantic");
    if (!validModel(model)) throw new Error("Enter a valid review model ID.");
    if (!state.bootstrap?.semantic?.model_rates) throw new Error("Reopen Data Quality Inspector from your desktop to estimate the review cost.");
    const options = semanticOptions();
    const payload = {report_id: semanticState.reportId, options, model};
    if (!Object.hasOwn(state.bootstrap.semantic.model_rates, model)) {
      const input = $("#semantic-input-rate").value, output = $("#semantic-output-rate").value;
      if (![input, output].every(rate => Number.isFinite(Number(rate)) && Number(rate) > 0)) {
        throw new Error("Enter positive input and output prices for this model, in USD per million tokens.");
      }
      payload.rates = {input_per_million: input, output_per_million: output};
    }
    $("#semantic-preview").disabled = true;
    $("#semantic-preview").textContent = "Estimating…";
    const preview = await api("/api/answer-review/preview", {method: "POST", headers: semanticHeaders(), body: semanticRequestBody(payload)});
    if (revision !== semanticState.revision) return;
    if (preview.estimate?.model !== model || preview.estimate.currency !== "USD" ||
        preview.estimate.estimated_usd == null || !Number.isFinite(Number(preview.estimate.estimated_usd)) ||
        (Number(preview.eligible_count) > 0 && !preview.quote_id)) throw new Error("The cost estimate is unavailable. Estimate the review again.");
    renderReviewEstimate(preview, options);
    semanticState.preview = preview;
    semanticState.options = options;
    semanticState.rates = payload.rates;
  } catch (error) {
    if (revision === semanticState.revision) semanticMessage(error.message, true);
  } finally {
    if (revision === semanticState.revision) {
      $("#semantic-preview").textContent = "Estimate review cost";
      $("#semantic-preview").disabled = false;
      semanticStartEnabled();
    }
  }
}
function displayReviewCost(amount) {
  const value = Number(amount);
  if (value > 0 && value < 0.01) return "under $0.01";
  return value.toLocaleString("en-US", {style: "currency", currency: "USD"});
}
function renderReviewEstimate(preview, options) {
  const price = preview.estimate;
  const scope = options.selection === "all" ? "Whole dataset" : "Selected records";
  const cost = displayReviewCost(price.estimated_usd);
  const node = $("#semantic-preview-result");
  node.replaceChildren(element("strong", `Estimated cost · ${cost} USD`));
  node.append(element("p", `${scope} · ${number(preview.selected_count)} of ${number(preview.total_records)} records · ${number(preview.eligible_count)} eligible requests · ${number(preview.skipped_count)} skipped`));
  node.append(element("p", `${modelName(price.model)} · ${options.rubrics.length} ${options.rubrics.length === 1 ? "criterion" : "criteria"}: ${options.rubrics.map(rule => rule.field).join(", ")}`));
  if (options.selection !== "all") node.append(element("p", `Record numbers: ${preview.record_numbers.join(", ")}.`));
  if (preview.skipped_count) node.append(element("p", "Records with missing text or content over the review size limit will be skipped."));
  node.append(element("p", `At the output limit: approximately ${displayReviewCost(price.output_limit_usd)} USD. Actual charges depend on token use and your API rates.`));
  const detail = element("details");
  detail.append(element("summary", "How the cost was estimated"));
  detail.append(element("p", `Estimated tokens: ${number(price.estimated_input_tokens)} input and ${number(price.estimated_output_tokens)} output. Output limit across eligible requests: ${number(price.max_output_tokens)} tokens.`));
  detail.append(element("p", `${price.rate_source} · USD per million tokens: $${price.input_per_million} input and $${price.output_per_million} output.`));
  if (price.pricing_url) {
    const link = element("a", `OpenAI pricing${price.rates_date ? " · " + price.rates_date : ""}`);
    link.href = price.pricing_url; link.target = "_blank"; link.rel = "noopener noreferrer";
    detail.append(link);
  }
  detail.append(element("p", price.assumptions));
  node.append(detail); node.hidden = false;
  $("#semantic-consent-text").textContent = `I authorize sending the eligible questions, answers, rubrics and optional references to OpenAI using my API credits. Estimated cost for ${modelName(price.model)}: ${cost} USD.`;
  $("#semantic-authorization").hidden = !Number(preview.eligible_count);
}
function rememberPending(id, parentId, mode = null) {
  semanticState.requestId = id; semanticState.pendingParent = parentId;
  semanticState.pendingMode = mode;
  try {
    if (id) sessionStorage.setItem("dqi-pending-answer-review", id);
    else sessionStorage.removeItem("dqi-pending-answer-review");
    if (parentId) sessionStorage.setItem("dqi-pending-review-parent", parentId);
    else sessionStorage.removeItem("dqi-pending-review-parent");
    if (id && mode) sessionStorage.setItem("dqi-pending-review-mode", mode);
    else sessionStorage.removeItem("dqi-pending-review-mode");
  } catch { /* Nonsecret identifiers remain in page memory. */ }
}
async function startSemanticReview() {
  const preview = semanticState.preview;
  if (state.answerReviewRunning || !preview?.quote_id || !Number(preview.eligible_count) || !semanticState.options) return;
  const model = chosenModel("semantic");
  const credential = reviewCredential("semantic");
  if (!validModel(model) || model !== preview.estimate?.model || !$("#semantic-consent").checked || !credential) return;
  const payload = {report_id: semanticState.reportId, options: semanticState.options, model, quote_id: preview.quote_id, ...credential, consent: true};
  if (semanticState.rates) payload.rates = semanticState.rates;
  clearReviewCredential("semantic");
  semanticInvalidate();
  await submitReview(payload);
}
async function startSecondOpinion(event) {
  event.preventDefault();
  if (!opinionReady()) return;
  const {parentId, record} = semanticState.opinion;
  const payload = {parent_job_id: parentId, record: Number(record), model: chosenModel("opinion"), ...reviewCredential("opinion"), consent: true};
  clearReviewCredential("opinion");
  $("#opinion-dialog").close();
  await submitReview(payload);
}
function mountTaskReview() {
  const panel = $("#semantic-panel");
  if (semanticState.mode !== "single_task") {
    $("#semantic-results").hidden = true;
    $("#opinion-comparison").hidden = true;
    semanticState.primaryJobId = null;
  }
  semanticState.mode = "single_task";
  if (state.step !== "single-task") showStep("single-task", true);
  $("#single-task-review-host").append(panel);
  $("#semantic-setup").hidden = true;
  panel.hidden = false;
}
async function submitReview(payload, mode = semanticState.mode) {
  if (state.answerReviewRunning) return;
  const parentId = payload.parent_job_id || null;
  const reuseId = semanticState.pendingParent === parentId && semanticState.pendingMode === mode && semanticState.requestId;
  const requestId = reuseId || crypto.randomUUID();
  if (mode === "single_task") mountTaskReview();
  rememberPending(requestId, parentId, mode);
  semanticMessage(""); semanticBusy(true); semanticState.jobId = null;
  try {
    const request = {...payload, request_id: requestId};
    const task = !parentId && mode === "single_task";
    const url = parentId ? "/api/answer-review/second-opinion" : task ? "/api/single-task/review" : "/api/answer-review";
    const body = parentId ? JSON.stringify(request) : task ? singleTaskRequestBody(request) : semanticRequestBody(request);
    const data = await api(url, {method: "POST", headers: semanticHeaders(), body});
    rememberPending(null, null);
    semanticState.jobId = data.job_id;
    if (!parentId) { semanticState.primaryJobId = null; $("#semantic-results").hidden = true; }
    $("#opinion-comparison").hidden = true; $("#semantic-setup").open = false;
    $("#semantic-progress").hidden = false; $("#semantic-progress").scrollIntoView({block: "nearest"});
    $("#semantic-cancel").disabled = false; $("#semantic-resume-poll").hidden = true;
    await pollSemanticReview();
  } catch (error) {
    if (error.status && error.status < 500) {
      rememberPending(null, null); semanticBusy(false); semanticMessage(error.message, true);
      if (payload.use_saved_key) refreshKeyStatus();
      if (!parentId) $("#semantic-setup").open = true;
    } else {
      semanticMessage("Checking whether the review was accepted.", true);
      await recoverPendingSemanticReview();
    }
  }
  finally { semanticStartEnabled(); }
}
async function loadPrimaryReview(id) {
  if (!id || semanticState.primaryJobId === id) return;
  const data = await api(`/api/answer-review/${encodeURIComponent(id)}`, {headers: semanticHeaders()});
  if (data.result && !data.parent_job_id) renderSemanticResults(data.result, data.urls || {}, data.status, id);
}
async function recoverPendingSemanticReview() {
  if (!semanticState.requestId) return;
  try {
    const data = await api(`/api/answer-review/request/${encodeURIComponent(semanticState.requestId)}`, {headers: {"X-DQI-Token": state.bootstrap.csrf_token}});
    if (data.review_mode === "single_task") mountTaskReview();
    semanticState.jobId = data.job_id; rememberPending(null, null);
    semanticBusy(true); $("#semantic-progress").hidden = false; $("#semantic-cancel").disabled = data.status !== "running";
    await pollSemanticReview();
  } catch (error) {
    if (error.status === 404 || error.status === 409) {
      const parentId = semanticState.pendingParent;
      if (error.status === 409) rememberPending(null, null);
      semanticBusy(false); $("#semantic-progress").hidden = true;
      if (parentId) {
        try { await loadPrimaryReview(parentId); } catch (restoreError) { semanticMessage(restoreError.message, true); return; }
      } else $("#semantic-setup").open = true;
      if (error.status === 409) { semanticMessage(error.message, true); return; }
      semanticMessage("No accepted review was found. Confirm your key choice and consent to retry with the same request identifier.", true);
    } else {
      semanticBusy(true); $("#semantic-progress").hidden = false; $("#semantic-cancel").disabled = true; $("#semantic-resume-poll").hidden = false;
      semanticMessage("The batch status is still unknown. Use Check review status before starting another billed review.", true);
    }
  }
}
async function pollSemanticReview() {
  if (!semanticState.jobId || semanticState.polling) return;
  semanticState.polling = true;
  const jobId = semanticState.jobId;
  try {
    const data = await api(`/api/answer-review/${encodeURIComponent(jobId)}`, {headers: {"X-DQI-Token": state.bootstrap.csrf_token}});
    if (semanticState.jobId !== jobId) return;
    const task = data.review_mode === "single_task";
    if (task && semanticState.mode !== "single_task") mountTaskReview();
    $("#semantic-progress-bar").max = Math.max(1, Number(data.total)); $("#semantic-progress-bar").value = Number(data.processed);
    $("#semantic-progress-text").textContent = task ? "Evaluating the response against your rubric…" : `${number(data.processed)} of ${number(data.total)} selected records processed. ${data.status === "running" ? "Reviewing answers…" : ""}`;
    $("#semantic-resume-poll").hidden = true;
    if (data.parent_job_id && semanticState.primaryJobId !== data.parent_job_id) await loadPrimaryReview(data.parent_job_id);
    if (semanticState.jobId !== jobId) return;
    if (data.status === "running") semanticState.timer = setTimeout(pollSemanticReview, 1200);
    else {
      semanticBusy(false); $("#semantic-progress").hidden = true;
      const completion = task ? "Task evaluation finished." : "Answer review finished. Review the evidence before changing any original ratings.";
      semanticMessage(data.error || (data.status === "cancelled" ? "Review stopped. Any completed assessments are available below." : completion), data.status === "failed");
      if (task && data.result) restoreSingleTask(data.result);
      if (data.result?.primary_review) renderSecondOpinion(data.result, data.urls || {});
      else if (data.result) renderSemanticResults(data.result, data.urls || {}, data.status, jobId);
      $("#semantic-consent").checked = false; semanticStartEnabled();
    }
  } catch (error) {
    semanticMessage(`${error.message} The batch may still be running. Check its status before starting another review.`, true);
    $("#semantic-progress").hidden = false; $("#semantic-resume-poll").hidden = false;
  } finally { semanticState.polling = false; }
}
async function cancelSemanticReview() {
  if (!semanticState.jobId) return;
  try {
    await api(`/api/answer-review/${encodeURIComponent(semanticState.jobId)}/cancel`, {method: "POST", headers: semanticHeaders(), body: "{}"});
    $("#semantic-cancel").disabled = true; semanticMessage("Stopping after the current answer. Completed results will be retained.");
  } catch (error) { semanticMessage(error.message, true); }
}
function renderSemanticResults(result, urls, status, jobId) {
  semanticState.primaryJobId = jobId;
  const task = result.review_mode === "single_task";
  const criteria = new Map((result.attributes || []).map((item) => [item.field, item]));
  $("#semantic-result-title").textContent = `${task ? "Task evaluation" : "Answer review"} by ${modelName(result.model)}`;
  $("#semantic-results").hidden = false;
  const rows = result.rows || [];
  const recordCounts = result.record_counts || {};
  const counts = result.counts || {};
  if (!result.record_counts || !result.counts) {
    for (const row of rows) {
      if (!result.record_counts) recordCounts[row.status] = (recordCounts[row.status] || 0) + 1;
      if (!result.counts) for (const item of row.assessments || []) counts[item.comparison] = (counts[item.comparison] || 0) + 1;
    }
  }
  const completed = (recordCounts.reviewed || 0) + (recordCounts.insufficient_evidence || 0);
  const selection = result.options?.selection === "all" ? "Whole dataset" : "Selected records";
  $("#semantic-result-note").textContent = task
    ? completed ? "Each criterion below uses the scale in your rubric." : "The evaluation ended before any criterion could be assessed."
    : `${selection} · ${number(completed)} of ${number(result.selected_count ?? rows.length)} records assessed · ${number(recordCounts.skipped || 0)} skipped · ${number(recordCounts.error || 0)} failed · ${number(result.pending_count || 0)} not processed.`;
  const metrics = $("#semantic-result-metrics"); metrics.replaceChildren();
  const metricLabels = task ? [["Criteria scored", "scored"], ["Insufficient evidence", "insufficient_evidence"]] : [["Supported scores", "supported"], ["Close scores", "close"], ["Questionable scores", "questionable"], ["Undecided scores", "insufficient_evidence"]];
  metricLabels.forEach(([label, comparison]) => metrics.append(auditMetric(label, number(counts[comparison] || 0))));
  $("#semantic-result-provenance").textContent = task
    ? `Evaluated ${result.completed_at || ""} · ${modelName(result.model)}. The Inspector checked each quoted excerpt against the text you supplied. ${result.usage_note || ""}`
    : `Source: ${result.source?.filename || "see export"} · ${result.completed_at || ""} · Requested model: ${result.model || result.provenance?.model || $("#semantic-model").value} · Rubric version: ${result.rubric_version || "recorded in export"}. Original ratings were withheld from the reviewer and compared afterward. Quotes are checked against the source text; matching a quote does not prove the explanation is correct. ${result.usage_note || ""}`;
  [["#semantic-download", "zip"], ["#semantic-summary", "html"], ["#semantic-csv", "csv"], ["#semantic-json", "json"]].forEach(([id, key]) => { $(id).hidden = !urls[key]; if (urls[key]) $(id).href = urls[key]; });
  const pager = $("#semantic-result-pages"), parent = $("#semantic-result-rows");
  pager.replaceChildren();
  const perPage = 20;
  pager.hidden = rows.length <= perPage;
  function showPage(start) {
    parent.replaceChildren();
    const end = Math.min(rows.length, start + perPage);
    for (let i = start; i < end; i++) {
      parent.append(semanticResultCard(rows[i], i === start, result, criteria, task, jobId));
    }
  }
  showPage(0);
  if (pager.hidden) return;
  const prev = element("button", "Previous", "button secondary"), next = element("button", "Next", "button secondary");
  prev.type = next.type = "button";
  const range = element("span", "", "small subtle");
  range.setAttribute("role", "status");
  let start = 0;
  function updatePager() {
    prev.disabled = start === 0; next.disabled = start + perPage >= rows.length;
    range.textContent = `Showing ${number(start + 1)}–${number(Math.min(start + perPage, rows.length))} of ${number(rows.length)} results`;
  }
  function move(by) {
    start += by;
    showPage(start);
    updatePager();
  }
  prev.addEventListener("click", () => move(-perPage)); next.addEventListener("click", () => move(perPage));
  pager.append(range, prev, next); updatePager();
}
function semanticResultCard(row, open, result, criteria, task, jobId) {
  const card = element("details", null, "review-record"), heading = element("summary", task ? "Your task" : `Record ${row.record}`);
  heading.append(element("span", row.status.replaceAll("_", " "), "semantic-record-status")); card.append(heading); card.open = open;
  const body = element("div", null, "review-context");
  if (row.assessments?.length && jobId) {
    const button = element("button", "Get a second opinion", "button secondary opinion-trigger");
    button.type = "button"; button.disabled = state.answerReviewRunning;
    button.setAttribute("aria-label", task ? "Get a second opinion for this task" : `Get a second opinion for record ${row.record}`);
    button.addEventListener("click", () => openSecondOpinion(jobId, row, result));
    body.append(button);
  }
  if (row.error || row.reason) body.append(element("p", row.error || row.reason, "subtle"));
  (row.assessments || []).forEach((item) => {
    const section = element("section", null, `semantic-assessment ${item.comparison}`);
    if (task) {
      const rule = criteria.get(item.field);
      const score = item.suggested_score == null ? "Insufficient evidence" : `Score ${scoreText(item.suggested_score)}`;
      section.append(element("h4", item.field), element("p", `${score}${rule ? ` · Scale ${scoreText(rule.min)}–${scoreText(rule.max)}` : ""}`, "semantic-score-line"));
    } else {
      section.append(element("h4", `${item.field} · ${semanticLabels[item.comparison] || item.comparison}`));
      section.append(element("p", `Original rating: ${scoreText(item.original_score)} · ${modelName(result.model)}: ${item.suggested_score == null ? "Undecided" : scoreText(item.suggested_score)}${item.difference == null ? "" : ` · Difference: ${scoreText(item.difference)}`}`, "semantic-score-line"));
    }
    section.append(element("p", item.rationale));
    (item.evidence || []).forEach((evidence) => { section.append(element("p", `Evidence from ${evidence.source}`, "small subtle"), element("blockquote", evidence.quote)); });
    if (item.limitations?.length) section.append(element("p", `What remains uncertain: ${item.limitations.join(" ")}`, "small subtle"));
    body.append(section);
  });
  body.append(element("h4", "Original question"), element("pre", valueText(row.prompt), "source-text"), element("h4", "Original answer"), element("pre", valueText(row.response), "source-text"));
  if (row.reference != null) body.append(element("h4", "Provided reference"), element("pre", valueText(row.reference), "source-text"));
  card.append(body); return card;
}
function renderSecondOpinion(result, urls) {
  const primary = result.primary_review, row = result.rows?.[0];
  const task = result.review_mode === "single_task";
  const panel = $("#opinion-comparison");
  panel.replaceChildren(element("h3", task ? "Second opinion for this task" : `Second opinion for record ${primary.record}`));
  if (!row?.assessments?.length) panel.append(element("p", "The second reviewer returned no scores."));
  for (const first of primary.assessments || []) {
    const second = row?.assessments?.find((item) => item.field === first.field);
    const section = element("section", null, "semantic-assessment");
    section.append(element("h4", first.field));
    if (!task) section.append(element("p", `Original rating: ${scoreText(first.original_score)}`, "semantic-score-line"));
    for (const [model, assessment] of [[primary.model, first], [result.model, second]]) {
      if (!assessment) continue;
      const block = element("div", null, "opinion-model-notes");
      block.append(element("h5", `${modelName(model)}: ${assessment.suggested_score == null ? "Undecided" : scoreText(assessment.suggested_score)}`), element("p", assessment.rationale));
      for (const evidence of assessment.evidence || []) block.append(element("p", `Evidence from ${evidence.source}`, "small subtle"), element("blockquote", evidence.quote));
      if (assessment.limitations?.length) block.append(element("p", assessment.limitations.join(" "), "small subtle"));
      section.append(block);
    }
    panel.append(section);
  }
  const links = element("div", null, "download-links");
  const back = element("a", "View full first review"); back.href = "#semantic-results"; links.append(back);
  for (const [format, label] of [["html", "Open second opinion report"], ["zip", "Download second opinion"]]) {
    if (!urls[format]) continue;
    const link = element("a", label); link.href = urls[format];
    if (format === "html") { link.target = "_blank"; link.rel = "noopener"; }
    links.append(link);
  }
  panel.append(links); panel.hidden = false;
}
function recoverSemanticReview(metadata) {
  try {
    semanticState.requestId = sessionStorage.getItem("dqi-pending-answer-review");
    semanticState.pendingParent = sessionStorage.getItem("dqi-pending-review-parent");
    semanticState.pendingMode = semanticState.requestId && (sessionStorage.getItem("dqi-pending-review-mode") || "dataset");
  } catch { /* Page memory only. */ }
  const jobId = metadata?.active_job_id || metadata?.latest_job_id;
  if (!jobId && !semanticState.requestId) return;
  if ((semanticState.pendingMode || metadata?.restore_review_mode) === "single_task") {
    mountTaskReview();
  } else {
    if (!metadata?.active_job_id && !semanticState.requestId && state.step === "single-task") return;
    if (state.step === "single-task") showStep("upload");
    const panel = $("#semantic-panel"); $("main").insertBefore(panel, $("#upload-panel")); panel.hidden = false;
  }
  $("#semantic-setup").hidden = true; $("#semantic-progress").hidden = false;
  semanticState.jobId = jobId; semanticBusy(Boolean(metadata?.active_job_id || semanticState.requestId || jobId));
  semanticMessage("Restoring the answer review from this local session.");
  if (semanticState.requestId) recoverPendingSemanticReview(); else pollSemanticReview();
}

$("#opinion-form").addEventListener("submit", startSecondOpinion);
$("#opinion-dismiss").addEventListener("click", () => $("#opinion-dialog").close());
$("#opinion-dialog").addEventListener("close", () => {
  clearReviewCredential("opinion"); semanticState.opinion = null;
});
function updateOpinionControls(event) {
  $("#opinion-custom-label").hidden = $("#opinion-model").value !== "custom";
  if (["opinion-model", "opinion-custom-model"].includes(event.target.id)) $("#opinion-consent").checked = false;
  $("#opinion-start").disabled = !opinionReady();
}
$("#opinion-form").addEventListener("input", updateOpinionControls);
$("#opinion-form").addEventListener("change", updateOpinionControls);
function changedReviewModel() {
  $("#semantic-custom-label").hidden = $("#semantic-model").value !== "custom";
  $("#semantic-input-rate").value = ""; $("#semantic-output-rate").value = "";
}
$("#semantic-model").addEventListener("change", changedReviewModel);
$("#semantic-custom-model").addEventListener("input", changedReviewModel);

$("#semantic-settings").addEventListener("input", (event) => {
  if (["semantic-key", "semantic-key-source", "semantic-consent"].includes(event.target.id)) semanticStartEnabled();
  else semanticInvalidate();
});
$("#semantic-settings").addEventListener("change", (event) => {
  if (["semantic-key", "semantic-key-source", "semantic-consent"].includes(event.target.id)) semanticStartEnabled();
  else semanticInvalidate();
});
$("#semantic-preview").addEventListener("click", previewSemanticReview);
$("#semantic-start").addEventListener("click", startSemanticReview);
$("#semantic-cancel").addEventListener("click", cancelSemanticReview);
$("#semantic-resume-poll").addEventListener("click", () => semanticState.requestId ? recoverPendingSemanticReview() : pollSemanticReview());
window.addEventListener("beforeunload", (event) => { if (state.answerReviewRunning) { event.preventDefault(); event.returnValue = ""; } });
