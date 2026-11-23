"""Check highlights derived from the inspected rows and navigation to their evidence."""

from pathlib import Path
import shutil
import subprocess
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "dqi" / "static" / "app.js"
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "Node.js is needed for report view controls")
class LiveReportViewTests(unittest.TestCase):
    def test_highlights_and_queue_shortcut_preserve_the_current_filter(self):
        program = """
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const nodes = new Map();
class Node extends EventTarget {
  children = [];
  get childElementCount() { return this.children.length; }
  replaceChildren() { this.children = []; }
  append(child) { this.children.push(child); }
  focus() { this.focused = true; }
  scrollIntoView() {}
}
function get(selector) {
  if (!nodes.has(selector)) nodes.set(selector, new Node());
  return nodes.get(selector);
}
const document = {querySelector: get, querySelectorAll: () => [], createElement: () => new Node()};
const context = vm.createContext({document, window: {location: {pathname: "/tour-preview"}}});
vm.runInContext(fs.readFileSync(process.argv[1], "utf8"), context);

const empty = {findings: [], skipped_checks: [], finding_count: 0, skipped_check_count: 0};
const low = {kind: "low_rating", field: "score"};
const duplicate = {kind: "duplicate", field: "response"};
context.renderReportHighlights({...empty, audit: {
  options: {review_thresholds: {score: "0.00000001"}}, review_record_count: 2,
  review_queue: [{reasons: [low, duplicate]}, {reasons: [low, duplicate]}],
  duplicate_group_count: 1, duplicate_record_count: 2
}});
assert.deepEqual(get("#report-highlights-list").children.map(item => item.textContent), [
  "2 records have a source rating of 0.00000001 or lower for score.",
  "Exact repeated question–answer pairs occur in 2 records."
]);
assert.equal(get("#browse-review-queue").hidden, false);
get("#audit-kind-filter").value = "low_rating";
get("#browse-review-queue").dispatchEvent(new Event("click"));
assert.equal(get("#report-view-detailed").checked, true);
assert.equal(get("#audit-kind-filter").value, "low_rating");
assert.equal(get("#audit-kind-filter").focused, true);
context.renderReportHighlights(empty);
assert.equal(get("#report-highlights").hidden, true);
assert.equal(get("#browse-review-queue").hidden, true);
"""
        result = subprocess.run([NODE, "-e", program, str(SCRIPT)], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
