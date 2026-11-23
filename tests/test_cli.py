"""Exercise CLI outcomes and report exports at their public boundaries."""

from contextlib import redirect_stderr, redirect_stdout
import csv
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dqi.__main__ import export, main
from dqi.core import inspect, load_csv


CLEAN_CSV = "evaluation_id,response,rating\nA1,A complete answer,4\n"


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.source = self.directory / "source.csv"
        self.source.write_text(CLEAN_CSV, encoding="utf-8")

    def run_main(self, *arguments):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main([str(arg) for arg in arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def write_config(self, config):
        path = self.directory / "rules.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        return path

    def digest(self, path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_clean_data_prints_json_and_exits_zero_without_changing_input(self):
        before = self.digest(self.source)
        code, stdout, stderr = self.run_main(self.source)
        report = json.loads(stdout)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["total_records"], 1)
        self.assertEqual(report["finding_count"], 0)
        self.assertEqual(self.digest(self.source), before)

    def test_findings_print_json_and_exit_one(self):
        self.source.write_text("evaluation_id,response,rating\nA1,,8\n", encoding="utf-8")
        code, stdout, stderr = self.run_main(self.source)
        report = json.loads(stdout)
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        self.assertEqual(report["status"], "issues_found")
        self.assertEqual(report["affected_records"], 1)
        self.assertEqual(report["finding_count"], 2)

    def test_bad_input_exits_two_with_error_instead_of_a_success_report(self):
        self.source.write_text("evaluation_id,response,rating\nA1,Answer\n", encoding="utf-8")
        code, stdout, stderr = self.run_main(self.source)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Error:", stderr)

    def test_bad_config_exits_two_without_creating_reports(self):
        config = self.write_config({"rating_range": [5, 1]})
        output = self.directory / "reports"
        code, stdout, stderr = self.run_main(self.source, "--config", config, "--output-dir", output)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Error:", stderr)
        self.assertFalse(output.exists())

    def test_no_checks_and_incomplete_results_exit_three(self):
        cases = (
            (
                "no_checks_selected",
                {"required": [], "unique_id": False, "numeric_rating": False, "rating_range": None},
                CLEAN_CSV,
            ),
            (
                "incomplete",
                {"numeric_rating": False},
                "evaluation_id,response,rating\nA1,Answer,excellent\n",
            ),
        )
        for expected_status, rules, content in cases:
            with self.subTest(status=expected_status):
                self.source.write_text(content, encoding="utf-8")
                config = self.write_config(rules)
                code, stdout, stderr = self.run_main(self.source, "--config", config)
                self.assertEqual(code, 3)
                self.assertEqual(stderr, "")
                self.assertEqual(json.loads(stdout)["status"], expected_status)

    def test_report_export_preserves_json_values_and_marks_csv_formula_literals(self):
        ratings = ["=2+2", "+oops", "-1", "@score", "\t7", "bad", "'=2+2"]
        with self.source.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["evaluation_id", "response", "rating"])
            for i, rating in enumerate(ratings, 1):
                writer.writerow([f"A{i}", "Answer", rating])
        before = self.digest(self.source)
        output = self.directory / "reports"
        code, stdout, stderr = self.run_main(self.source, "--output-dir", output)
        self.assertEqual(code, 1)
        self.assertEqual(stderr, "")
        self.assertIn("issues_found", stdout)
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([row["value"] for row in report["findings"]], ratings)
        with (output / "findings.csv").open(encoding="utf-8", newline="") as handle:
            exported = list(csv.DictReader(handle))
        self.assertEqual(
            [row["value"] for row in exported],
            ["'=2+2", "'+oops", "'-1", "'@score", "'\t7", "bad", "'=2+2"],
        )
        with (output / "skipped-checks.csv").open(encoding="utf-8", newline="") as handle:
            skipped = list(csv.DictReader(handle))
        self.assertEqual(len(skipped), report["skipped_check_count"])
        self.assertEqual(skipped[0]["value"], "'=2+2")
        self.assertEqual(self.digest(self.source), before)

    def test_existing_reports_are_preserved_until_overwrite_is_explicit(self):
        output = self.directory / "reports"
        result = inspect(load_csv(self.source))
        export(result, output, self.source)
        sentinel = output / "report.json"
        sentinel.write_text("Existing report stays untouched", encoding="utf-8")
        before = {path.name: path.read_bytes() for path in output.iterdir()}
        code, stdout, stderr = self.run_main(self.source, "--output-dir", output)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("overwrite", stderr.lower())
        self.assertEqual({path.name: path.read_bytes() for path in output.iterdir()}, before)
        code, stdout, stderr = self.run_main(self.source, "--output-dir", output, "--overwrite")
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertEqual(json.loads(sentinel.read_text(encoding="utf-8"))["status"], "passed")
        self.assertEqual(self.source.read_text(encoding="utf-8"), CLEAN_CSV)

    def test_direct_source_collision_is_rejected_even_with_overwrite(self):
        output = self.directory / "reports"
        output.mkdir()
        source = output / "findings.csv"
        source.write_text(CLEAN_CSV, encoding="utf-8")
        before = self.digest(source)
        code, stdout, stderr = self.run_main(source, "--output-dir", output, "--overwrite")
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("overwrite", stderr.lower())
        self.assertEqual(self.digest(source), before)
        self.assertFalse((output / "report.json").exists())

    def test_hardlink_source_alias_is_rejected_before_any_report_is_written(self):
        output = self.directory / "reports"
        output.mkdir()
        alias = output / "findings.csv"
        try:
            os.link(self.source, alias)
        except OSError as error:
            self.skipTest(f"Filesystem does not support a hardlink fixture: {error}")
        before = self.digest(self.source)
        result = inspect(load_csv(self.source))
        with self.assertRaises(ValueError):
            export(result, output, self.source, overwrite=True)
        self.assertEqual(self.digest(self.source), before)
        self.assertEqual(self.digest(alias), before)
        self.assertFalse((output / "report.json").exists())

    def test_config_collision_is_rejected_even_with_overwrite(self):
        output = self.directory / "reports"
        output.mkdir()
        config = output / "report.json"
        config.write_text("{}", encoding="utf-8")
        before_source, before_config = self.digest(self.source), self.digest(config)
        code, stdout, stderr = self.run_main(
            self.source, "--config", config, "--output-dir", output, "--overwrite"
        )
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("overwrite", stderr.lower())
        self.assertEqual(self.digest(self.source), before_source)
        self.assertEqual(self.digest(config), before_config)
        self.assertFalse((output / "findings.csv").exists())

    def test_config_hardlink_alias_is_protected_by_public_export(self):
        config = self.write_config({})
        output = self.directory / "reports"
        output.mkdir()
        alias = output / "report.json"
        try:
            os.link(config, alias)
        except OSError as error:
            self.skipTest(f"Filesystem does not support a hardlink fixture: {error}")
        before = self.digest(config)
        result = inspect(load_csv(self.source))
        with self.assertRaises(ValueError):
            export(result, output, self.source, overwrite=True, config_source=config)
        self.assertEqual(self.digest(config), before)
        self.assertFalse((output / "findings.csv").exists())


if __name__ == "__main__":
    unittest.main()
