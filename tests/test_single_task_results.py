import csv
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from dqi.semantic import review_record, summarize_review
from dqi.semantic_reporting import export_review, render_html
from dqi.single_task import prepare_single_task


class SingleTaskResultTests(unittest.TestCase):
    def example(self):
        prompt = "Give a color and verify the current sales price."
        response = "The color is green. The item costs 12 dollars."
        plan = prepare_single_task({
            "prompt": prompt, "response": response, "rubric_filename": "trainer.json",
            "rubrics": [{"field": name, "description": description, "min": 0, "max": 2, "integer": True}
                        for name, description in [("color", "2 for naming a color."), ("price", "2 for a verified price.")]],
        })

        def judge(request):
            self.assertEqual(request["instructions"], plan["reviewer_instructions"])
            self.assertNotIn("auditing existing", request["instructions"])
            self.assertEqual(request["input"]["prompt"], prompt)
            self.assertEqual(request["input"]["response"], response)
            return {"assessments": [
                {"field": "color", "suggested_score": 2, "rationale": "Names a color as required by anchor 2.",
                 "evidence": [{"source": "response", "quote": "The color is green."}], "limitations": []},
                {"field": "price", "suggested_score": None, "rationale": "A price is asserted without a supplied reference.",
                 "evidence": [], "limitations": ["Provide the current price from the seller."]},
            ]}

        row = review_record(plan["records"][0], plan["attributes"], judge,
                            reviewer_instructions=plan["reviewer_instructions"])
        report = summarize_review(plan, [row], provider="test", model="fixture")
        report["source"] = {"kind": "single_task", "filename": "Pasted task", "rubric_filename": "trainer.json"}
        return report

    def test_one_criterion_scored_and_another_withheld(self):
        report = self.example()
        self.assertEqual(report["review_mode"], "single_task")
        self.assertEqual(report["counts"], {"scored": 1, "insufficient_evidence": 1})
        scored, uncertain = report["rows"][0]["assessments"]
        self.assertEqual(scored["suggested_score"], 2)
        self.assertEqual(scored["limitations"], [])
        self.assertNotIn("original_score", scored)
        self.assertNotIn("difference", scored)
        self.assertIsNone(uncertain["suggested_score"])
        self.assertTrue(uncertain["limitations"])

    def test_download_includes_task_and_scales_and_omits_dataset_comparisons(self):
        report = self.example()
        with TemporaryDirectory() as tmp:
            destination = Path(tmp) / "download"
            export_review(report, destination)
            with ZipFile(destination / "answer-review.zip") as bundle:
                saved = json.loads(bundle.read("answer-review.json"))
                html = bundle.read("answer-review.html").decode()
                rows = list(csv.DictReader(StringIO(bundle.read("answer-review.csv").decode())))
        self.assertEqual(saved["review_mode"], "single_task")
        for expected in ("Task evaluation", "The color is green.", "trainer.json", "Insufficient evidence"):
            self.assertIn(expected, html)
        for unexpected in ("Original ratings", "Comparison tolerance", "Source dataset", "File fingerprint"):
            self.assertNotIn(unexpected, html)
        self.assertEqual([(r["field"], r["suggested_score"], r["score_status"]) for r in rows],
                         [("color", "2", "scored"), ("price", "", "insufficient_evidence")])
        self.assertEqual((rows[0]["min"], rows[0]["max"]), ("0", "2"))
        self.assertNotIn("original_score", rows[0])

    def test_task_second_opinion_presents_both_models(self):
        report = self.example()
        report["primary_review"] = {"record": 1, "model": "other-model", "assessments": report["rows"][0]["assessments"]}
        html = render_html(report)
        self.assertIn("Compare task evaluations", html)
        self.assertIn("Primary review · other-model · 2", html)
        self.assertIn("Second opinion · fixture · 2", html)
        self.assertNotIn("Original rating", html)
