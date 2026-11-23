import csv
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from dqi.semantic_reporting import export_review, render_html


def report_fixture():
    return {
        "source": {"filename": "<script>bad</script>.json", "sha256": "f" * 64, "app_version": "0.6.0"},
        "provider": "openai", "model": "test-model", "run_status": "cancelled", "completed_at": "2026-11-23T00:00:00Z",
        "selected_count": 3, "processed_count": 2, "pending_count": 1, "total_records": 100,
        "selection_note": "Selected records; not representative.", "counts": {"questionable": 1},
        "options": {"tolerance": 1}, "rubric_version": "v1", "rubric_sha256": "e" * 64,
        "attributes": [{"field": "rating", "description": "<img src=x onerror=bad>", "min": 0, "max": 4, "integer": False}],
        "limitations": ["No independent fact verification."],
        "rows": [
            {"record": 1, "source_line": 2, "status": "reviewed", "prompt": "=QUESTION", "response": "<script>bad</script>" + "x" * 2000, "reference": None, "original_ratings": {"rating": Decimal("3.0000000000000000001")}, "assessments": [
                {"field": "rating", "original_score": Decimal("3.0000000000000000001"), "suggested_score": 0, "difference": Decimal("-3.0000000000000000001"), "comparison": "questionable", "rationale": "@unsafe formula", "evidence": [{"source": "response", "quote": "<script>bad</script>"}], "limitations": ["<b>not markup</b>"]}
            ]},
            {"record": 2, "source_line": 3, "status": "skipped", "prompt": "", "response": "Answer", "error": "Prompt missing; no call made.", "assessments": []},
        ],
    }


class SemanticReportingTests(unittest.TestCase):
    def second_opinion_fixture(self):
        report = report_fixture()
        row = report["rows"][0]
        first = {**row["assessments"][0], "suggested_score": None, "comparison": "insufficient_evidence",
                 "rationale": "=first rationale <unsafe>", "evidence": [{"source": "response", "quote": "<first quote>"}],
                 "limitations": ["Missing reference"]}
        report.update(rows=[row], primary_review={"job_id": "first-job", "model": "<first-model>",
                                                  "record": row["record"], "assessments": [first]})
        return report

    def test_second_opinion_bundle_contains_independent_reviews_and_evidence(self):
        report = self.second_opinion_fixture()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "export"
            export_review(report, path)
            with ZipFile(path / "answer-review.zip") as archive:
                html = archive.read("answer-review.html").decode()
                table = list(csv.DictReader(StringIO(archive.read("answer-review.csv").decode())))
                saved = json.loads(archive.read("answer-review.json"), parse_float=Decimal)
            self.assertEqual(saved, report)
            self.assertEqual(len(table), 1)
            self.assertEqual(table[0]["original_score"], "3.0000000000000000001")
            self.assertEqual(table[0]["primary_model"], "<first-model>")
            self.assertEqual(table[0]["second_opinion_model"], "test-model")
            self.assertEqual(table[0]["primary_suggested_score"], "")
            self.assertEqual(table[0]["primary_comparison"], "insufficient_evidence")
            self.assertEqual(table[0]["primary_rationale"], "'=first rationale <unsafe>")
            self.assertEqual(json.loads(table[0]["primary_evidence"])[0]["quote"], "<first quote>")
            self.assertEqual(table[0]["suggested_score"], "0")
            self.assertIn("Primary review · &lt;first-model&gt; · Undecided", html)
            self.assertIn("Second opinion · test-model · 0", html)
            self.assertIn("&lt;first quote&gt;", html)
            self.assertIn("Missing reference", html)
            self.assertNotIn("<first-model>", html)
            self.assertNotIn("<unsafe>", html)

    def test_second_opinion_failure_keeps_first_review_available(self):
        for rows_returned in (False, True):
            with self.subTest(rows_returned=rows_returned), TemporaryDirectory() as temp:
                report = self.second_opinion_fixture()
                report["rows"] = ([{**report["rows"][0], "status": "error", "error": "Provider error",
                                    "assessments": []}] if rows_returned else [])
                path = Path(temp) / "export"
                export_review(report, path)
                html = (path / "answer-review.html").read_text()
                row, = csv.DictReader(StringIO((path / "answer-review.csv").read_text()))
                self.assertIn("Second opinion · test-model</h4><p>No assessment returned.", html)
                self.assertIn("Primary review · &lt;first-model&gt; · Undecided", html)
                self.assertEqual(row["primary_rationale"], "'=first rationale <unsafe>")
                self.assertEqual(row["suggested_score"], "")
                self.assertEqual(row["record_status"], "error" if rows_returned else "not_processed")

    def test_fields_are_paired_by_name_across_differing_assessment_lists(self):
        report = self.second_opinion_fixture()
        second = report["rows"][0]["assessments"][0]
        second["field"] = "another field"
        with TemporaryDirectory() as temp:
            path = Path(temp) / "export"
            export_review(report, path)
            table = list(csv.DictReader(StringIO((path / "answer-review.csv").read_text())))
            self.assertEqual([(r["field"], r["primary_rationale"], r["rationale"]) for r in table],
                             [("rating", "'=first rationale <unsafe>", ""),
                              ("another field", "", "'@unsafe formula")])

    def test_instruction_provenance_is_readable_and_escaped(self):
        report = report_fixture()
        report["reviewer_instructions"] = "Judge only the answer. <script>untrusted</script>"
        report["instructions_sha256"] = "a" * 64
        html = render_html(report)
        self.assertIn("Reviewer instructions used for this run", html)
        self.assertIn("Judge only the answer. &lt;script&gt;untrusted&lt;/script&gt;", html)
        self.assertIn("a" * 64, html)
        self.assertNotIn("<script>untrusted</script>", html)
        # Reports predating instruction capture still render without invented provenance.
        old = render_html(report_fixture())
        self.assertNotIn("Reviewer instructions used for this run", old)

    def test_html_retains_context_and_escapes_model_source_and_rubric(self):
        report = report_fixture()
        html = render_html(report)
        for raw in ("<script>bad</script>", "<img src=x onerror=bad>", "<b>not markup</b>"):
            self.assertNotIn(raw, html)
        self.assertIn("&lt;script&gt;bad&lt;/script&gt;", html)
        self.assertIn("3.0000000000000000001", html)
        self.assertIn("x" * 2000, html)
        self.assertIn("2 of 3 selected records processed", html)
        self.assertIn("1 not processed", html)
        self.assertIn("not verified ground truth", html)
        self.assertIn("Prompt missing; no call made.", html)

    def test_exports_preserve_partial_results_and_protect_spreadsheet_cells(self):
        report = report_fixture()
        with TemporaryDirectory() as temp:
            path = Path(temp) / "export"
            export_review(report, path)
            raw_json = (path / "answer-review.json").read_text()
            self.assertIn("3.0000000000000000001", raw_json)
            self.assertEqual(json.loads(raw_json)["pending_count"], 1)
            rows = list(csv.DictReader(StringIO((path / "answer-review.csv").read_text())))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["prompt"], "'=QUESTION")
            self.assertEqual(rows[0]["rationale"], "'@unsafe formula")
            self.assertEqual(rows[1]["record_status"], "skipped")
            with ZipFile(path / "answer-review.zip") as archive:
                self.assertEqual(set(archive.namelist()), {"answer-review.html", "answer-review.json", "answer-review.csv"})
                self.assertEqual(archive.read("answer-review.html"), (path / "answer-review.html").read_bytes())
            with self.assertRaises(FileExistsError):
                export_review(report, path)


if __name__ == "__main__":
    unittest.main()
