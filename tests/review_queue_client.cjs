const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const staticDir = process.argv[2];
const read = (name) => fs.readFileSync(path.join(staticDir, name), "utf8");

class Element extends EventTarget {
  constructor(tag, text = "") {
    super();
    this.tag = tag;
    this.textContent = text;
    this.children = [];
    this.value = "";
    this.hidden = false;
  }
  replaceChildren() { this.children = []; this.value = ""; }
  append(...nodes) { this.children.push(...nodes); }
  removeAttribute(name) { delete this[name]; }
  focus() {}
  scrollIntoView() {}
}

const controls = new Map(Array.from(read("index.html").matchAll(/<(\w+)[^>]*?\bid="([^"]+)"/g),
  ([, tag, id]) => ["#" + id, new Element(tag)]));
function $(selector) {
  if (selector === "#audit-queue summary") return $("#audit-queue").children[0]?.children[0];
  const control = controls.get(selector);
  assert.ok(control, selector);
  return control;
}
const state = {bootstrap: {filtered_review_csv: true}};
const location = new URL("http://localhost:8765/");
const context = vm.createContext({$, state, window: {location}, URL, number: String,
  valueText: String, element: (tag, text) => new Element(tag, text)});
vm.runInContext(read("audit-ui.js"), context);

const field = "Clarté & ton";
const queue = Array.from({length: 105}, (_, index) => ({
  record: index + 1,
  reasons: index < 101 ? [{kind: "low_rating", field}] :
    [{kind: "data_error", field}, {kind: "low_rating", field: "fluency"}],
}));
function inspect(id, records) {
  const audit = {review_queue: records, review_record_count: records.length, options: {rule_origin: "custom"}};
  const urls = {review: "/api/reports/" + id + "/review-queue.csv"};
  state.result = {report: {audit}, urls};
  context.renderAuditReport(audit, urls);
}
function page(first, last, range) {
  const cards = $("#audit-queue").children;
  assert.equal(cards.length, last - first + 1);
  cards.forEach((card, index) => assert.ok(card.children[0].textContent.startsWith(`Record ${first + index} ·`)));
  assert.equal($("#audit-queue-count").textContent, range);
}
const next = $("#audit-queue-next"), previous = $("#audit-queue-previous");
const pager = $("#audit-queue-pager"), download = $("#download-filtered-review");
const click = (control) => control.dispatchEvent(new Event("click"));
function filter(id, value) {
  const select = $(id);
  select.value = value;
  select.dispatchEvent(new Event("change"));
}

inspect("first", queue);
page(1, 50, "Showing 1–50 of 105 records");
assert.ok(!pager.hidden && previous.disabled && !next.disabled && download.hidden);
click(next);
page(51, 100, "Showing 51–100 of 105 records");
click(next);
page(101, 105, "Showing 101–105 of 105 records");
assert.ok(!previous.disabled && next.disabled);
click(previous);
page(51, 100, "Showing 51–100 of 105 records");

filter("#audit-kind-filter", "low_rating");
page(1, 50, "Showing 1–50 of 105 matching records");
click(next);
filter("#audit-field-filter", field);
page(1, 50, "Showing 1–50 of 101 matching records");
assert.equal(download.hidden, false);
const url = new URL(download.href);
assert.equal(url.pathname, state.result.urls.review);
assert.deepEqual(Object.fromEntries(url.searchParams), {reason: "low_rating", attribute: field});
click(next);
click(next);
page(101, 101, "Showing 101 of 101 matching records");
assert.equal(download.href, url.href);
assert.equal($("#download-review").href, state.result.urls.review);

filter("#audit-kind-filter", "data_error");
page(102, 105, "Showing 1–4 of 4 matching records");
assert.ok(pager.hidden && !download.hidden);
filter("#audit-field-filter", "fluency");
assert.equal($("#audit-queue-count").textContent, "0 matching records");
assert.ok(pager.hidden && download.hidden);
assert.match($("#audit-queue").children[0].textContent, /No records match/);

filter("#audit-field-filter", field);
filter("#audit-kind-filter", "low_rating");
click(next);
inspect("second", queue.slice(0, 55));
page(1, 50, "Showing 1–50 of 55 records");
assert.ok(previous.disabled && download.hidden);
assert.equal($("#download-review").href, "/api/reports/second/review-queue.csv");

delete state.bootstrap.filtered_review_csv;
filter("#audit-kind-filter", "low_rating");
assert.ok(download.hidden && !download.href && !$("#audit-download-upgrade").hidden);
assert.equal($("#download-review").href, "/api/reports/second/review-queue.csv");
