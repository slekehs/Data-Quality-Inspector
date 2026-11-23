"""Exercise the browser's actual answer-review payload builder with exact numbers."""
from decimal import Decimal
import json
from pathlib import Path
import shutil
import subprocess
import unittest


NODE = shutil.which('node')
STATIC = Path(__file__).resolve().parents[1] / 'dqi' / 'static'


@unittest.skipUnless(NODE, 'Node.js is needed only for browser payload regression tests')
class SemanticClientTests(unittest.TestCase):
    def test_payload_preserves_decimals_and_treats_rubric_text_as_data(self):
        script = r'''
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
function includeFunction(source, name, nextDeclaration) {
  const start = source.indexOf('function ' + name + '(');
  const end = source.indexOf('\n' + nextDeclaration, start);
  assert.ok(start >= 0 && end > start, 'Function boundaries must exist');
  vm.runInThisContext(source.slice(start, end));
}
const app = fs.readFileSync(process.argv[1], 'utf8');
const semantic = fs.readFileSync(process.argv[2], 'utf8');
includeFunction(app, 'decimalLiteral', 'function inspectionBody(');
includeFunction(semantic, 'semanticOptions', 'function semanticRequestBody(');
includeFunction(semantic, 'semanticRequestBody', 'async function previewSemanticReview(');
const values = {
  '#semantic-limit': '2', '#semantic-tolerance': '0.1000000000000000000000000001',
  '#semantic-selection': 'records', '#semantic-records': '1, 2',
  '#semantic-prompt': 'question"\\\n', '#semantic-response': 'answer', '#semantic-reference': ''
};
function $(selector) { return {value: values[selector]}; }
const description = 'Meaning: "accurate".\nLiteral JSON: },"consent":false,{\n</script><script>globalThis.polluted=true</script>';
const semanticState = {rubrics: [
  {enabled: true, field: '__proto__', description, min: '-0.0000000000000000000000000001', max: '4.0000000000000000000000000001', integer: false}
]};
const options = semanticOptions();
const body = semanticRequestBody({report_id: 'report"\\\n', options, model: 'fixture-model', api_key: 'synthetic-test-value', consent: true});
const parsed = JSON.parse(body);
assert.equal(parsed.options.rubrics[0].field, '__proto__');
assert.equal(parsed.options.rubrics[0].description, description);
assert.equal(parsed.options.prompt_field, values['#semantic-prompt']);
assert.equal(parsed.consent, true);
assert.equal(parsed.options.reference_field, null);
assert.deepEqual(parsed.options.records, [1, 2]);
assert.equal(typeof parsed.options.tolerance, 'number');
assert.equal(typeof parsed.options.rubrics[0].min, 'number');
assert.equal({}.polluted, undefined);
assert.equal(globalThis.polluted, undefined);
assert.throws(() => semanticRequestBody({options: {...options, tolerance: '0,"consent":false'}}));
assert.throws(() => semanticRequestBody({options: {...options, rubrics: [{...options.rubrics[0], max: '4], "extra": 5'}]}}));
process.stdout.write(body);
'''
        completed = subprocess.run([NODE, '-e', script, str(STATIC / 'app.js'), str(STATIC / 'semantic-ui.js')],
                                   capture_output=True, text=True, timeout=10)
        self.assertEqual(0, completed.returncode, completed.stderr)
        payload = json.loads(completed.stdout, parse_float=Decimal)
        self.assertEqual(Decimal('0.1000000000000000000000000001'), payload['options']['tolerance'])
        self.assertEqual(Decimal('-0.0000000000000000000000000001'), payload['options']['rubrics'][0]['min'])
        self.assertEqual(Decimal('4.0000000000000000000000000001'), payload['options']['rubrics'][0]['max'])
        self.assertIs(payload['consent'], True)
        self.assertEqual('__proto__', payload['options']['rubrics'][0]['field'])


    def test_imported_and_manual_criteria_in_review_request(self):
        fixture = Path(__file__).with_name('semantic_rubric_client.cjs')
        completed = subprocess.run([NODE, str(fixture), str(STATIC)], capture_output=True, text=True, timeout=10)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_optional_second_opinion_request_and_refresh(self):
        fixture = Path(__file__).with_name('semantic_opinion_client.cjs')
        completed = subprocess.run([NODE, str(fixture), str(STATIC)], capture_output=True, text=True, timeout=10)
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_dataset_scope_quote_confirmation_and_paging(self):
        fixture = Path(__file__).with_name('semantic_cost_client.cjs')
        completed = subprocess.run([NODE, str(fixture), str(STATIC)], capture_output=True, text=True, timeout=10)
        self.assertEqual(0, completed.returncode, completed.stderr)


if __name__ == '__main__':
    unittest.main()
