"use strict";

const singleTask = {filename: "", entries: [], preview: null, revision: 0, loading: false, touched: false};

function singleTaskFeedback(message, error = false) {
  const node = $("#single-feedback");
  node.hidden = !message;
  node.textContent = message;
  node.className = error ? "message error" : "message";
}

function singleTaskBusy(running) {
  $("#single-task-settings").disabled = Boolean(running || singleTask.loading);
  $("#single-task-back").disabled = Boolean(running);
  $("#single-evaluate").disabled = running || singleTask.loading || !singleTask.preview ||
    !$("#single-consent").checked || !reviewCredential("single");
}

function invalidateSingleTask() {
  singleTask.revision++;
  singleTask.preview = null;
  $("#single-preview-result").hidden = true;
  $("#single-authorize").hidden = true;
  clearReviewCredential("single");
  singleTaskFeedback("");
  singleTaskBusy(state.answerReviewRunning);
}

function leaveSingleTask() {
  if (singleTask.loading) showSingleRubricStatus();
  singleTask.loading = false;
  invalidateSingleTask();
}

function resetSingleTask() {
  leaveSingleTask();
  singleTask.entries = [];
  singleTask.filename = "";
  singleTask.touched = false;
  for (const id of ["single-prompt", "single-response", "single-reference", "single-custom-model", "single-rubric-file"]) {
    $(`#${id}`).value = "";
  }
  $("#single-model").selectedIndex = 0;
  $("#single-custom-label").hidden = true;
  $("#single-rubric-action").textContent = "Upload rubric";
  $("#single-rubric-status").textContent = "";
  $("#single-rubric-criteria").replaceChildren();
}

function hidePreviousSingleTaskResults() {
  if (state.answerReviewRunning || !$("#single-task-review-host").contains($("#semantic-panel"))) return;
  $("#semantic-results").hidden = true;
  $("#opinion-comparison").hidden = true;
  $("#semantic-message").hidden = true;
  $("#semantic-panel").hidden = true;
}

function showSingleRubricStatus() {
  const count = singleTask.entries.length;
  $("#single-rubric-status").textContent = count
    ? `${singleTask.filename || "Saved rubric"} · ${count} ${count === 1 ? "criterion" : "criteria"}. Expand a criterion to review its wording and scale.`
    : "";
}

function restoreSingleTask(result) {
  const row = result?.rows?.[0];
  if (singleTask.touched || singleTask.loading || singleTask.entries.length || !row || !Array.isArray(result.attributes)) return;
  if (["single-prompt", "single-response", "single-reference", "single-key"].some((id) => $(`#${id}`).value)) return;
  $("#single-prompt").value = row.prompt ?? "";
  $("#single-response").value = row.response ?? "";
  $("#single-reference").value = row.reference ?? "";
  singleTask.entries = result.attributes.map((entry) => ({...entry, min: String(entry.min), max: String(entry.max)}));
  singleTask.filename = result.source?.rubric_filename || "";
  if (result.model) {
    const menu = $("#single-model");
    const listed = Array.from(menu.options).some((option) => option.value !== "custom" && option.value === result.model);
    menu.value = listed ? result.model : "custom";
    $("#single-custom-label").hidden = listed;
    $("#single-custom-model").value = listed ? "" : result.model;
  }
  $("#single-rubric-action").textContent = "Replace rubric";
  renderSingleTaskCriteria();
  showSingleRubricStatus();
}

function singleScaleCaption(entry) {
  if (entry.min === "" || entry.max === "" || entry.integer === null) return "Set the scoring scale";
  return `${entry.min} to ${entry.max} · ${entry.integer ? "Whole numbers" : "Decimals allowed"}`;
}

function renderSingleTaskCriteria() {
  const container = $("#single-rubric-criteria");
  container.replaceChildren();
  for (const entry of singleTask.entries) {
    const card = element("details", null, "single-criterion");
    card.open = entry.min === "";
    const heading = element("summary", `${entry.field} · `);
    const caption = element("span", singleScaleCaption(entry), "small");
    heading.append(caption);
    const label = element("label", "Criteria and score meanings");
    const description = element("textarea");
    description.rows = 3;
    description.setAttribute("aria-label", `${entry.field} criteria and score meanings`);
    description.value = entry.description;
    description.addEventListener("input", () => { entry.description = description.value; });
    label.append(description);
    const scale = element("div", null, "single-scale");
    for (const [key, name] of [["min", "Minimum score"], ["max", "Maximum score"]]) {
      const boundLabel = element("label", name);
      const input = element("input");
      input.type = "text";
      input.inputMode = "decimal";
      input.value = entry[key];
      input.setAttribute("aria-label", `${entry.field} ${name.toLowerCase()}`);
      input.addEventListener("input", () => {
        entry[key] = input.value;
        caption.textContent = singleScaleCaption(entry);
      });
      boundLabel.append(input);
      scale.append(boundLabel);
    }
    const typeLabel = element("label", "Score type");
    const scoreType = element("select");
    scoreType.setAttribute("aria-label", `${entry.field} score type`);
    for (const [value, text] of [["", "Choose a score type"], ["whole", "Whole numbers"], ["decimal", "Decimals allowed"]]) {
      const option = element("option", text);
      option.value = value;
      scoreType.append(option);
    }
    scoreType.value = entry.integer === null ? "" : entry.integer ? "whole" : "decimal";
    scoreType.addEventListener("change", () => {
      entry.integer = scoreType.value === "" ? null : scoreType.value === "whole";
      caption.textContent = singleScaleCaption(entry);
    });
    typeLabel.append(scoreType);
    scale.append(typeLabel);
    card.append(heading, label, scale);
    container.append(card);
  }
}

async function loadSingleTaskRubric(file) {
  if (!file || singleTask.loading || state.answerReviewRunning) return;
  invalidateSingleTask();
  const revision = singleTask.revision;
  const status = $("#single-rubric-status");
  try {
    if (!state.bootstrap) throw new Error("The inspector is still connecting. Try again in a moment.");
    if (file.size > 256 * 1024) throw new Error("Choose a rubric file up to 256 KiB.");
    singleTask.loading = true;
    singleTaskBusy(false);
    status.textContent = `Reading ${file.name}…`;
    const parsed = await api("/api/rubric-import", {
      method: "POST", body: file,
      headers: {"Content-Type": "application/octet-stream", "X-Filename": encodeURIComponent(file.name), "X-DQI-Token": state.bootstrap.csrf_token}
    });
    if (revision !== singleTask.revision) return;
    singleTask.entries = parsed.entries.map((entry) => ({...entry, min: entry.min ?? "", max: entry.max ?? ""}));
    singleTask.filename = parsed.filename;
    singleTask.touched = true;
    hidePreviousSingleTaskResults();
    renderSingleTaskCriteria();
    $("#single-rubric-action").textContent = "Replace rubric";
    showSingleRubricStatus();
  } catch (error) {
    if (revision === singleTask.revision) status.textContent = `${error.message}${singleTask.filename ? " Current rubric: " + singleTask.filename + "." : ""}`;
  } finally {
    if (revision === singleTask.revision) { singleTask.loading = false; singleTaskBusy(state.answerReviewRunning); }
  }
}

function singleTaskPayload() {
  const prompt = $("#single-prompt").value, response = $("#single-response").value;
  if (!prompt.trim()) throw new Error("Paste the prompt you gave the AI.");
  if (!response.trim()) throw new Error("Paste the AI response you want to evaluate.");
  if (!singleTask.entries.length) throw new Error("Upload a scoring rubric for this task.");
  const rubrics = singleTask.entries.map((entry) => {
    if (!entry.description.trim()) throw new Error(`Enter the scoring criteria for “${entry.field}”.`);
    if (!entry.min.trim() || !entry.max.trim() || entry.integer === null) throw new Error(`Choose the minimum, maximum, and score type for “${entry.field}”.`);
    const scaleError = rubricScaleError(entry);
    if (scaleError) throw new Error(`Check the scale for “${entry.field}”. ${scaleError}`);
    return {field: entry.field, description: entry.description, min: decimalLiteral(entry.min), max: decimalLiteral(entry.max), integer: entry.integer};
  });
  const reference = $("#single-reference").value;
  return {prompt, response, reference: reference.trim() ? reference : null, rubrics, ...(singleTask.filename ? {rubric_filename: singleTask.filename} : {})};
}

function singleTaskRequestBody(payload) {
  const properties = Object.entries(payload).map(([key, value]) => {
    const literal = key === "rubrics" ? `[${serializeReviewRubrics(value)}]` : JSON.stringify(value);
    return `${JSON.stringify(key)}:${literal}`;
  });
  return `{${properties.join(",")}}`;
}

async function previewSingleTask() {
  if (singleTask.loading || state.answerReviewRunning) return;
  invalidateSingleTask();
  const revision = singleTask.revision;
  try {
    const task = singleTaskPayload();
    const model = chosenModel("single");
    if (!validModel(model)) throw new Error("Choose a review model or enter a valid OpenAI model ID.");
    if (!state.bootstrap) throw new Error("The inspector is still connecting. Try again in a moment.");
    singleTask.loading = true;
    singleTaskBusy(false);
    singleTaskFeedback("Preparing your local preview…");
    const preview = await api("/api/single-task/preview", {method: "POST", headers: semanticHeaders(), body: singleTaskRequestBody(task)});
    if (revision !== singleTask.revision) return;
    if (Number(preview.eligible_count) !== 1) throw new Error("The reviewer could not accept this task. Check the prompt, response, and reference length.");
    singleTask.preview = {...task, model};
    const box = $("#single-preview-result");
    box.replaceChildren(element("strong", "Ready to evaluate"));
    const count = task.rubrics.length;
    box.append(element("p", `${modelName(model)} will review this response against ${count} ${count === 1 ? "criterion" : "criteria"} in one API request.`));
    box.hidden = false;
    $("#single-authorize").hidden = false;
    singleTaskFeedback("");
  } catch (error) {
    if (revision === singleTask.revision) singleTaskFeedback(error.message, true);
  } finally {
    if (revision === singleTask.revision) { singleTask.loading = false; singleTaskBusy(state.answerReviewRunning); }
  }
}

async function evaluateSingleTask() {
  if (singleTask.loading || state.answerReviewRunning || !singleTask.preview) return;
  const credential = reviewCredential("single");
  if (!credential || !$("#single-consent").checked) return;
  const payload = {...singleTask.preview, ...credential, consent: true};
  clearReviewCredential("single");
  singleTaskBusy(false);
  await submitReview(payload, "single_task");
}

$("#open-single-task").addEventListener("click", () => {
  if (state.busy || state.answerReviewRunning) return;
  showStep("single-task");
  $("#single-task-title").focus();
});
$("#single-task-back").addEventListener("click", () => {
  if (state.answerReviewRunning) return;
  showStep("upload");
  $("#open-single-task").focus();
});
$("#single-rubric-file").addEventListener("change", (event) => {
  const file = event.target.files[0];
  event.target.value = "";
  loadSingleTaskRubric(file);
});
$("#single-model").addEventListener("change", () => { $("#single-custom-label").hidden = $("#single-model").value !== "custom"; });
for (const eventName of ["input", "change"]) {
  $("#single-task-settings").addEventListener(eventName, (event) => {
    if (event.target.id === "single-rubric-file") return;
    if (["single-key", "single-key-source", "single-consent"].includes(event.target.id)) singleTaskBusy(state.answerReviewRunning);
    else {
      singleTask.touched = true;
      hidePreviousSingleTaskResults();
      invalidateSingleTask();
    }
  });
}
$("#single-preview").addEventListener("click", previewSingleTask);
$("#single-evaluate").addEventListener("click", evaluateSingleTask);
