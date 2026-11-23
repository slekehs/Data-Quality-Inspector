// Exercise the real dataset UI with a local quote and a simulated billed start.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const staticDir = process.argv[2];
const fields = new Map();
function node(tag, text = "") {
  return {
    tag, textContent: text, value: "", checked: false, disabled: false, hidden: false,
    children: [], options: [], handlers: {},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    addEventListener(name, handler) { this.handlers[name] = handler; },
    setAttribute(name, value) { this[name] = value; },
  };
}
function $(selector) {
  if (!fields.has(selector)) fields.set(selector, node("div"));
  return fields.get(selector);
}
const state = {bootstrap: {csrf_token: "test", semantic: {model_rates: {sol: {}}}}, answerReviewRunning: false};
const requests = [], starts = [];
let returnQuote;
const ctx = vm.createContext({
  $, state, window: node("window"), number: String, scoreText: String, valueText: String, element: node, decimalLiteral: String,
  reviewCredential: () => ({use_saved_key: true}), clearReviewCredential: () => { $("#semantic-consent").checked = false; },
  auditMetric: (label, count) => node("div", label + count),
  api: async (url, init) => {
    assert.equal(url, "/api/answer-review/preview");
    const payload = JSON.parse(init.body);
    assert.ok(!Object.hasOwn(payload, "use_saved_key") && !Object.hasOwn(payload, "api_key"));
    requests.push(payload);
    if (returnQuote) return returnQuote;
    return makeQuote(payload.model);
  },
});
function makeQuote(model) {
  return {
    quote_id: "test-quote", eligible_count: 99, skipped_count: 1, selected_count: 100, total_records: 100,
    record_numbers: [1, 2], estimate: {model, currency: "USD", estimated_usd: "1.47", output_limit_usd: "8.22",
      rate_source: "Test pricing", estimated_input_tokens: 5000, estimated_output_tokens: 4500, max_output_tokens: 792000,
      input_per_million: "2", output_per_million: "10", assumptions: "Test assumptions."}
  };
}
vm.runInContext(fs.readFileSync(path.join(staticDir, "semantic-ui.js"), "utf8"), ctx);
ctx.submitReview = async payload => starts.push(payload);
vm.runInContext('semanticState.reportId="r"; semanticState.rubrics=[{field:"score", enabled:true, min:"1",max:"5",integer:true,description:"A rating."}]', ctx);
for (const [key, value] of Object.entries({model:"sol", limit:"5", tolerance:"1", prompt:"p", response:"r", selection:"first"})) {
  $("#semantic-" + key).value = value;
}
$("#semantic-model").options = [{value:"sol",textContent:"Sol"}];
function edit(id) {
  $("#semantic-settings").handlers.input({target: {id}});
}
(async () => {
  $("#semantic-scope-all").checked = true; edit("semantic-scope-all");
  assert.equal($("#semantic-limit-label").hidden, true);
  $("#semantic-consent").checked = true;
  await ctx.startSemanticReview();
  assert.equal(starts.length, 0);
  await ctx.previewSemanticReview();
  assert.equal(requests[0].options.selection, "all");
  assert.ok(!Object.hasOwn(requests[0].options, "limit"));
  assert.equal($("#semantic-authorization").hidden, false);
  assert.match($("#semantic-preview-result").children[0].textContent, /\$1\.47/);
  assert.equal($("#semantic-consent").checked, false);
  await ctx.startSemanticReview(); assert.equal(starts.length, 0);
  $("#semantic-consent").checked = true; edit("semantic-consent");
  assert.equal($("#semantic-start").disabled, false);
  $("#semantic-scope-all").checked = false; edit("semantic-scope-selected");
  assert.equal($("#semantic-consent").checked, false);
  assert.equal($("#semantic-authorization").hidden, true);
  assert.equal($("#semantic-limit-label").hidden, false);
  await ctx.startSemanticReview(); assert.equal(starts.length, 0);

  $("#semantic-model").value = "custom"; $("#semantic-custom-model").value = "other-model";
  edit("semantic-custom-model");
  await ctx.previewSemanticReview();
  assert.equal(requests.length, 1);
  assert.equal($("#semantic-custom-rates").hidden, false);
  $("#semantic-input-rate").value = "2.75"; $("#semantic-output-rate").value = "9";
  let finish;
  returnQuote = new Promise(resolve => { finish = resolve; });
  const pending = ctx.previewSemanticReview();
  $("#semantic-output-rate").value = "12"; edit("semantic-output-rate");
  finish(makeQuote("other-model")); await pending;
  assert.equal($("#semantic-authorization").hidden, true, "ignore a delayed quote after an edit");
  returnQuote = null;
  await ctx.previewSemanticReview();
  $("#semantic-consent").checked = true;
  await ctx.startSemanticReview(); await ctx.startSemanticReview();
  assert.equal(starts.length, 1);
  assert.equal(starts[0].quote_id, "test-quote");
  assert.equal(starts[0].model, "other-model"); assert.equal(starts[0].rates.output_per_million, "12");
  assert.equal(starts[0].options.selection, "first"); assert.equal(starts[0].options.limit, 5);
  assert.equal(starts[0].consent, true);

  const rows = Array.from({length: 47}, (_, index) => ({record:index+1, status:"reviewed", prompt:"test", response:"test", assessments:[]}));
  ctx.renderSemanticResults({options:{selection:"all"}, rows, model:"sol"}, {}, "completed", "job");
  const list = $("#semantic-result-rows"), pager = $("#semantic-result-pages");
  assert.equal(list.children.length, 20);
  assert.match($("#semantic-result-note").textContent,/Whole dataset/);
  pager.children[2].handlers.click(); pager.children[2].handlers.click();
  assert.equal(list.children.length, 7);
  assert.equal(list.children[0].children[0].textContent, "Record 41");
  assert.equal(pager.children[2].disabled, true);
})().catch(error => { console.error(error); process.exitCode = 1; });
