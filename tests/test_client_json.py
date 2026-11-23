"""Check browser numeric preservation when Node.js is available for development."""

from pathlib import Path
import shutil
import subprocess
import unittest


NODE = shutil.which("node")
SCRIPT = Path(__file__).resolve().parents[1] / "dqi" / "static" / "exact-json.js"


@unittest.skipUnless(NODE, "Node.js is optional and needed only for browser parser tests")
class ClientJSONTests(unittest.TestCase):
    def assert_javascript(self, assertions):
        program = """
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
vm.runInThisContext(fs.readFileSync(process.argv[1], 'utf8'));
const parse = globalThis.DQIParseJSON;
""" + assertions
        completed = subprocess.run(
            [NODE, "-e", program, str(SCRIPT)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_integer_counts_and_json_structure_keep_their_types(self):
        self.assert_javascript("""
const source = ' {"count":42,"negative":-17,"nested":[true,false,null,{},[]],"safe":9007199254740991} ';
assert.deepEqual(parse(source), JSON.parse(source));
assert.equal(Object.is(parse('-0'), -0), true);
assert.equal(parse('0'), 0);
assert.equal(parse('-9007199254740991'), -9007199254740991);
""")

    def test_fraction_exponent_and_unsafe_integer_tokens_are_exact_text(self):
        self.assert_javascript("""
const tokens = ['4.000000000000000000001', '4.001', '4.002', '1.0', '-0.0000',
                '1e0', '1E+309', '-1e-999', '9007199254740992', '9007199254740993',
                '-9007199254740993', '123456789012345678901234567890'];
assert.deepEqual(parse('[' + tokens.join(',') + ']'), tokens);
const result = parse('{"findings":[{"record":1,"value":9007199254740993}],"mean":4.001}');
assert.equal(result.findings[0].record, 1);
assert.equal(result.findings[0].value, '9007199254740993');
assert.equal(result.mean, '4.001');
""")

    def test_escaped_strings_and_numeric_object_keys_are_unchanged(self):
        self.assert_javascript(r'''
const source = String.raw`{"1e309":"4.001","text":"quote: \"1e2\", slash: \\, tab: \t, newline: \n","unicode":"\u2603\ud83d\ude00","backslash":"\\","end":"\""}`;
assert.deepEqual(parse(source), JSON.parse(source));
assert.equal(parse('"9007199254740993"'), '9007199254740993');
for (const text of ['[] {} : , 1e309 -999.1', '\\"', '"\\', '\u0000', 'a\nb', '😀']) {
  assert.equal(parse(JSON.stringify(text)), text);
}
''')

    def test_object_keys_are_data_without_prototype_side_effects(self):
        self.assert_javascript("""
const source = '{"__proto__":{"polluted":true},"constructor":7,"toString":8,"same":1,"same":2}';
const result = parse(source);
assert.equal(Object.getPrototypeOf(result), Object.prototype);
assert.equal(Object.hasOwn(result, '__proto__'), true);
assert.equal(result.__proto__.polluted, true);
assert.equal({}.polluted, undefined);
assert.equal(result.constructor, 7);
assert.equal(result.toString, 8);
assert.equal(result.same, 2);
assert.deepEqual(result, JSON.parse(source));
""")

    def test_invalid_json_is_rejected_instead_of_rewritten_into_valid_json(self):
        self.assert_javascript(r'''
const invalid = ['', ' ', '01', '-01', '+1', '.1', '1.', '1e', '1e+', '--1',
                 'NaN', 'Infinity', '[1,]', '{"a":1,}', '{1.2:0}', '{"a" 1}',
                 '[1 2]', '{}[]', 'truefalse', 'nul', '"unterminated',
                 String.raw`"bad\xescape"`, '"bad\ncontrol"', '\u00a01'];
for (const source of invalid) assert.throws(() => parse(source), SyntaxError, source);
assert.throws(() => parse(null), TypeError);
''')


if __name__ == "__main__":
    unittest.main()
