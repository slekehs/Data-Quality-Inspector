// Exercise the real form handlers with a local preview and a simulated submission.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function node(tag) {
  return {
    tag, textContent: "", value: "", checked: false, events: {}, children: [], options: [],
    addEventListener(name, callback) { this.events[name] = callback; },
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    setAttribute(name, value) { this[name] = value; },
    removeAttribute(name) { delete this[name]; },
    contains(other) { return this.children.some((child) => child === other || child.contains?.(other)); },
    focus() {}
  };
}

const elements = new Map();
function get(selector) {
  if (!elements.has(selector)) elements.set(selector, node("div"));
  return elements.get(selector);
}

const context = vm.createContext({
  document: {querySelector: get, querySelectorAll: () => [], createElement: node, head: node("head")},
  window: {location: {protocol: "file:"}, addEventListener() {}}
});
for (const filename of ["app.js", "rubric-import.js", "settings.js", "semantic-ui.js", "single-task.js"]) {
  vm.runInContext(fs.readFileSync(path.join(process.argv[2], filename), "utf8"), context, {filename});
}
vm.runInContext('state.bootstrap = {csrf_token: "test"}; state.step = "single-task";', context);

function editRubricInput(label, value, eventName = "input") {
  function find(target) {
    if (target["aria-label"] === label) return target;
    return target.children.map(find).find(Boolean);
  }
  const input = find(get("#single-rubric-criteria"));
  assert.ok(input, label);
  input.value = value;
  const event = {target: input};
  input.events[eventName](event);
  get("#single-task-settings").events[eventName](event);
}

async function test() {
  let submitted, previewBody, pendingPreview;
  context.api = async (url, options) => {
    assert.equal(options.headers["X-DQI-Token"], "test");
    if (url === "/api/rubric-import") {
      return {filename: "tone.txt", entries: [{field: "tone", description: "Score the explanation.", min: null, max: null, integer: null}]};
    }
    assert.equal(url, "/api/single-task/preview");
    previewBody = options.body;
    return pendingPreview || {eligible_count: 1};
  };
  context.submitReview = async (payload, mode) => {
    assert.equal(mode, "single_task");
    submitted = payload;
  };
  get("#single-model").value = "gpt-6.2-sol";
  get("#single-model").options = [{value: "gpt-6.2-sol"}];
  get("#semantic-model").options = [{value: "gpt-6.2-sol", textContent: "Sol"}];
  await context.loadSingleTaskRubric({name: "tone.txt", size: 50});
  assert.equal(get("#single-rubric-criteria").children[0].open, true);
  get("#single-prompt").value = "Explain why.";
  get("#single-response").value = 'One possible "answer".';
  await context.previewSingleTask();
  assert.match(get("#single-feedback").textContent, /score type/);
  assert.equal(previewBody, undefined);
  editRubricInput("tone minimum score", "0.00000000000000000001");
  editRubricInput("tone maximum score", "0.99999999999999999999");
  editRubricInput("tone score type", "decimal", "change");
  await context.previewSingleTask();
  assert.match(previewBody, /"max":0\.99999999999999999999/);
  const preview = JSON.parse(previewBody);
  assert.equal(preview.rubric_filename, "tone.txt");
  assert.equal(preview.reference, null);
  assert.equal(preview.response, get("#single-response").value);
  assert.equal(get("#single-authorize").hidden, false);
  await context.evaluateSingleTask();
  assert.equal(submitted, undefined);
  get("#single-consent").checked = true;
  get("#single-key").value = "synthetic";
  await context.evaluateSingleTask();
  assert.equal(submitted.api_key, "synthetic");
  assert.equal(submitted.rubrics[0].max, "0.99999999999999999999");
  assert.equal(get("#single-key").value, "");
  assert.equal(get("#single-consent").checked, false);

  const sendTask = context.submitReview;
  let stored = "", credentialCalls = 0;
  // Only the fake Keychain transport receives the test secret.
  const previewApi = context.api;
  context.api = async (url, options) => {
    if (url !== "/api/settings/api-key") return previewApi(url, options);
    assert.equal(options.headers["X-DQI-Token"], "test");
    if (options.method === "POST") {
      const body = JSON.parse(options.body);
      stored = body.action === "remove" ? "" : body.api_key;
      ++credentialCalls;
    }
    return {supported: true, saved: Boolean(stored)};
  };
  await context.initializeCredentialSettings();
  assert.equal(get("#settings-remove-key").hidden, true);
  get("#settings-key").value = "synthetic-settings";
  await context.changeSavedKey(false);
  assert.equal(credentialCalls, 1);
  assert.equal(get("#settings-key").value, "");
  assert.equal(get("#single-key-label").hidden, true);
  assert.equal(get("#single-key-source").value, "saved");
  submitted = null;
  await context.evaluateSingleTask();
  assert.equal(submitted, null, "Saving a key must never authorize a review");
  get("#single-consent").checked = true;
  await context.evaluateSingleTask();
  assert.equal(submitted.use_saved_key, true);
  assert.ok(!Object.hasOwn(submitted, "api_key"));
  assert.equal(get("#single-consent").checked, false);
  assert.ok(!JSON.stringify(submitted).includes(stored));

  context.submitReview = async (payload) => { submitted = payload; };
  submitted = null;
  vm.runInContext('semanticState.preview = {eligible_count:1, quote_id:"quoted", estimate:{model:"gpt-6.2-sol"}}; semanticState.options = {}; semanticState.reportId = "example";', context);
  get("#semantic-model").value = "gpt-6.2-sol";
  get("#semantic-consent").checked = true;
  await context.startSemanticReview();
  assert.equal(submitted.use_saved_key, true);
  assert.equal(submitted.quote_id, "quoted");
  assert.ok(!Object.hasOwn(submitted, "api_key"));

  get("#single-key-source").value = "once";
  get("#single-key-source").events.change();
  assert.equal(get("#single-key-label").hidden, false);
  assert.equal(get("#single-consent").checked, false);
  get("#single-key").value = "synthetic-once"; get("#single-consent").checked = true;
  await context.evaluateSingleTask();
  assert.equal(submitted.api_key, "synthetic-once");
  assert.ok(!Object.hasOwn(submitted, "use_saved_key"));
  assert.equal(get("#single-key-source").value, "saved");
  assert.equal(credentialCalls, 1, "One-time overrides must not replace the stored key");

  get("#settings-key").value = "synthetic-replacement";
  await context.changeSavedKey(false);
  assert.equal(stored, "synthetic-replacement");
  get("#single-consent").checked = true;
  await context.changeSavedKey(true);
  assert.equal(stored, "");
  assert.equal(context.reviewCredential("single"), null);
  assert.equal(get("#single-key-label").hidden, false);
  assert.equal(get("#single-consent").checked, false);
  context.applyKeyStatus({supported:false,saved:false});
  assert.equal(get("#settings-key-form").hidden, true);
  assert.equal(get("#semantic-key-label").hidden, false);
  get("#semantic-key").value = "test-unsupported-once";
  assert.equal(context.reviewCredential("semantic").api_key, "test-unsupported-once");
  context.clearReviewCredentials();
  assert.equal(context.reviewCredential("semantic"), null);
  context.submitReview = sendTask;

  get("#single-task-review-host").append(get("#semantic-panel"));
  editRubricInput("tone maximum score", "2");
  assert.equal(get("#semantic-panel").hidden, true);
  assert.equal(get("#single-authorize").hidden, true);
  let finish;
  pendingPreview = new Promise((resolve) => { finish = resolve; });
  const abandoned = context.previewSingleTask();
  context.leaveSingleTask();
  finish({eligible_count: 1});
  await abandoned;
  assert.equal(get("#single-authorize").hidden, true);

  context.resetSingleTask();
  const previousResult = {
    rows: [{prompt: "Earlier prompt", response: "Earlier answer", reference: null}],
    attributes: [{field: "tone", description: "Judge tone.", min: 0, max: 3, integer: true}],
    source: {rubric_filename: "saved.csv"}, model: "custom-model"
  };
  context.restoreSingleTask(previousResult);
  assert.equal(get("#single-prompt").value, "Earlier prompt");
  assert.equal(get("#single-custom-model").value, "custom-model");
  assert.equal(get("#single-key").value, "");
  assert.equal(get("#single-consent").checked, false);
  context.restoreSingleTask({...previousResult, rows: [{prompt: "Overwrite"}]});
  assert.equal(get("#single-prompt").value, "Earlier prompt");
}

test().catch((error) => { console.error(error); process.exitCode = 1; });
