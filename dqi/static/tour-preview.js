"use strict";

// This script is served only in the tour's isolated copy of the interface.
(() => {
  if (window.location.pathname !== "/tour-preview") return;
  const pages = ["upload", "checks", "report", "single-task", "settings", "walkthrough"];
  let preparation;

  document.body.inert = true;
  $("#connection").textContent = "Tour example";
  document.querySelectorAll("form, input, textarea").forEach((field) => field.setAttribute("autocomplete", "off"));
  document.querySelectorAll("input[type=password]").forEach((field) => {
    field.value = "";
    field.disabled = true;
  });

  function showTourPage(page) {
    if (!pages.includes(page)) throw new Error("This tour page is unavailable.");
    if (page === "settings" || page === "walkthrough") {
      state.utility = page;
    } else {
      state.utility = null;
      state.step = page;
    }
    renderPage();
  }

  async function loadExample() {
    const reply = await fetch("/tour-example.json", {credentials: "omit"});
    if (!reply.ok) throw new Error("The tour example could not be loaded.");
    const {upload, result} = window.DQIParseJSON(await reply.text());
    const model = $("#semantic-model").value;
    // The example has no session token. Preparing any page uses only its
    // renderers, without running a check, reading Keychain, or starting a job.
    state.bootstrap = {semantic: {default_model: model}};
    state.upload = upload;
    $("#file-name").textContent = upload.filename;
    $("#file-meta").textContent = `${number(upload.record_count)} records · ${number(upload.columns.length)} columns · ${upload.input_format.toUpperCase()}`;
    renderPreview();
    applyPreset("standard");
    state.result = result;
    renderReport();
    $("#report-view-detailed").checked = true;

    resetSingleTask();
    const sample = upload.preview[0].values;
    restoreSingleTask({
      rows: [{prompt: sample.prompt, response: sample.response}],
      attributes: [{
        field: "helpfulness", min: 1, max: 5, integer: true,
        description: "Score 1 for an unrelated answer, 3 for a useful partial answer, and 5 for a complete answer that addresses the request."
      }],
      source: {rubric_filename: "tour-rubric.txt"}, model
    });
    applyKeyStatus({supported: true, saved: false});
    document.querySelectorAll("a[href]").forEach((link) => link.removeAttribute("href"));
    showTourPage("upload");
  }

  window.showTourPage = showTourPage;
  window.prepareTourPreview = () => preparation ??= loadExample();
})();
