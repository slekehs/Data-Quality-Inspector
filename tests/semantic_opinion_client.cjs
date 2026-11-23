// Exercise real client event handlers with an in-memory report and stub transport.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const staticDir = process.argv[2];
const source = fs.readFileSync(path.join(staticDir, 'semantic-ui.js'), 'utf8');
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');

class Node {
  constructor(text = '', className = '') {
    this.textContent = text || ''; this.className = className; this.value = '';
    this.children = []; this.handlers = {}; this.hidden = false; this.disabled = false;
  }
  get options() { return this.children; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; if (children.length) this.value = children[0].value; }
  addEventListener(name, handler) { this.handlers[name] = handler; }
  setAttribute(name, value) { this[name] = value; }
  cloneNode() { return Object.assign(new Node(this.textContent), {value: this.value}); }
  showModal() { this.open = true; }
  close() { this.open = false; this.handlers.close(); }
  scrollIntoView() {}
  all() { return [this, ...this.children.flatMap((node) => node.all())]; }
}

const elements = new Map([...html.matchAll(/id="([^"]+)"/g)].map((match) => ['#' + match[1], new Node()]));
const $ = (id) => { assert.ok(elements.has(id), id); return elements.get(id); };
const modelOptions = ['gpt-6.2-sol', 'gpt-6.2-astra', 'custom'];
for (const [index, value] of modelOptions.entries()) {
  assert.ok(html.includes(`<option value="${value}">`));
  $('#semantic-model').append(Object.assign(new Node(['GPT-6.2 Sol', 'GPT-6.2 Astra', 'Other'][index]), {value}));
}
const assessment = (score, rationale) => ({field: 'helpfulness', comparison: 'close', original_score: 4, suggested_score: score, difference: score-4, rationale, evidence:[], limitations:[]});
const primaryRow = {record: 7, status:'reviewed', prompt:'Q', response:'A', assessments:[assessment(3, 'Primary explanation')]};
const parent = {job_id:'parent',status:'completed',processed:1,total:1,urls:{zip:'/primary.zip'}, result:{model:modelOptions[0],rows:[primaryRow]}};
const child = {job_id:'child',parent_job_id:'parent',status:'completed',processed:1,total:1,urls:{html:'/second.html',zip:'/second.zip'},result:{model:modelOptions[1],primary_review:{job_id:'parent',model:modelOptions[0],record:7,assessments:primaryRow.assessments},rows:[{...primaryRow,assessments:[assessment(4,'Second explanation')]}]}};
const calls = [], storage = new Map();
let failConfirmation = false;
const state = {bootstrap:{csrf_token:'csrf'},answerReviewRunning:false};
const ctx = vm.createContext({
  $, state, document:{querySelectorAll: () => $('#semantic-result-rows').all().filter((node) => node.className.includes('opinion-trigger'))},
  window:new Node(), element:(tag,text,cls) => new Node(text,cls), auditMetric:(s) => new Node(s), updateNavigation(){}, singleTaskBusy(){},
  scoreText:String, valueText:String, number:String, setTimeout,clearTimeout,crypto:require('node:crypto').webcrypto,
  sessionStorage:{setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k),getItem:k=>storage.get(k)},
  api:async (url, opts) => {
    const payload = opts.body && JSON.parse(opts.body);
    calls.push({url,payload});
    if (url.endsWith('/second-opinion')) {
      if (failConfirmation) throw new Error('lost confirmation');
      return {job_id:'child'};
    }
    if (url.includes('/request/') || url.endsWith('/child')) return child;
    if (url.endsWith('/parent')) return parent;
    throw new Error('Unexpected request '+url);
  },parent,
});
vm.runInContext(fs.readFileSync(path.join(staticDir, 'settings.js'), 'utf8'), ctx);
vm.runInContext(source,ctx);
const run = (s) => vm.runInContext(s,ctx);
const trigger = () => $('#semantic-result-rows').all().find((node) => node.className.includes('opinion-trigger'));
const ev = {preventDefault(){}};
(async () => {
  run('renderSemanticResults(parent.result,parent.urls,parent.status,parent.job_id)');
  assert.equal(calls.length,0);
  trigger().handlers.click();
  assert.ok($('#opinion-dialog').open);
  assert.equal($('#opinion-model').value,'gpt-6.2-astra');
  assert.ok(!$('#opinion-model').options.some((option)=>option.value==='gpt-6.2-sol'));
  await $('#opinion-form').handlers.submit(ev);
  assert.equal(calls.length,0);
  $('#opinion-key').value='fake-key'; $('#opinion-consent').checked=true;
  $('#opinion-model').value='custom'; $('#opinion-custom-model').value='gpt-6.2-sol';
  assert.equal(run('opinionReady()'),false);
  $('#opinion-custom-model').value='my-compatible-model'; assert.equal(run('opinionReady()'),true);
  $('#opinion-model').value='gpt-6.2-astra';
  failConfirmation=true;
  const start = $('#opinion-form').handlers.submit(ev);
  await $('#opinion-form').handlers.submit(ev); // duplicate submission while busy
  await start;
  const starts = calls.filter((call)=>call.payload);
  assert.equal(starts.length,1);
  assert.deepEqual(Object.keys(starts[0].payload).sort(),['parent_job_id','record','model','request_id','api_key','consent'].sort());
  assert.equal(starts[0].payload.record,7);
  assert.equal(starts[0].payload.parent_job_id,'parent');
  assert.equal(starts[0].payload.model,'gpt-6.2-astra');
  assert.equal($('#opinion-key').value,'');
  assert.equal(storage.size,0);
  assert.equal($('#semantic-download').href,'/primary.zip');
  const noteTexts = () => $('#opinion-comparison').all().map((node)=>node.textContent);
  assert.ok(noteTexts().includes('Primary explanation') && noteTexts().includes('Second explanation'));

  // Reload on a retained child. Both reviewer explanations and the whole original report return.
  run('semanticState.jobId="child";semanticState.primaryJobId=null');
  $('#semantic-results').hidden=true; $('#opinion-comparison').replaceChildren();
  await run('pollSemanticReview()');
  assert.equal(calls.at(-1).url,'/api/answer-review/parent');
  assert.equal($('#semantic-results').hidden,false);
  assert.equal($('#semantic-download').href,'/primary.zip');
  assert.ok(noteTexts().includes('Primary explanation') && noteTexts().includes('Second explanation'));
  assert.ok(trigger());
  run('applyKeyStatus({supported:true, saved:true})');
  trigger().handlers.click();
  const previousStarts = calls.filter((call) => call.payload).length;
  await $('#opinion-form').handlers.submit(ev);
  assert.equal(calls.filter((call) => call.payload).length, previousStarts);
  $('#opinion-consent').checked = true;
  await $('#opinion-form').handlers.submit(ev);
  const savedRequest = calls.filter((call) => call.payload).at(-1).payload;
  assert.equal(savedRequest.use_saved_key, true);
  assert.ok(!Object.hasOwn(savedRequest, 'api_key'));
})().catch(error=>{console.error(error);process.exitCode=1;});
