// Load the browser scripts in file-preview mode, which needs no running server.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const nodes = new Map();
function querySelector(selector) {
  if (!nodes.has(selector)) nodes.set(selector, {value: '', addEventListener() {}});
  return nodes.get(selector);
}
const context = vm.createContext({
  document: {querySelector, querySelectorAll: () => [], createElement: () => ({}), head: {append() {}}},
  window: {location: {protocol: 'file:'}, addEventListener() {}},
});
for (const filename of ['exact-json.js', 'app.js', 'semantic-ui.js']) {
  vm.runInContext(fs.readFileSync(path.join(process.argv[2], filename), 'utf8'), context, {filename});
}
for (const [field, value] of Object.entries({
  limit: '1', tolerance: '0', prompt: 'customer_question', response: 'agent_reply', selection: 'first',
})) querySelector('#semantic-' + field).value = value;

const {semanticRubricForField, semanticOptions, semanticRequestBody, DQIParseJSON} = context;
const defaults = {
  helpfulness: {description: 'Preset helpfulness'},
  coherence: {description: 'Preset coherence', levels: {0: 'Hard to follow', 4: 'Easy to follow'}},
  correctness: {description: 'Preset correctness'},
};
const scale = {integer: true, range: [0, 4]};
const customCriterion = {filename: 'support.csv', sourceField: 'resolution', description: 'Use the support resolution anchors.'};
const helpfulness = semanticRubricForField({field: 'helpfulness', rules: scale},
  {presetField: 'helpfulness', importedRubric: customCriterion}, defaults);
const coherence = semanticRubricForField({field: 'coherence', rules: scale}, {presetField: 'coherence'}, defaults);
const manual = semanticRubricForField({field: 'correctness', rules: scale}, {field: 'correctness'}, defaults);
const tone = semanticRubricForField({
  field: 'tone_score',
  rules: DQIParseJSON('{"integer":false,"range":[-0.000000000000000000001,1.000000000000000000001]}'),
}, {importedRubric: {filename: 'support.csv', sourceField: 'tone', description: 'Use the tone anchors.'}}, defaults);

assert.equal(helpfulness.source, 'support.csv · resolution');
assert.equal(helpfulness.fromPreset, false);
assert.equal(coherence.fromPreset, true);
assert.equal(manual.description, '');
assert.equal(manual.enabled, false);
context.reviewFields = [helpfulness, coherence, manual, tone];
vm.runInContext('semanticState.rubrics = reviewFields', context);
function reviewCriteria() {
  const body = semanticRequestBody({report_id: 'test-report', options: semanticOptions()});
  const numericScale = JSON.parse(body).options.rubrics.find(rule => rule.field === 'tone_score');
  assert.equal(typeof numericScale.min, 'number');
  assert.equal(typeof numericScale.max, 'number');
  const criteria = DQIParseJSON(body).options.rubrics;
  return Object.fromEntries(criteria.map(rule => [rule.field, rule]));
}
const criteria = reviewCriteria();
assert.deepEqual(Object.keys(criteria), ['helpfulness', 'coherence', 'tone_score']);
assert.equal(criteria.helpfulness.description, customCriterion.description);
assert.equal(criteria.coherence.description, 'Preset coherence\n\n0: Hard to follow\n4: Easy to follow');
assert.equal(criteria.tone_score.min, '-0.000000000000000000001');
assert.equal(criteria.tone_score.max, '1.000000000000000000001');
assert.equal(criteria.tone_score.integer, false);
// The editor changes these same objects when the user selects a field or changes its criteria.
manual.enabled = true;
manual.description = 'Use the reference answer to assess correctness.';
helpfulness.description = 'Use the revised support resolution anchors.';
const edited = reviewCriteria();
assert.equal(edited.correctness.description, manual.description);
assert.equal(edited.helpfulness.description, helpfulness.description);
assert.equal(edited.coherence.description, criteria.coherence.description);
