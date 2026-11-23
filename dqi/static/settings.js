"use strict";

// Only availability metadata lives on the page. Keychain returns no secret to the browser.
const keySettings = {supported: null, saved: false, writing: false, read: null};
const reviewKeyPrefixes = ["semantic", "single", "opinion"];

function reviewCredential(prefix) {
  if (keySettings.saved && $(`#${prefix}-key-source`).value === "saved") return {use_saved_key: true};
  const key = $(`#${prefix}-key`).value.trim();
  return key ? {api_key: key} : null;
}

function showReviewKeyChoice(prefix) {
  const saved = keySettings.saved;
  $(`#${prefix}-key-source-label`).hidden = !saved;
  $(`#${prefix}-key-label`).hidden = saved && $(`#${prefix}-key-source`).value === "saved";
  $(`#${prefix}-key-help`).hidden = saved || keySettings.supported === false;
}

function clearReviewCredential(prefix) {
  $(`#${prefix}-key`).value = "";
  $(`#${prefix}-consent`).checked = false;
  $(`#${prefix}-key-source`).value = keySettings.saved ? "saved" : "once";
  showReviewKeyChoice(prefix);
}

function updateReviewKeyButtons() {
  semanticStartEnabled();
  singleTaskBusy(state.answerReviewRunning);
  $("#opinion-start").disabled = !opinionReady();
}

function clearReviewCredentials() {
  reviewKeyPrefixes.forEach(clearReviewCredential);
  updateReviewKeyButtons();
}

function settingsFeedback(text, error = false) {
  const node = $("#settings-feedback");
  node.textContent = text;
  node.hidden = !text;
  node.className = error ? "message error" : "message success";
}

function updateKeySettingsButtons() {
  $("#settings-save-key").disabled = keySettings.writing || !keySettings.supported || !$("#settings-key").value.trim();
  $("#settings-remove-key").disabled = keySettings.writing;
}

function applyKeyStatus(status, resetChoices = false) {
  const saved = status.supported === true && status.saved === true;
  const changed = keySettings.saved !== saved;
  keySettings.supported = status.supported;
  keySettings.saved = saved;
  $("#settings-key-form").hidden = !keySettings.supported;
  $("#settings-key-status").textContent = keySettings.supported
    ? (saved ? "A key is saved in Keychain." : "No key saved yet.")
    : (keySettings.supported === false ? "Keychain storage is unavailable here. Enter a one-time key when you run an AI review." : "Couldn’t check Keychain. Open Settings again to retry.");
  $("#settings-key-label").textContent = saved ? "Replacement API key" : "OpenAI API key";
  $("#settings-save-key").textContent = saved ? "Replace saved key" : "Save key";
  $("#settings-remove-key").hidden = !saved;
  updateKeySettingsButtons();
  for (const prefix of reviewKeyPrefixes) {
    if (resetChoices) clearReviewCredential(prefix);
    else {
      if (changed) {
        $(`#${prefix}-key-source`).value = saved && !$(`#${prefix}-key`).value ? "saved" : "once";
        $(`#${prefix}-consent`).checked = false;
      }
      showReviewKeyChoice(prefix);
    }
  }
  updateReviewKeyButtons();
}

function refreshKeyStatus() {
  if (!state.bootstrap || keySettings.writing) return;
  if (keySettings.read) return keySettings.read;
  keySettings.read = api("/api/settings/api-key", {headers: {"X-DQI-Token": state.bootstrap.csrf_token}})
    .then((status) => { if (!keySettings.writing) applyKeyStatus(status); })
    .catch((error) => {
      if (keySettings.writing) return;
      applyKeyStatus({supported: null, saved: false});
      if (state.utility === "settings") settingsFeedback(error.message, true);
    })
    .finally(() => { keySettings.read = null; });
  return keySettings.read;
}

function initializeCredentialSettings() { return refreshKeyStatus(); }
function enterCredentialSettings() {
  settingsFeedback("");
  return refreshKeyStatus();
}
function leaveCredentialSettings() {
  $("#settings-key").value = "";
  updateKeySettingsButtons();
}

async function changeSavedKey(remove) {
  if (keySettings.writing || state.busy || !state.bootstrap || !keySettings.supported) return;
  const value = $("#settings-key").value.trim();
  if ((remove && !keySettings.saved) || (!remove && !value)) return;
  keySettings.writing = true;
  $("#settings-key").value = "";
  updateKeySettingsButtons(); settingsFeedback("");
  setBusy(true, remove ? "Removing the saved key…" : "Saving your key to Keychain…");
  try {
    // A status read started on entry must finish before applying a newer write result.
    if (keySettings.read) await keySettings.read;
    const status = await api("/api/settings/api-key", {
      method: "POST",
      headers: {"Content-Type": "application/json", "X-DQI-Token": state.bootstrap.csrf_token},
      body: JSON.stringify(remove ? {action: "remove"} : {api_key: value})
    });
    applyKeyStatus(status, true);
    settingsFeedback(remove ? "Removed the saved key from this Mac." : "Saved. Future AI reviews can use this key.");
  } catch (error) { settingsFeedback(error.message, true); }
  finally { keySettings.writing = false; updateKeySettingsButtons(); setBusy(false); }
}

for (const prefix of reviewKeyPrefixes) {
  $(`#${prefix}-key-source`).addEventListener("change", () => {
    $(`#${prefix}-key`).value = "";
    $(`#${prefix}-consent`).checked = false;
    showReviewKeyChoice(prefix);
    updateReviewKeyButtons();
  });
}
$("#settings-key").addEventListener("input", updateKeySettingsButtons);
$("#settings-key-form").addEventListener("submit", (event) => { event.preventDefault(); changeSavedKey(false); });
$("#settings-remove-key").addEventListener("click", () => changeSavedKey(true));
