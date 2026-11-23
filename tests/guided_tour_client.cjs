// Exercise the actual controller with a separate preview-document fixture.
// The live document has a draft and an approved local preview before the tour.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const staticDir = process.argv[2];
const read = (name) => fs.readFileSync(path.join(staticDir, name), "utf8");
const live = new Map(), frames = [], shown = [], errors = [];
const draft = {prompt: "Unsent task instructions", response: "Private draft answer"};
const preview = {ready: true, rubrics: [{field: "accuracy"}]};
const state = {step: "single-task", utility: "walkthrough", busy: false, answerReviewRunning: false, bootstrap: {}};
let activeOverlay, scroll = [24, 310], selectedPage;

class Element extends EventTarget {
  constructor(id) {
    super();
    this.id = id;
    this.style = {};
    this.isConnected = true;
    this.classList = { add() {}, remove() {} };
  }
  setAttribute(name, value) { this[name] = value; }
  focus() { document.activeElement = this; }
  remove() { this.isConnected = false; }
  append(...nodes) { nodes.forEach((node) => { node.isConnected = true; }); }
  getClientRects() { return [{}]; }
}
function get(selector) {
  if (!live.has(selector)) live.set(selector, new Element(selector.slice(1)));
  return live.get(selector);
}
const document = new EventTarget();
Object.assign(document, {
  querySelector: get,
  getElementById: (id) => get("#" + id),
  body: new Element(), documentElement: new Element(),
  createElement(tag) {
    const node = new Element();
    if (tag === "iframe") {
      node.contentDocument = {querySelector(selector) { return {fixture: true, selector, page: selectedPage}; }};
      node.contentWindow = {
        prepareTourPreview() { return new Promise(resolve => { node.finishPreparing = resolve; }); },
        showTourPage(page) { selectedPage = page; }
      };
      frames.push(node);
    }
    return node;
  }
});
const window = new EventTarget();
Object.assign(window, {
  location: {pathname: "/", hash: "#walkthrough", protocol: "http:"},
  scrollTo(x, y) { scroll = typeof x === "object" ? [x.left || 0, x.top || 0] : [x,y]; }
});
Object.defineProperties(window, {
  scrollX: {get: () => scroll[0]}, scrollY: {get: () => scroll[1]}
});
const context = vm.createContext({
  window, document, state, $: get,
  showError(error) { errors.push(error.message); },
  fetch() { throw Error("The parent tour controller must not send a request"); },
  setTimeout, clearTimeout,
  createTourOverlay(actions) {
    activeOverlay = {actions, closed: false,
      show(step) { shown.push(step); },
      close() { this.closed = true; }
    };
    return activeOverlay;
  },
});
vm.runInContext(read("tour-stops.js"), context);
vm.runInContext(read("guided-tour.js"), context);
const stops = vm.runInContext("TOUR_STOPS", context);
const html = read("index.html");
for (const id of ["start-guided-tour", "start-home-tour"]) assert.ok(html.includes('id="' + id + '"'));
assert.deepEqual(new Set(stops.map(stop => stop.page)), new Set(["upload","checks","report","single-task","settings","walkthrough"]));

function assertStop(index) {
  const actual = shown.at(-1);
  assert.equal(actual.index, index);
  assert.equal(actual.total, stops.length);
  assert.equal(actual.title, stops[index].title);
  assert.equal(actual.target.fixture, true, "Only the isolated tour document may be highlighted");
  assert.equal(actual.target.page, stops[index].page);
  assert.equal(actual.target.selector, stops[index].target);
}

function start(trigger) {
  trigger.focus();
  trigger.dispatchEvent(new Event("click"));
  const frame = frames.at(-1);
  assert.ok(frame && frame.isConnected);
  assert.equal(frame.src, "/tour-preview");
  frame.dispatchEvent(new Event("load"));
  return frame;
}
function returned(frame, trigger, expectedPage) {
  assert.ok(!frame.isConnected && activeOverlay.closed);
  assert.equal(document.activeElement, trigger);
  assert.deepEqual(scroll, [24,310]);
  assert.deepEqual([state.step, state.utility], expectedPage);
  assert.equal(get("#single-prompt").value, draft.prompt);
  assert.equal(get("#single-response").value, draft.response);
  assert.equal(get("#single-preview-result").hidden, false);
  assert.equal(context.singleTask.preview, preview);
}
context.singleTask = {preview};
get("#single-prompt").value = draft.prompt;
get("#single-response").value = draft.response;
get("#single-preview-result").hidden = false;

async function test() {
  let trigger = get("#start-guided-tour");
  const abandoned = start(trigger);
  activeOverlay.actions.onNext();
  assert.ok(!shown.some(step => step.target?.fixture));
  activeOverlay.actions.onSkip(); returned(abandoned, trigger, ["single-task", "walkthrough"]);
  let frame = start(trigger);
  abandoned.finishPreparing();
  await new Promise(setImmediate);
  assert.ok(!shown.some(step => step.target?.fixture), "An old load cannot take over a new tour");
  frame.finishPreparing();
  await new Promise(setImmediate);
  assertStop(0);
  activeOverlay.actions.onNext(); assertStop(1);
  activeOverlay.actions.onBack(); assertStop(0);
  window.scrollTo(0, 0);
  activeOverlay.actions.onSkip(); returned(frame, trigger, ["single-task", "walkthrough"]);

  state.step = "upload"; state.utility = null;
  trigger = get("#start-home-tour");
  frame = start(trigger);
  frame.finishPreparing();
  await new Promise(setImmediate);
  for (let index = 0; index < stops.length; index++) {
    assertStop(index);
    activeOverlay.actions.onNext();
  }
  returned(frame, trigger, ["upload", null]);
  assert.deepEqual(errors, []);

  frame = start(trigger);
  frame.dispatchEvent(new Event("error"));
  returned(frame, trigger, ["upload", null]);
  assert.equal(errors.length, 1, "A failed preview load should explain how to retry");
  frame.finishPreparing();
  await new Promise(setImmediate);
  assert.ok(activeOverlay.closed);
}
test().catch(error => { console.error(error); process.exitCode = 1; });
