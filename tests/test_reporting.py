"""Readable summaries keep scope, exclusions, and untrusted text explicit."""

from copy import deepcopy
from decimal import Decimal
from html.parser import HTMLParser
import unittest

from dqi import semantic_reporting
from dqi.report_appearance import with_report_theme
from dqi.reporting import render_html, render_text


def example_report():
    summary = {
        "field": "helpfulness",
        "rules": {"required": True, "numeric": True, "integer": True, "range": [0, 4]},
        "counts": {"total": 3, "missing": 0, "invalid_numeric": 0, "non_integer": 0, "out_of_range": 0, "valid": 3, "findings": 0, "skipped_checks": 0},
        "statistics": {"count": 3, "minimum": Decimal(0), "maximum": Decimal(4), "mean": Decimal("2.666667")},
        "distribution": [{"value": Decimal(i), "count": (1 if i == 0 else 2 if i == 4 else 0)} for i in range(5)],
    }
    second = deepcopy(summary)
    second["field"] = "verbosity"
    return {
        "status": "passed", "input_format": "jsonl", "total_records": 3,
        "affected_records": 0, "finding_count": 0, "skipped_check_count": 0,
        "config": {"columns": {"response": "response"}, "required": ["response"], "unique_id": False},
        "rating_summaries": [summary, second], "findings": [], "skipped_checks": [],
    }


def example_audit():
    return {
        "provenance": {"rule_origin": "helpsteer2", "review_thresholds": {"helpfulness": Decimal("1.000000000000000000001")}, "rating_evidence": "Existing source annotations; no answer was regraded."},
        "duplicate_group_count": 1, "duplicate_record_count": 2,
        "conflict_group_count": 1, "conflict_record_count": 2,
        "check_status": [{"check": "review_thresholds", "status": "completed", "eligible_values": 3}, {"check": "annotator_agreement", "status": "not_assessed", "reason": "Individual annotations are unavailable."}],
        "limitations": ["Scores alone do not establish answer correctness."],
        "coverage": None,
        "review_record_count": 1,
        "review_queue": [{"record": 1, "source_line": 2, "priority": "medium", "prompt": "Explain this result", "response": "An answer to review", "ratings": {"helpfulness": 1, "correctness": 4}, "reasons": [{"reason": "Existing helpfulness is at or below the selected threshold.", "action": "Review the prompt and response against the rubric."}]}],
        "positively_rated_examples": [{"record": 3, "source_line": 4, "prompt": "Another question", "response": "Another recorded answer", "ratings": {"helpfulness": 4}, "selection_reason": "Highest existing helpfulness rating."}],
    }


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.attributes = []
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)
        self.attributes.extend(attrs)
        self.elements.append((tag, dict(attrs)))


class PanelText(HTMLParser):
    """Read text from one report panel and its nested content."""

    def __init__(self, html, panel_id):
        super().__init__()
        self.panel_id = panel_id
        self.tag = None
        self.depth = 0
        self.parts = []
        self.feed(html)
        self.text = " ".join(" ".join(self.parts).split())

    def handle_starttag(self, tag, attrs):
        if dict(attrs).get("id") == self.panel_id:
            self.tag, self.depth = tag, 1
        elif self.depth and tag == self.tag:
            self.depth += 1

    def handle_endtag(self, tag):
        if self.depth and tag == self.tag:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.parts.append(data)


class ReportingTests(unittest.TestCase):
    def test_dataset_html_starts_in_simple_view_and_keeps_the_detailed_evidence(self):
        report = example_report()
        report["audit"] = example_audit()
        html = render_html(report, "sample.jsonl")
        parser = Tags()
        parser.feed(html)
        choices = [attrs for tag, attrs in parser.elements if tag == "input" and attrs.get("name") == "report-view"]
        self.assertEqual([(choice["id"], choice["type"], "checked" in choice) for choice in choices],
                         [("view-simple", "radio", True), ("view-detailed", "radio", False)])
        labels = {attrs.get("for") for tag, attrs in parser.elements if tag == "label"}
        self.assertTrue({"view-simple", "view-detailed"} <= labels)
        simple = PanelText(html, "simple-report").text
        detailed = PanelText(html, "detailed-report").text
        for text in ("Explain this result", "An answer to review", "Checks run"):
            self.assertIn(text, detailed)
            self.assertNotIn(text, simple)
        downloads = {attrs.get("href") for tag, attrs in parser.elements if tag == "a"}
        self.assertTrue({"findings.csv", "review-queue.csv", "report.json", "skipped-checks.csv"} <= downloads)

    def test_simple_view_counts_overlapping_thresholds_as_distinct_records(self):
        report = example_report()
        audit = report["audit"] = example_audit()
        audit["options"] = {"review_thresholds": {"helpfulness": 1, "correctness": 1}}
        helpfulness = {"kind": "low_rating", "field": "helpfulness"}
        correctness = {"kind": "low_rating", "field": "correctness"}
        audit.update(review_record_count=2, threshold_match_count=3, duplicate_group_count=0, conflict_group_count=0,
                     review_queue=[{"record": 1, "reasons": [helpfulness, correctness]},
                                   {"record": 2, "reasons": [helpfulness]}])
        simple = PanelText(render_html(report), "simple-report").text
        self.assertIn("2 records to review", simple)
        self.assertNotIn("3 records to review", simple)
        self.assertIn("helpfulness ≤ 1 · 2 records", simple)
        self.assertIn("correctness ≤ 1 · 1 record", simple)

    def test_simple_view_distinguishes_an_absent_audit_from_one_with_no_queue(self):
        report = example_report()
        simple = PanelText(render_html(report), "simple-report").text
        self.assertNotIn("No review flags", simple)
        self.assertNotIn("0 records to review", simple)
        audit = report["audit"] = {"review_queue": [], "review_record_count": 0}
        audit["check_status"] = [{"check": "review_thresholds", "status": "not_assessed"}]
        simple = PanelText(render_html(report), "simple-report").text
        self.assertIn("Audit checks not assessed", simple)
        self.assertNotIn("No review flags", simple)
        audit["check_status"][0]["status"] = "completed"
        self.assertIn("No review flags under selected rules", PanelText(render_html(report), "simple-report").text)
        audit["coverage"] = {"gaps": [{"value": "French", "count": 0, "minimum": 2}]}
        simple = PanelText(render_html(report), "simple-report").text
        self.assertIn("Review needed", simple)
        self.assertIn("French", simple)
        self.assertNotIn("No review flags", simple)
    def test_offline_reports_follow_system_and_served_copies_accept_known_themes(self):
        for render in (render_html, semantic_reporting.render_html):
            with self.subTest(renderer=render.__module__):
                offline = render({}).encode("utf-8")
                self.assertIn(b"prefers-color-scheme: dark", offline)
                for theme in ("light", "dark"):
                    viewed = with_report_theme(offline, theme)
                    parser = Tags()
                    parser.feed(viewed.decode("utf-8"))
                    self.assertIn(("data-theme", theme), parser.attributes)
                    self.assertNotIn("script", parser.tags)
                    self.assertIn(b'<html lang="en">', offline)
                for theme in ("", "system", 'dark" onload="alert(1)'):
                    self.assertEqual(offline, with_report_theme(offline, theme))

    def test_audit_review_is_separate_from_structural_success_and_preserves_context(self):
        report = example_report()
        report["audit"] = example_audit()
        report["audit"]["source"] = {"filename": "uploaded.json.gz", "sha256": "f" * 64, "app_version": "0.5.0", "inspected_at": "2026-11-22T10:00:00Z"}
        html, text = render_html(report), render_text(report)
        for content in (html, text):
            self.assertIn("Data validation: Passed", content)
            self.assertIn("Review needed", content)
            self.assertIn("HelpSteer2 preset", content)
            self.assertIn("1.000000000000000000001", content)
            self.assertIn("Explain this result", content)
            self.assertIn("An answer to review", content)
            self.assertIn("Original ratings", content)
            self.assertIn("Review the prompt and response against the rubric.", content)
            self.assertIn("annotator agreement: not assessed", content)
            self.assertIn("Exact duplicate groups: 1", content)
            self.assertIn("Conflicting-rating groups: 1", content)
            self.assertIn("not endorsements or fresh AI evaluations", content)
            self.assertIn("review-queue.csv", content)
            self.assertIn("uploaded.json.gz", content)
            self.assertIn("Original uploaded bytes SHA-256", content)
            self.assertIn("f" * 64, content)
            self.assertIn("2026-11-22T10:00:00Z", content)
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["finding_count"], 0)

    def test_missing_expected_category_requires_review_without_queued_records(self):
        report = example_report()
        audit = report["audit"] = example_audit()
        audit.update(review_queue=[], review_record_count=0)
        audit["coverage"] = {"field": "language", "minimum": 20, "missing_records": 1, "unsupported_records": 0, "groups": [], "gaps": [{"value": "French", "count": 0, "minimum": 20, "kind": "missing_expected"}]}
        for content in (render_html(report), render_text(report)):
            self.assertIn("Review needed", content)
            self.assertIn("French", content)
            self.assertIn("missing expected", content)
            self.assertIn("minimum 20", content.lower())

    def test_audit_untrusted_context_and_metadata_cannot_create_html(self):
        report = example_report()
        audit = report["audit"] = example_audit()
        attack = '<img src=x onerror="alert(1)">'
        audit["provenance"].update(rule_origin=attack, rating_evidence=attack, review_thresholds={attack: 1})
        audit["source"] = dict.fromkeys(("filename", "sha256", "app_version", "inspected_at"), attack)
        audit["check_status"] = [{"check": attack, "status": attack, "reason": attack}]
        audit["limitations"] = [attack]
        audit["review_queue"][0].update(prompt=attack, response=attack, ratings={attack: attack}, priority=attack, reasons=[{"reason": attack, "action": attack}])
        audit["positively_rated_examples"][0].update(prompt=attack, response=attack, selection_reason=attack)
        audit["coverage"] = {"field": attack, "minimum": 1, "groups": [{"value": attack, "count": 0, "below_minimum": True}], "gaps": [{"value": attack, "count": 0, "minimum": 1, "kind": attack}]}
        html = render_html(report)
        parser = Tags()
        parser.feed(html)
        self.assertNotIn("img", parser.tags)
        self.assertFalse(any(key.startswith("on") for key, _ in parser.attributes))
        self.assertIn("&lt;img", html)

    def test_audit_preview_is_limited_and_full_exports_are_signposted(self):
        report = example_report()
        audit = report["audit"] = example_audit()
        template = audit["review_queue"][0]
        audit["review_queue"] = [{**template, "record": i, "response": "x" * 1400} for i in range(1, 23)]
        audit["review_record_count"] = 22
        audit["positively_rated_examples"] = [{"record": i, "prompt": "Question", "response": "Answer", "ratings": {"helpfulness": 4}} for i in range(901, 905)]
        for content in (render_html(report), render_text(report)):
            self.assertIn("Showing 20 of 22", content)
            self.assertNotIn("Record 21", content)
            self.assertNotIn("Record 904", content)
            self.assertNotIn("x" * 1201, content)
            self.assertIn("1,200 characters", content)
            self.assertIn("report.json", content)

    def test_conflicting_ratings_show_exact_scores_and_bounded_record_membership(self):
        report = example_report()
        audit = report["audit"] = example_audit()
        field = '<score onerror="alert(1)">'
        audit["review_queue"][0]["reasons"] = [{"kind": "conflicting_rating", "field": field, "group_id": "conflict-1", "reason": "This exact pair has different source scores.", "action": "Compare the recorded scores and source rubric."}]
        audit["conflict_groups"] = [{"group_id": "conflict-1", "differences": [
            {"field": "unrelated", "values": [{"value": 999, "records": [500]}]},
            {"field": field, "values": [{"value": Decimal("1.000000000000000000001"), "records": list(range(100, 125))}, {"value": 4, "records": [200]}]},
        ]}]
        html, text = render_html(report), render_text(report)
        for content in (html, text):
            self.assertIn("Source score 1.000000000000000000001: records 100, 101", content)
            self.assertIn("(25 total)", content)
            self.assertIn("Showing the first 20; full membership is in report.json.", content)
            self.assertIn("Source score 4: records 200 (1 total).", content)
            self.assertNotIn("Source score 999", content)
            self.assertNotIn(", 120", content)
        parser = Tags()
        parser.feed(html)
        self.assertNotIn("score", parser.tags)
        self.assertFalse(any(key.startswith("on") for key, _ in parser.attributes))

    def test_category_labels_make_outer_whitespace_visible_without_normalizing(self):
        report = example_report()
        audit = report["audit"] = example_audit()
        audit["coverage"] = {"field": "category", "minimum": 2, "groups": [{"value": "  math ", "count": 1, "below_minimum": True}], "gaps": [{"value": "  math ", "count": 1, "minimum": 2, "kind": "below_minimum"}, {"value": "math", "count": 0, "minimum": 2, "kind": "missing_expected"}]}
        html, text = render_html(report), render_text(report)
        self.assertIn("<code class='value'>&quot;  math &quot;</code>", html)
        self.assertIn("<code class='value'>math</code>", html)
        self.assertIn('Coverage gap: "  math "; 1 records', text)
        self.assertIn("Coverage gap: math; 0 records", text)

    def test_review_reasons_and_positive_examples_identify_their_attributes(self):
        report = example_report()
        audit = report["audit"] = example_audit()
        audit["review_queue"][0]["reasons"] = [
            {"kind": "low_rating", "field": field, "reason": "Existing source score 1 meets the threshold.", "action": "Review this annotation."}
            for field in ("helpfulness", "correctness")
        ]
        audit["positively_rated_examples"] = [
            {"record": 3, "source_line": 4, "field": field, "value": value, "prompt": "Question", "response": "Answer", "ratings": {field: value}}
            for field, value in (("helpfulness", 4), ("correctness", Decimal("3.000000000000000000001")))
        ]
        for content in (render_html(report), render_text(report)):
            self.assertIn("Source rating meets review threshold · helpfulness", content)
            self.assertIn("Source rating meets review threshold · correctness", content)
            self.assertIn("Record 3 · line 4 · helpfulness: 4", content)
            self.assertIn("Record 3 · line 4 · correctness: 3.000000000000000000001", content)

    def test_clean_report_distinguishes_records_and_rating_values(self):
        report = example_report()
        text = render_text(report, "/tmp/validation.jsonl.gz")
        html = render_html(report, "/tmp/validation.jsonl.gz")
        self.assertIn("Records inspected: 3", text)
        self.assertIn("Rating values examined: 6", text)
        self.assertIn("Required fields: response.", text)
        self.assertEqual(text.count("Required fields: response."), 1)
        self.assertIn("mean 2.67", text)
        self.assertIn("4: 2 (66.7%)", text)
        self.assertIn("Higher complexity or verbosity is not inherently better", text)
        self.assertIn("does not establish", html)
        self.assertNotIn("/tmp/", html)
        self.assertIn("validation.jsonl.gz", html)

    def test_untrusted_content_cannot_create_elements_or_attributes(self):
        report = example_report()
        attack = '<img src=x onerror="alert(1)">'
        report["status"] = attack
        report["input_format"] = attack
        report["config"]["columns"]["response"] = attack
        report["rating_summaries"][0]["field"] = attack
        report["findings"] = [{"record": attack, "source_line": attack, "field": attack, "value": attack, "rule": attack, "reason": attack, "value_type": attack}]
        report["finding_count"] = 1
        html = render_html(report, attack + ".json")
        parser = Tags()
        parser.feed(html)
        self.assertNotIn("img", parser.tags)
        self.assertNotIn("script", parser.tags)
        self.assertNotIn("iframe", parser.tags)
        self.assertFalse(any(key.startswith("on") for key, _ in parser.attributes))
        self.assertIn("&lt;img", html)
        self.assertTrue(all(not value.startswith(("http:", "https:")) for key, value in parser.attributes if key in ("href", "src")))

    def test_previews_are_bounded_and_missing_fields_are_distinguished(self):
        report = example_report()
        report.update(status="issues_found", finding_count=22, affected_records=22)
        report["findings"] = [{"record": i, "source_line": None, "field": "response", "value": "x" * 400, "rule": "required", "reason": "Preview", "value_type": "string"} for i in range(1, 23)]
        report["findings"][0].update(value=None, field_present=False, value_type="missing")
        html = render_html(report)
        text = render_text(report)
        self.assertIn("Showing 20 of 22", html)
        self.assertIn("(field absent)", html)
        self.assertIn("shortened to 160 characters", html)
        self.assertNotIn("x" * 161, html)
        self.assertNotIn("Record 21", html)
        self.assertIn("Showing 20 of 22", text)
        self.assertIn("findings.csv", text)

    def test_zero_valid_values_and_empty_states_never_show_fake_statistics(self):
        for status in ("empty_dataset", "no_checks_selected", "incomplete"):
            report = example_report()
            report["status"] = status
            for item in report["rating_summaries"]:
                item["statistics"] = {"count": 0, "minimum": None, "maximum": None, "mean": None}
                item["counts"]["valid"] = 0
            html = render_html(report)
            text = render_text(report)
            self.assertIn("No valid numeric scores", html)
            self.assertIn("mean —", text)
            self.assertNotIn("All selected structural checks passed", html)

    def test_legacy_report_without_attribute_summaries_is_readable(self):
        report = example_report()
        del report["rating_summaries"]
        report["config"] = {"columns": {"rating": "score"}, "required": ["rating"], "numeric_rating": True, "rating_range": [1, 5], "unique_id": False}
        text = render_text(report)
        html = render_html(report)
        self.assertIn("score: required, finite number, 1–5 inclusive", text)
        self.assertIn("Attribute statistics are not available", text)
        self.assertIn("score:", html)
        self.assertNotIn("Rating values examined", text)

    def test_extreme_numbers_surrogates_and_optional_mean_note_remain_safe(self):
        report = example_report()
        summary = report["rating_summaries"][0]
        summary["statistics"] = {"count": 1, "minimum": Decimal("1e1000000"), "maximum": Decimal("1e1000000"), "mean": None, "mean_note": "Mean unavailable at supported reporting precision."}
        summary["distribution"] = [{"value": Decimal("1e1000000"), "count": 1}]
        report["findings"] = [{"record": 1, "field": "rating", "value": "\ud800", "rule": "numeric", "reason": "Expected a number.", "field_present": True, "value_type": "string"}]
        report["finding_count"] = 1
        html = render_html(report, "\ud800.json")
        text = render_text(report, "\ud800.json")
        html.encode("utf-8")
        text.encode("utf-8")
        self.assertIn("1.000E+1000000", html)
        self.assertIn("Mean unavailable at supported reporting precision.", html)
        self.assertIn("Mean unavailable at supported reporting precision.", text)
        self.assertIn("\\ud800", html)
        self.assertLess(len(html), 30000)

    def test_zero_with_extreme_exponent_stays_compact(self):
        report = example_report()
        summary = report["rating_summaries"][0]
        zero = Decimal("0E-10000")
        summary["statistics"] = {"count": 1, "minimum": zero, "maximum": zero, "mean": zero}
        summary["distribution"] = [{"value": zero, "count": 1}]
        html = render_html(report)
        text = render_text(report)
        self.assertIn("min 0; max 0; mean 0.00", text)
        self.assertIn("0: 1 (100.0%)", text)
        self.assertLess(len(html), 25000)
        self.assertLess(len(text), 6000)
        self.assertNotIn("Counts describe rule failures", html)
        self.assertIn("counts without findings", html)


if __name__ == "__main__":
    unittest.main()
