"""Exercise the browser mapping and application flow without contacting an AI provider."""
from pathlib import Path
import shutil
import subprocess
import unittest


STATIC = Path(__file__).resolve().parents[1] / "dqi" / "static"
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is needed for rubric import browser tests")
class RubricImportClientTests(unittest.TestCase):
    def test_companion_file_lifecycle_and_exact_scales(self):
        script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const elements = new Map();
function element(tag, text, css) {
  return {
    tag, textContent: text || "", children: [], events: {}, classList: {add() {}},
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; },
    setAttribute() {}, addEventListener(name, fn) { this.events[name] = fn; },
    focus() {}
  };
}
function $(id) {
  if (!elements.has(id)) elements.set(id, element("div"));
  return elements.get(id);
}
const state = {bootstrap: {csrf_token: "test"}, upload: null, shared: {}, rules: [], busy: false};
function clearError() { state.error = ""; }
function showError(error) { state.error = error.message; }
function edited() { state.result = null; }
function renderRules() {}
function setBusy(value) { state.busy = value; }
let reply, apiCalls = 0;
async function api(path, options) {
  assert.equal(path, "/api/rubric-import");
  assert.equal(options.headers["X-DQI-Token"], "test");
  assert.equal(options.headers["X-Filename"], encodeURIComponent(options.body.name));
  apiCalls++;
  if (reply instanceof Error) throw reply;
  return await reply;
}
const source = fs.readFileSync(process.argv[1], "utf8");
for (const [start, end] of [["makeRule", "applyPreset"], ["decimalLiteral", "inspectionBody"]]) {
  const i = source.indexOf("function " + start + "(");
  const j = source.indexOf("\nfunction " + end + "(", i);
  assert.ok(i >= 0 && j > i);
  vm.runInThisContext(source.slice(i, j));
}
vm.runInThisContext(fs.readFileSync(process.argv[2], "utf8"));
function chooseTarget(rowIndex, value) {
  function selectWithin(node) {
    if (node.tag === "select") return node;
    for (const child of node.children || []) {
      const found = selectWithin(child);
      if (found) return found;
    }
  }
  const select = selectWithin($("#rubric-mappings").children[rowIndex]);
  select.value = value;
  select.events.change();
}
function entry(field, min = null, max = null, integer = null) {
  return {field, description: "<em>Criteria</em>\nKeep both lines.", min, max, integer};
}
async function pick(entries, {next = false, name = "my rúbric.json"} = {}) {
  reply = {filename: name, entries};
  await readRubricFile({name, size: 20}, next);
}
async function main() {
  await pick([entry("HelpFul_Ness"), entry("a b"), entry("Spelling")], {next: true});
  assert.ok(nextRubricFile);
  assert.match($("#upload-rubric-status").textContent, /ready/);
  state.upload = {columns: ["response", "original prompt", "Helpfulness", "a-b", "a_b", "__proto__"]};
  state.shared = {response: {field: "response", required: true}};
  state.audit = {enabled: true, prompt: "original prompt"};
  const existing = {...makeRule("Helpfulness", true), numeric: false, required: false};
  state.rules = [existing, makeRule("unspecified")];
  rubricDatasetChanged(true);
  assert.equal(nextRubricFile, null);
  assert.match(validateRubricImportForInspection(), /Apply/);
  assert.equal($("#apply-rubric").disabled, true);
  let rows = planRubricMappings(currentRubricDraft);
  assert.equal(rows[0].field, "Helpfulness");
  assert.match(rows[1].note, /Several/);
  assert.match(rows[2].note, /No matching/);
  assert.equal(rubricMatch("a-b", state.upload.columns).target, "a-b");
  const unrevised = state.rules;
  currentRubricDraft.choices[1] = "Helpfulness";
  currentRubricDraft.choices[2] = "response";
  rows = planRubricMappings(currentRubricDraft);
  assert.match(rows[0].error, /Another rubric/);
  assert.match(rows[1].error, /Another rubric/);
  assert.match(rows[2].error, /in use/);
  currentRubricDraft.choices[2] = "original prompt";
  assert.match(planRubricMappings(currentRubricDraft)[2].error, /in use/);
  applyRubricDraft();
  assert.equal(state.rules, unrevised);
  chooseTarget(1, String(state.upload.columns.indexOf("__proto__")));
  chooseTarget(2, "skip");
  assert.equal($("#apply-rubric").disabled, false);
  $("#apply-rubric").events.click();
  assert.equal(validateRubricImportForInspection(), "");
  assert.equal(state.rules[0].integer, true);
  assert.equal(state.rules[0].min, "0");
  assert.equal(state.rules[0].required, false);
  assert.equal(state.rules[0].numeric, false);
  assert.equal(state.rules[0].importedRubric.sourceField, "HelpFul_Ness");
  assert.equal(state.rules[0].importedRubric.description, "<em>Criteria</em>\nKeep both lines.");
  assert.equal(state.rules[1].field, "unspecified");
  assert.equal(state.rules[2].field, "__proto__");
  assert.equal(state.rules[2].importedRubric.min, "1");
  assert.equal(Object.prototype.importedRubric, undefined);
  await pick([entry("Helpfulness", "0.1000000000000000000000001", "0.1000000000000000000000002", false)]);
  assert.match($("#rubric-mapping-status").textContent, /replaces/);
  applyRubricDraft();
  assert.equal(state.rules.length, 3);
  assert.equal(state.rules[0].integer, false);
  assert.equal(state.rules[0].importedRubric.max, "0.1000000000000000000000002");
  assert.equal(state.rules[2].importedRubric, undefined);
  let prior = state.rules[0].importedRubric;
  reply = new Error("Invalid file");
  await readRubricFile({name: "bad.csv", size: 20}, false);
  assert.equal(state.error, "Invalid file");
  assert.equal(state.rules[0].importedRubric, prior);
  const beforeLimit = apiCalls;
  await readRubricFile({name: "too-big.json", size: 262145}, false);
  assert.equal(apiCalls, beforeLimit);
  assert.match(state.error, /256/);
  $("#reopen-rubric").events.click();
  assert.ok(currentRubricDraft);
  $("#discard-rubric").events.click();
  assert.equal(validateRubricImportForInspection(), "");
  assert.equal(state.rules[0].importedRubric, prior);
  state.rules = [];
  refreshRubricImport();
  assert.match($("#applied-rubric-status").textContent, /no criteria/);
  $("#reopen-rubric").events.click();
  applyRubricDraft();
  assert.equal(state.rules.length, 1);
  assert.equal(state.rules[0].min, prior.min);
  await pick([entry("New field")], {next: true, name: "next.md"});
  state.upload = {columns: ["New field"]};
  state.rules = [];
  rubricDatasetChanged(false);
  assert.equal(lastAppliedRubric, null);
  assert.equal(currentRubricDraft.filename, "next.md");
  assert.equal($("#apply-rubric").disabled, false);
  rubricDatasetChanged(false);
  assert.equal(currentRubricDraft, null);
  let finish;
  reply = new Promise((resolve) => { finish = resolve; });
  const pending = readRubricFile({name: "slow.json", size: 1}, false);
  resetRubricImport();
  finish({filename: "slow.json", entries: [entry("New field")]});
  await pending;
  assert.equal(currentRubricDraft, null);
  assert.equal($("#rubric-draft").hidden, true);
  assert.equal(rubricScaleError({min: "1e-1000", max: "2e-1000", integer: false}), "");
  assert.match(rubricScaleError({min: "1", max: "1.0", integer: false}), /below/);
  assert.match(rubricScaleError({min: "1", max: "2.1", integer: true}), /whole-number/);
}
main().catch((error) => { console.error(error); process.exitCode = 1; });
"""
        result = subprocess.run([NODE, "-e", script, str(STATIC / "app.js"),
                                 str(STATIC / "rubric-import.js")], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
