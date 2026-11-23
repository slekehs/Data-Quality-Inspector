"""Offline audit observations preserve evidence without redefining validation."""

from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dqi.audit import add_audit
from dqi.core import Config, ConfigError, DatasetError, inspect, load_dataset
from dqi.json_utils import dumps_json
from dqi.presets import HELPSTEER2


def record(prompt="Question", response="Answer", **overrides):
    return {"prompt": prompt, "response": response, "helpfulness": 4, "correctness": 4,
            "coherence": 4, "complexity": 2, "verbosity": 2, **overrides}


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.source = Path(self.temporary.name) / "source.json"

    def run_audit(self, rows, options=None, rules=None):
        self.source.write_text(dumps_json(rows), encoding="utf-8")
        original = self.source.read_bytes()
        dataset = load_dataset(self.source)
        result = inspect(dataset, Config.from_dict(rules or HELPSTEER2))
        audited = add_audit(dataset, result, options or {})
        self.assertEqual(self.source.read_bytes(), original)
        self.assertIsNone(result.audit)
        self.assertNotIn("audit", result.to_dict())
        self.assertEqual(audited.findings, result.findings)
        self.assertEqual(audited.status, result.status)
        self.assertEqual(audited.affected_records, result.affected_records)
        return audited

    def test_no_implicit_thresholds_and_audit_does_not_change_pass_status(self):
        result = self.run_audit([record(helpfulness=0, correctness=0, coherence=0, complexity=0, verbosity=0)])
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.audit["review_record_count"], 0)
        self.assertEqual(result.audit["positively_rated_examples"], [])
        states = {item["check"]: item["status"] for item in result.audit["check_status"]}
        self.assertEqual(states["review_thresholds"], "not_selected")
        self.assertEqual(states["annotator_agreement"], "not_assessed")
        self.assertEqual(states["split_leakage"], "not_assessed")

    def test_exact_pairs_flag_all_copies_but_not_repeated_prompts_alone(self):
        result = self.run_audit([
            record(evaluation_id="first"), record(response="Different answer", evaluation_id="first"),
            record(evaluation_id="different ID"), record(prompt=" Question"), record(prompt="question"),
        ], {"prompt_field": "prompt", "duplicates": True, "conflicts": True})
        audit = result.audit
        self.assertEqual(audit["duplicate_groups"], [{"group_id": "duplicate-1", "records": [1, 3], "record_count": 2}])
        self.assertEqual(audit["conflict_groups"], [])
        self.assertEqual(audit["review_record_count"], 2)
        self.assertEqual([item["record"] for item in audit["review_queue"]], [1, 3])
        self.assertEqual(audit["review_queue"][1]["evaluation_id"], "different ID")
        self.assertTrue(all(item["priority"] == "medium" for item in audit["review_queue"]))

    def test_conflicts_use_valid_scores_and_merge_with_core_findings(self):
        result = self.run_audit([
            record(helpfulness=0), record(helpfulness=4), record(helpfulness="bad"),
        ], {"prompt_field": "prompt", "duplicates": True, "conflicts": True, "review_thresholds": {"helpfulness": 1}})
        audit = result.audit
        self.assertEqual(audit["duplicate_record_count"], 3)
        self.assertEqual(audit["conflict_record_count"], 2)
        conflict = audit["conflict_groups"][0]
        self.assertEqual(conflict["records"], [1, 2])
        self.assertEqual(conflict["pair_records"], [1, 2, 3])
        self.assertEqual(conflict["differences"], [{"field": "helpfulness", "values": [{"value": Decimal(0), "records": [1]}, {"value": Decimal(4), "records": [2]}]}])
        queue = {item["record"]: item for item in audit["review_queue"]}
        self.assertEqual(set(queue), {1, 2, 3})
        self.assertEqual(audit["review_queue"][0]["record"], 3)
        self.assertEqual(queue[3]["priority"], "high")
        self.assertEqual({reason["kind"] for reason in queue[1]["reasons"]}, {"duplicate", "conflicting_rating", "low_rating"})
        self.assertNotIn("conflicting_rating", {reason["kind"] for reason in queue[3]["reasons"]})
        self.assertNotIn("low_rating", {reason["kind"] for reason in queue[3]["reasons"]})
        self.assertIn("skipped_check", {reason["kind"] for reason in queue[3]["reasons"]})

    def test_thresholds_are_inclusive_explicit_and_deduplicate_review_records(self):
        result = self.run_audit([
            record(prompt="One", helpfulness=1, correctness=0, coherence=4, complexity=0, verbosity=0),
            record(prompt="Two", helpfulness=0, correctness=4, coherence=1),
            record(prompt="Three", helpfulness=4, correctness=4, coherence=4, complexity=0, verbosity=0),
        ], {"prompt_field": "prompt", "review_thresholds": {"helpfulness": 1, "correctness": 1, "coherence": 1}, "rule_origin": "helpsteer2"})
        audit = result.audit
        self.assertEqual(result.status, "passed")
        self.assertEqual(audit["threshold_match_count"], 4)
        self.assertEqual(audit["review_record_count"], 2)
        self.assertEqual([item["record"] for item in audit["review_queue"]], [1, 2])
        self.assertTrue(all(reason["field"] not in {"verbosity", "complexity"} for item in audit["review_queue"] for reason in item["reasons"]))
        self.assertEqual(audit["provenance"]["rule_origin"], "helpsteer2")
        self.assertIn("heuristics", audit["provenance"]["review_thresholds"])
        check = next(item for item in audit["check_status"] if item["check"] == "review_thresholds")
        self.assertEqual((check["eligible_values"], check["skipped_values"], check["matched_values"]), (9, 0, 4))

    def test_invalid_values_never_get_threshold_quality_flags(self):
        result = self.run_audit([
            record(prompt="One", helpfulness=9), record(prompt="Two", helpfulness=True),
            record(prompt="Three", helpfulness=None), record(prompt="Four", helpfulness=Decimal("0.5")),
            record(prompt="Five", helpfulness=0),
        ], {"review_thresholds": {"helpfulness": 10}})
        low = [item["record"] for item in result.audit["review_queue"] if any(reason["kind"] == "low_rating" for reason in item["reasons"])]
        self.assertEqual(low, [5])
        check = next(item for item in result.audit["check_status"] if item["check"] == "review_thresholds")
        self.assertEqual((check["eligible_values"], check["skipped_values"]), (1, 4))

    def test_thresholds_preserve_exact_decimals(self):
        rules = deepcopy(HELPSTEER2)
        rules["ratings"]["helpfulness"]["integer"] = False
        result = self.run_audit([
            record(prompt="Above", helpfulness=Decimal("0.10000000000000000001")),
            record(prompt="At", helpfulness=Decimal("0.1")),
        ], {"review_thresholds": {"helpfulness": Decimal("0.1")}}, rules)
        self.assertEqual([item["record"] for item in result.audit["review_queue"]], [2])
        self.assertEqual(result.audit["options"]["review_thresholds"]["helpfulness"], Decimal("0.1"))

    def test_prompt_mapping_can_be_absent_without_disabling_other_audits(self):
        result = self.run_audit([record(helpfulness=0)], {"prompt_field": None, "duplicates": True, "conflicts": True, "review_thresholds": {"helpfulness": 1}})
        checks = {item["check"]: item for item in result.audit["check_status"]}
        self.assertEqual(checks["exact_duplicates"]["status"], "not_assessed")
        self.assertIn("not mapped", checks["rating_conflicts"]["reason"])
        self.assertEqual(checks["review_thresholds"]["status"], "completed")
        self.assertEqual(result.audit["review_record_count"], 1)
        self.assertIsNone(result.audit["review_queue"][0]["prompt"])

    def test_non_string_and_blank_pair_context_is_explicitly_excluded(self):
        result = self.run_audit([
            record(prompt=""), record(prompt=["nested"]), record(prompt=None),
            record(prompt="Valid"), record(prompt="Valid"),
        ], {"prompt_field": "prompt", "duplicates": True, "conflicts": True})
        check = next(item for item in result.audit["check_status"] if item["check"] == "exact_duplicates")
        self.assertEqual((check["eligible_records"], check["skipped_records"]), (2, 3))
        self.assertEqual(result.audit["duplicate_groups"][0]["records"], [4, 5])

    def test_coverage_reports_actual_small_groups_and_missing_expected_groups(self):
        values = ["en", "en", "fr", "", None, 1, " fr"]
        result = self.run_audit([record(prompt=str(i), language=value) for i, value in enumerate(values)], {
            "coverage": {"field": "language", "minimum": 2, "expected_values": ["en", "fr", "de"]},
        })
        coverage = result.audit["coverage"]
        self.assertEqual((coverage["eligible_records"], coverage["missing_records"], coverage["unsupported_records"]), (4, 2, 1))
        self.assertEqual(coverage["missing_expected_values"], ["de"])
        self.assertEqual({(gap["value"], gap["count"], gap["kind"]) for gap in coverage["gaps"]}, {("fr", 1, "below_minimum"), (" fr", 1, "below_minimum"), ("de", 0, "missing_expected")})
        self.assertEqual(result.audit["review_record_count"], 0)
        check = next(item for item in result.audit["check_status"] if item["check"] == "category_coverage")
        self.assertIn("do not establish statistical representativeness", check["reason"])

    def test_existing_score_examples_have_original_context_and_explicit_selection_basis(self):
        text = '<script>alert("source content")</script>'
        result = self.run_audit([
            record(prompt=text, response="First high source score", helpfulness=4),
            record(prompt="Second", response="Tied high score", helpfulness=4),
            record(prompt="Third", helpfulness=0),
        ], {"prompt_field": "prompt", "review_thresholds": {"helpfulness": 1, "complexity": 1}})
        examples = result.audit["positively_rated_examples"]
        self.assertEqual(len(examples), 2)
        first = next(example for example in examples if example["field"] == "helpfulness")
        self.assertEqual(first["record"], 1)
        self.assertEqual(first["prompt"], text)
        self.assertEqual(first["value"], Decimal(4))
        self.assertIn("not an independent assessment", first["selection_reason"])

    def test_source_score_examples_require_a_value_above_the_review_threshold(self):
        result = self.run_audit([
            record(prompt="Low", helpfulness=0), record(prompt="At threshold", helpfulness=1),
        ], {"review_thresholds": {"helpfulness": 1}})
        self.assertEqual(result.audit["positively_rated_examples"], [])
        self.assertEqual(result.audit["review_record_count"], 2)

    def test_large_group_reasons_are_bounded_with_complete_group_traceability(self):
        result = self.run_audit([
            record(helpfulness=i % 2) for i in range(200)
        ], {"prompt_field": "prompt", "duplicates": True, "conflicts": True})
        audit = result.audit
        self.assertEqual(audit["duplicate_groups"][0]["records"], list(range(1, 201)))
        self.assertEqual(audit["conflict_groups"][0]["records"], list(range(1, 201)))
        for item in audit["review_queue"]:
            for reason in item["reasons"]:
                self.assertEqual(reason["related_records"], list(range(1, 21)))
                self.assertEqual(reason["related_record_count"], 200)
                self.assertTrue(reason["related_records_truncated"])
                if reason["kind"] == "duplicate":
                    self.assertEqual(reason["observed"], {"matching_record_count": 200})
                    self.assertEqual(reason["group_id"], "duplicate-1")
                else:
                    self.assertEqual(reason["group_id"], "conflict-1")
        self.assertLess(len(dumps_json(audit)), 500000)

    def test_invalid_options_and_unknown_fields_are_rejected(self):
        self.source.write_text(dumps_json([record(language="en")]), encoding="utf-8")
        dataset = load_dataset(self.source)
        result = inspect(dataset, Config.from_dict(HELPSTEER2))
        invalid = [
            [], {"unknown": True}, {"duplicates": "true"}, {"conflicts": 1}, {"prompt_field": ""},
            {"response_field": None}, {"rule_origin": "made_up"}, {"review_thresholds": []},
            {"review_thresholds": {"absent": 1}}, {"review_thresholds": {"helpfulness": True}},
            {"review_thresholds": {"helpfulness": "1"}}, {"review_thresholds": {"helpfulness": Decimal("NaN")}},
            {"coverage": {"field": "language", "minimum": 0}}, {"coverage": {"field": "language", "minimum": True}},
            {"coverage": {"field": "language", "expected_values": [1]}}, {"coverage": {"field": "language", "expected_values": ["en", "en"]}},
            {"coverage": {"field": "language", "extra": 1}},
        ]
        for options in invalid:
            with self.subTest(options=options), self.assertRaises(ConfigError):
                add_audit(dataset, result, options)
        for options in ({"response_field": "missing"}, {"prompt_field": "missing"}, {"coverage": {"field": "missing"}}):
            with self.subTest(options=options), self.assertRaises(DatasetError):
                add_audit(dataset, result, options)
        with self.assertRaises(DatasetError):
            add_audit(dataset, replace(result, total_records=2), {})

    def test_empty_dataset_keeps_empty_status_and_reports_expected_coverage_absence(self):
        result = self.run_audit([], {"prompt_field": "prompt", "duplicates": True, "review_thresholds": {"helpfulness": 1}, "coverage": {"field": "language", "expected_values": ["en"], "minimum": 1}})
        self.assertEqual(result.status, "empty_dataset")
        self.assertEqual(result.audit["review_queue"], [])
        self.assertEqual(result.audit["coverage"]["missing_expected_values"], ["en"])
        self.assertEqual(result.audit["positively_rated_examples"], [])


if __name__ == "__main__":
    unittest.main()
