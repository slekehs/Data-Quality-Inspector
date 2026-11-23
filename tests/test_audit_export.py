"""Audit exports preserve original values and protect inputs before writing."""

import csv
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from dqi.__main__ import export
from dqi.json_utils import loads_json
from tests.test_reporting import example_audit, example_report


class AuditExportTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.source = self.directory / "source.json"
        self.source.write_text("[]", encoding="utf-8")
        self.report = example_report()
        self.report["audit"] = example_audit()
        self.result = SimpleNamespace(to_dict=lambda: self.report, findings=(), skipped_checks=())

    def test_review_csv_has_full_context_exact_ratings_and_formula_protection(self):
        row = self.report["audit"]["review_queue"][0]
        row.update(prompt="=HYPERLINK(\"https://invalid\")", response="+" + "x" * 1500, ratings={"score": Decimal("4.000000000000000000001")})
        output = self.directory / "report"
        export(self.result, output, self.source)
        with (output / "review-queue.csv").open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            self.assertEqual(reader.fieldnames, ["record", "source_line", "priority", "reasons", "prompt", "response", "ratings"])
            exported = list(reader)
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["prompt"], "'" + row["prompt"])
        self.assertEqual(exported[0]["response"], "'" + row["response"])
        self.assertEqual(loads_json(exported[0]["ratings"]), row["ratings"])
        saved = loads_json((output / "report.json").read_text())
        self.assertEqual(saved["audit"]["review_queue"][0], row)
        self.assertEqual(self.source.read_text(), "[]")

    def test_review_csv_input_collision_is_rejected_before_writing(self):
        output = self.directory / "report"
        output.mkdir()
        source = output / "review-queue.csv"
        source.write_text("original source", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "overwrite an input"):
            export(self.result, output, source, overwrite=True)
        self.assertEqual(source.read_text(), "original source")
        self.assertEqual([path.name for path in output.iterdir()], ["review-queue.csv"])

    def test_legacy_export_keeps_five_files_and_audit_empty_queue_gets_header(self):
        self.report.pop("audit")
        legacy = self.directory / "legacy"
        export(self.result, legacy, self.source)
        self.assertEqual(len(list(legacy.iterdir())), 5)
        self.assertFalse((legacy / "review-queue.csv").exists())
        self.report["audit"] = {**example_audit(), "review_queue": [], "review_record_count": 0}
        audited = self.directory / "audited"
        export(self.result, audited, self.source)
        with (audited / "review-queue.csv").open(encoding="utf-8", newline="") as handle:
            self.assertEqual(len(list(csv.reader(handle))), 1)


if __name__ == "__main__":
    unittest.main()
