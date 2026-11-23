"""Acceptance coverage for native, compressed dataset ingestion."""

from contextlib import redirect_stderr, redirect_stdout
import csv
from decimal import Decimal
import gzip
import hashlib
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dqi import load_dataset
from dqi.__main__ import main
from dqi.core import Config, DatasetError, inspect, load_csv, load_dataset as core_load_dataset
from dqi.json_utils import dumps_json


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def write(self, name, content):
        path = self.directory / name
        payload = content.encode("utf-8") if isinstance(content, str) else content
        if name.lower().endswith(".gz"):
            payload = gzip.compress(payload)
        path.write_bytes(payload)
        return path

    def run_main(self, *arguments):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([str(arg) for arg in arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def helpfulness_config(self):
        return Config.from_dict(
            {
                "columns": {"rating": "helpfulness"},
                "required": ["response", "rating"],
                "unique_id": False,
                "rating_range": [0, 4],
            }
        )

    def test_autodetects_case_insensitive_formats_aliases_and_compression(self):
        row = {"evaluation_id": "A1", "response": "An answer", "rating": 4}
        fixtures = [
            ("CSV", "csv", "evaluation_id,response,rating\nA1,An answer,4\n"),
            ("TSV", "tsv", "evaluation_id\tresponse\trating\nA1\tAn answer\t4\n"),
            ("JSON", "json", json.dumps([row])),
            ("JSONL", "jsonl", json.dumps(row) + "\n"),
            ("NDJSON", "jsonl", json.dumps(row) + "\n"),
        ]
        for suffix, expected, content in fixtures:
            for compressed in (False, True):
                with self.subTest(format=suffix, compressed=compressed):
                    path = self.write(f"data.{suffix}" + (".GZ" if compressed else ""), content)
                    data = load_dataset(path)
                    self.assertEqual(data.input_format, expected)
                    self.assertEqual(data.headers, ("evaluation_id", "response", "rating"))
                    self.assertEqual(len(data.records), 1)
                    self.assertEqual(data.records[0].number, 1)
                    self.assertEqual(inspect(data).status, "passed")

    def test_explicit_format_handles_unknown_suffix_and_gzip(self):
        fixtures = {
            "csv": "evaluation_id,response,rating\nA1,Answer,4\n",
            "tsv": "evaluation_id\tresponse\trating\nA1\tAnswer\t4\n",
            "json": '[{"evaluation_id":"A1","response":"Answer","rating":4}]',
            "jsonl": '{"evaluation_id":"A1","response":"Answer","rating":4}\n',
            "ndjson": '{"evaluation_id":"A1","response":"Answer","rating":4}\n',
        }
        for fmt, content in fixtures.items():
            for compressed in (False, True):
                with self.subTest(format=fmt, compressed=compressed):
                    path = self.write("download.bin" + (".gz" if compressed else ""), content)
                    data = core_load_dataset(path, format=fmt)
                    self.assertEqual(data.input_format, "jsonl" if fmt == "ndjson" else fmt)
                    self.assertEqual(inspect(data).status, "passed")

    def test_legacy_csv_loader_remains_compatible(self):
        path = self.write("data.csv", "evaluation_id,response,rating\nA1,Answer,4\n")
        original = load_csv(path)
        detected = load_dataset(path)
        self.assertEqual(original.headers, detected.headers)
        self.assertEqual(original.records, detected.records)
        self.assertEqual(detected.records[0].values["rating"], "4")

    def test_json_unions_headers_in_first_seen_order_and_preserves_values(self):
        records = [
            {"response": "First", "rating": 0, "flag": True, "metadata": {"source": "test"}},
            {"rating": 4.5, "response": "Second", "optional": None, "tags": ["sample"]},
        ]
        data = load_dataset(self.write("data.json", json.dumps(records)))
        self.assertEqual(data.headers, ("response", "rating", "flag", "metadata", "optional", "tags"))
        self.assertEqual([record.values for record in data.records], records)
        self.assertIs(type(data.records[0].values["rating"]), int)
        self.assertIs(data.records[0].values["flag"], True)
        self.assertIsNone(data.records[1].values["optional"])
        self.assertTrue(all(record.source_line is None for record in data.records))

    def test_missing_and_explicit_null_fields_have_distinct_report_evidence(self):
        records = [
            {"evaluation_id": "A1", "response": "Answer", "rating": 4},
            {"evaluation_id": "A2", "response": "Answer"},
            {"evaluation_id": "A3", "response": "Answer", "rating": None},
        ]
        result = inspect(load_dataset(self.write("data.json", json.dumps(records))))
        self.assertEqual(len(result.findings), 2)
        by_record = {finding.record: finding for finding in result.findings}
        self.assertEqual(by_record[2].rule, "required")
        self.assertIsNone(by_record[2].value)
        self.assertIs(by_record[2].field_present, False)
        self.assertIsNone(by_record[3].value)
        self.assertIs(by_record[3].field_present, True)
        self.assertTrue(all(f.source_line is None for f in result.findings))
        self.assertEqual(list(result.skipped_checks), [])
        serialized = result.to_dict()["findings"]
        self.assertEqual([f["field_present"] for f in serialized], [False, True])

    def test_field_absent_from_every_nonempty_record_is_a_mapping_error(self):
        records = [{"evaluation_id": "A1", "response": "Answer"}]
        data = load_dataset(self.write("data.json", json.dumps(records)))
        with self.assertRaisesRegex(DatasetError, "rating"):
            inspect(data)

    def test_empty_json_and_jsonl_are_empty_datasets_not_mapping_errors(self):
        for name, content in (("empty.json", "[]"), ("empty.jsonl", "\n  \n\t\n")):
            with self.subTest(format=name):
                data = load_dataset(self.write(name, content))
                result = inspect(data)
                self.assertEqual(result.total_records, 0)
                self.assertEqual(result.status, "empty_dataset")
                self.assertEqual(list(result.findings), [])

    def test_jsonl_blank_lines_preserve_physical_lines_and_logical_numbers(self):
        first = '{"evaluation_id":"A1","response":"First","rating":9}'
        second = '{"evaluation_id":"A2","response":"Second","rating":8}'
        data = load_dataset(self.write("data.jsonl", "\n" + first + "\n \n" + second + "\n"))
        result = inspect(data)
        self.assertEqual([(r.number, r.source_line) for r in data.records], [(1, 2), (2, 4)])
        self.assertEqual([(f.record, f.source_line) for f in result.findings], [(1, 2), (2, 4)])

    def test_json_shapes_require_an_array_of_objects_or_objects_per_line(self):
        fixtures = (
            ("object.json", '{"response":"Answer"}'),
            ("scalar.json", "4"),
            ("scalar-row.json", '[{"response":"Answer"},4]'),
            ("null-row.json", "[null]"),
            ("array-line.jsonl", '[{"response":"Answer"}]\n'),
            ("scalar-line.jsonl", "4\n"),
            ("null-line.jsonl", "null\n"),
        )
        for name, content in fixtures:
            with self.subTest(file=name):
                with self.assertRaises(DatasetError):
                    load_dataset(self.write(name, content))

    def test_duplicate_object_keys_and_nonstandard_constants_are_rejected(self):
        invalid_rows = (
            '{"rating":1,"rating":2}',
            '{"metadata":{"same":1,"same":2}}',
            '{"rating":NaN}',
            '{"rating":Infinity}',
            '{"rating":-Infinity}',
        )
        for suffix in ("json", "jsonl"):
            for row in invalid_rows:
                with self.subTest(format=suffix, row=row):
                    content = "[" + row + "]" if suffix == "json" else row + "\n"
                    with self.assertRaises(DatasetError):
                        load_dataset(self.write(f"invalid.{suffix}", content))

    def test_malformed_jsonl_error_identifies_the_physical_line(self):
        path = self.write("invalid.jsonl", '{"response":"Answer"}\n\n{"rating":}\n')
        with self.assertRaisesRegex(DatasetError, r"(?i)line\s+3"):
            load_dataset(path)

    def test_nested_extra_values_are_allowed_but_active_structures_are_rejected(self):
        good = {
            "response": "Answer",
            "helpfulness": 4,
            "metadata": {"annotators": ["sample"]},
        }
        config = self.helpfulness_config()
        data = load_dataset(self.write("good.json", json.dumps([good])))
        self.assertEqual(inspect(data, config).status, "passed")
        for field, value in (("response", {"text": "Answer"}), ("helpfulness", [4])):
            with self.subTest(field=field):
                bad = dict(good, **{field: value})
                data = load_dataset(self.write("bad.json", json.dumps([bad])))
                with self.assertRaisesRegex(DatasetError, field):
                    inspect(data, config)

    def test_native_rating_types_treat_bool_and_null_differently_from_numbers(self):
        rows = [
            {"response": "Zero", "helpfulness": 0},
            {"response": "Boolean", "helpfulness": True},
            {"response": "Null", "helpfulness": None},
            {"response": "Numeric string", "helpfulness": "4"},
            {"response": "Out of range", "helpfulness": 5},
        ]
        data = load_dataset(self.write("ratings.json", json.dumps(rows)))
        result = inspect(data, self.helpfulness_config())
        self.assertEqual([(f.record, f.rule) for f in result.findings], [(2, "numeric"), (3, "required"), (5, "range")])
        self.assertIs(result.findings[0].value, True)
        self.assertIsNone(result.findings[1].value)
        self.assertIs(type(result.findings[2].value), int)
        self.assertEqual(result.findings[2].value, 5)
        self.assertEqual(len(result.skipped_checks), 1)
        report = json.loads(dumps_json(result.to_dict()))
        self.assertIs(report["findings"][0]["value"], True)
        self.assertIsNone(report["findings"][1]["value"])

    def test_unique_identifiers_preserve_json_type_identity(self):
        rows = [
            {"evaluation_id": value, "response": "Answer", "rating": 4}
            for value in (1, "1", True, 1)
        ]
        result = inspect(load_dataset(self.write("ids.json", json.dumps(rows))))
        self.assertEqual([(f.record, f.rule) for f in result.findings], [(1, "unique"), (4, "unique")])
        self.assertTrue(all(tuple(f.related_records) == (1, 4) for f in result.findings))
        self.assertTrue(all(type(f.value) is int for f in result.findings))

    def test_helpsteer_style_gzipped_jsonl_cli_uses_native_fields_unchanged(self):
        rows = [
            {"prompt": "Question", "response": "First answer", "helpfulness": 0, "correctness": 1},
            {"prompt": "Question", "response": "Second answer", "helpfulness": 4, "correctness": 4},
            {"prompt": "Question", "response": "Third answer", "helpfulness": 6, "metadata": {"sample": True}},
        ]
        source = self.write("helpsteer.jsonl.gz", "\n".join(json.dumps(row) for row in rows) + "\n")
        config = self.write("rules.json", dumps_json(self.helpfulness_config().to_dict()))
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        code, stdout, stderr = self.run_main(source, "--config", config)
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        report = json.loads(stdout)
        self.assertEqual(report["total_records"], 3)
        self.assertEqual(report["finding_count"], 1)
        finding = report["findings"][0]
        self.assertEqual((finding["record"], finding["source_line"], finding["field"], finding["rule"]), (3, 3, "helpfulness", "range"))
        self.assertEqual(finding["value"], 6)
        self.assertIs(type(finding["value"]), int)
        self.assertIs(finding["field_present"], True)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_cli_explicit_format_reads_compressed_download_without_known_suffix(self):
        source = self.write("download.data.gz", '{"evaluation_id":"A1","response":"Answer","rating":4}\n')
        code, stdout, stderr = self.run_main(source, "--format", "ndjson")
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(stdout)["status"], "passed")

    def test_precise_json_fraction_is_not_rounded_into_a_valid_rating(self):
        exact = "5.000000000000000000001"
        source = self.write(
            "precise.json",
            '[{"evaluation_id":"A1","response":"Answer","rating":' + exact + "}]",
        )
        data = load_dataset(source)
        self.assertEqual(data.records[0].values["rating"], Decimal(exact))
        self.assertIs(type(data.records[0].values["rating"]), Decimal)
        code, stdout, stderr = self.run_main(source)
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        report = json.loads(stdout, parse_float=Decimal)
        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(report["findings"][0]["rule"], "range")
        self.assertEqual(report["findings"][0]["value"], Decimal(exact))
        self.assertIn('"value": ' + exact, stdout)

    def test_truncated_gzip_returns_a_clean_cli_error_without_changing_input(self):
        source = self.write("broken.jsonl.gz", '{"evaluation_id":"A1","response":"Answer","rating":4}\n')
        source.write_bytes(source.read_bytes()[:-8])
        before = source.read_bytes()
        code, stdout, stderr = self.run_main(source)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Error:", stderr)
        self.assertNotIn("Traceback", stderr)
        self.assertEqual(source.read_bytes(), before)

    def test_enormous_json_exponent_returns_a_clean_input_error(self):
        source = self.write(
            "enormous.json",
            '[{"evaluation_id":"A1","response":"Answer","rating":1e999999999999999999999999}]',
        )
        code, stdout, stderr = self.run_main(source)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Error:", stderr)
        self.assertNotIn("Traceback", stderr)

    def test_lone_surrogate_rating_exports_exact_json_and_valid_utf8_csv(self):
        source = self.write(
            "surrogate.json",
            r'[{"evaluation_id":"A1","response":"Answer","rating":"\ud800"}]',
        )
        before = source.read_bytes()
        output = self.directory / "surrogate-report"
        code, stdout, stderr = self.run_main(source, "--output-dir", output)
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        self.assertIn("issues_found", stdout)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(report["findings"][0]["value"], "\ud800")
        self.assertEqual(report["findings"][0]["rule"], "numeric")
        with (output / "findings.csv").open(encoding="utf-8", newline="") as handle:
            findings = list(csv.DictReader(handle))
        self.assertEqual(findings[0]["value"], r"\ud800")
        self.assertEqual(source.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
