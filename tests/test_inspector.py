"""Acceptance tests for the first CSV inspection milestone."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dqi.core import Config, ConfigError, DatasetError, inspect, load_csv


class InspectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "evaluations.csv"

    def dataset(self, content):
        if isinstance(content, bytes):
            self.path.write_bytes(content)
        else:
            self.path.write_text(content, encoding="utf-8", newline="")
        return load_csv(self.path)

    def run_inspection(self, content, config=None):
        data = self.dataset(content)
        return inspect(data, config if config is not None else Config())

    def test_valid_data_accepts_boundaries_decimals_and_absent_optional_column(self):
        result = self.run_inspection(
            "evaluation_id,response,rating\n"
            "A1,First answer,1\n"
            "A2,Second answer,5\n"
            "A3,Third answer,2.5\n"
        )
        self.assertEqual(result.total_records, 3)
        self.assertEqual(result.affected_records, 0)
        self.assertEqual(list(result.findings), [])
        self.assertEqual(list(result.skipped_checks), [])
        self.assertEqual(result.status, "passed")

    def test_header_only_csv_is_empty_instead_of_claiming_success(self):
        result = self.run_inspection("evaluation_id,response,rating\n")
        self.assertEqual(result.total_records, 0)
        self.assertEqual(list(result.findings), [])
        self.assertEqual(result.status, "empty_dataset")

    def test_sample_reports_every_duplicate_record_without_hiding_other_issues(self):
        content = (
            "evaluation_id,response,rating\n"
            "A01,The total is 42.,4\n"
            "A02,,3\n"
            "A03,See attached.,9\n"
            "A01,The total is 43.,4\n"
            "A04,Approved.,good\n"
            "A05,Paris.,5\n"
        )
        result = self.run_inspection(content)
        self.assertEqual(result.total_records, 6)
        self.assertEqual(result.affected_records, 5)
        self.assertEqual(len(result.findings), 5)
        self.assertEqual(result.status, "issues_found")
        self.assertEqual(
            {(f.record, f.field, f.rule) for f in result.findings},
            {
                (1, "evaluation_id", "unique"),
                (2, "response", "required"),
                (3, "rating", "range"),
                (4, "evaluation_id", "unique"),
                (5, "rating", "numeric"),
            },
        )
        duplicates = [f for f in result.findings if f.rule == "unique"]
        self.assertTrue(all(tuple(f.related_records) == (1, 4) for f in duplicates))
        self.assertEqual(len(result.skipped_checks), 1)
        self.assertEqual(self.path.read_text(encoding="utf-8"), content)

    def test_blank_values_only_produce_missing_findings_and_preserve_original_value(self):
        result = self.run_inspection(
            "evaluation_id,response,rating\n"
            "A1,   , \n"
        )
        self.assertEqual(result.affected_records, 1)
        self.assertEqual(len(result.findings), 2)
        self.assertEqual({f.rule for f in result.findings}, {"required"})
        self.assertEqual(
            {f.field: f.value for f in result.findings},
            {"response": "   ", "rating": " "},
        )
        self.assertEqual(list(result.skipped_checks), [])

    def test_empty_identifiers_are_missing_values_not_duplicate_ids(self):
        result = self.run_inspection(
            "evaluation_id,response,rating\n"
            ",Answer one,3\n"
            ",Answer two,4\n"
        )
        self.assertEqual(len(result.findings), 2)
        self.assertTrue(all(f.rule == "required" for f in result.findings))

    def test_id_uniqueness_uses_original_values_without_silent_normalization(self):
        result = self.run_inspection(
            "evaluation_id,response,rating\n"
            "A1,Answer one,3\n"
            " A1,Answer two,4\n"
        )
        self.assertEqual(result.status, "passed")

    def test_custom_columns_and_rating_range_work_without_rewriting_source(self):
        config = Config.from_dict(
            {
                "columns": {
                    "evaluation_id": "review_key",
                    "response": "answer",
                    "rating": "score",
                    "explanation": "notes",
                },
                "required": ["evaluation_id", "response", "rating", "explanation"],
                "rating_range": [0, 10],
            }
        )
        result = self.run_inspection(
            "review_key,answer,score,notes\n"
            "A1,Some answer,10,Looks correct\n"
            "A2,Other answer,11,\n",
            config,
        )
        self.assertEqual(result.total_records, 2)
        self.assertEqual(result.affected_records, 1)
        self.assertEqual(
            {(f.record, f.field, f.rule) for f in result.findings},
            {(2, "score", "range"), (2, "notes", "required")},
        )

    def test_serialized_configuration_round_trip_preserves_mapping_and_bounds(self):
        config = Config.from_dict(
            {
                "columns": {"rating": "score", "response": "answer"},
                "required": ["response", "rating"],
                "unique_id": False,
                "rating_range": [-0.25, 9.75],
            }
        )
        restored = Config.from_dict(config.to_dict())
        self.assertEqual(restored.columns, config.columns)
        self.assertEqual(restored.required, config.required)
        self.assertEqual(restored.rating_range, config.rating_range)
        self.assertEqual(restored.unique_id, config.unique_id)
        self.assertEqual(restored.numeric_rating, config.numeric_rating)

    def test_missing_active_column_stops_inspection(self):
        data = self.dataset("evaluation_id,response\nA1,Answer\n")
        with self.assertRaises(DatasetError):
            inspect(data, Config())

    def test_optional_explanation_column_becomes_required_when_selected(self):
        config = Config.from_dict(
            {"required": ["evaluation_id", "response", "rating", "explanation"]}
        )
        data = self.dataset("evaluation_id,response,rating\nA1,Answer,4\n")
        with self.assertRaises(DatasetError):
            inspect(data, config)

    def test_nonfinite_values_are_not_accepted_as_numeric_ratings(self):
        result = self.run_inspection(
            "evaluation_id,response,rating\n"
            "A1,Answer,NaN\n"
            "A2,Answer,Infinity\n"
            "A3,Answer,-Infinity\n"
        )
        self.assertEqual(len(result.findings), 3)
        self.assertTrue(all(f.rule == "numeric" for f in result.findings))
        self.assertEqual(len(result.skipped_checks), 3)
        self.assertEqual(result.status, "issues_found")

    def test_numeric_grammar_rejects_underscores_but_accepts_decimal_and_exponent(self):
        config = Config.from_dict({"rating_range": [0, 5]})
        result = self.run_inspection(
            "evaluation_id,response,rating\n"
            "A1,Answer,1_._5\n"
            "A2,Answer,4e0\n"
            "A3,Answer,.5\n",
            config,
        )
        self.assertEqual(
            [(finding.record, finding.rule) for finding in result.findings],
            [(1, "numeric")],
        )
        self.assertEqual(len(result.skipped_checks), 1)

    def test_range_cannot_report_pass_when_nonnumeric_value_is_unchecked(self):
        config = Config.from_dict({"numeric_rating": False})
        result = self.run_inspection(
            "evaluation_id,response,rating\nA1,Answer,excellent\n", config
        )
        self.assertEqual(list(result.findings), [])
        self.assertEqual(len(result.skipped_checks), 1)
        self.assertEqual(result.status, "incomplete")

    def test_disabled_rules_do_not_require_unrelated_columns_or_claim_pass(self):
        config = Config.from_dict(
            {
                "required": [],
                "unique_id": False,
                "numeric_rating": False,
                "rating_range": None,
            }
        )
        result = self.run_inspection("unrelated\nhello\n", config)
        self.assertEqual(result.total_records, 1)
        self.assertEqual(list(result.findings), [])
        self.assertEqual(list(result.skipped_checks), [])
        self.assertEqual(result.status, "no_checks_selected")

    def test_multiline_fields_keep_logical_record_and_physical_line_distinct(self):
        result = self.run_inspection(
            'evaluation_id,response,rating\n'
            '\n'
            'A1,"First line\nSecond line",bad\n'
            'A2,,3\n'
        )
        self.assertEqual(result.total_records, 2)
        self.assertEqual(
            {(f.record, f.source_line, f.rule) for f in result.findings},
            {(1, 3, "numeric"), (2, 5, "required")},
        )

    def test_blank_csv_lines_are_skipped_but_empty_records_are_checked(self):
        result = self.run_inspection(
            "evaluation_id,response,rating\n\n,,\n\nA1,Answer,4\n"
        )
        self.assertEqual(result.total_records, 2)
        self.assertEqual(result.affected_records, 1)
        self.assertEqual(len(result.findings), 3)
        self.assertTrue(all(f.record == 1 and f.source_line == 3 for f in result.findings))

    def test_utf8_bom_is_supported(self):
        result = inspect(
            self.dataset(b"\xef\xbb\xbfevaluation_id,response,rating\nA1,Answer,4\n"),
            Config(),
        )
        self.assertEqual(result.status, "passed")

    def test_invalid_csv_is_rejected_before_any_rows_are_inspected(self):
        fixtures = {
            "duplicate headers": "evaluation_id,response,rating,rating\nA1,Answer,4,5\n",
            "blank header": "evaluation_id,,rating\nA1,Answer,4\n",
            "whitespace header": "evaluation_id,   ,rating\nA1,Answer,4\n",
            "extra value": "evaluation_id,response,rating\nA1,Answer,4,extra\n",
            "missing value": "evaluation_id,response,rating\nA1,Answer\n",
            "unclosed quote": 'evaluation_id,response,rating\nA1,"Unclosed,4\n',
            "invalid UTF-8": b"evaluation_id,response,rating\nA1,\xff,4\n",
        }
        for label, content in fixtures.items():
            with self.subTest(case=label):
                with self.assertRaises(DatasetError):
                    self.dataset(content)

    def test_invalid_configuration_is_rejected_instead_of_silently_ignored(self):
        fixtures = {
            "unknown option": {"numeric_ratings": True},
            "unknown semantic field": {"columns": {"ratng": "score"}},
            "unknown required field": {"required": ["ratng"]},
            "empty column": {"columns": {"rating": ""}},
            "ambiguous active mapping": {"columns": {"rating": "response"}},
            "boolean provided as text": {"unique_id": "false"},
            "reversed range": {"rating_range": [5, 1]},
            "infinite range": {"rating_range": [1, float("inf")]},
            "invalid range length": {"rating_range": [1]},
        }
        for label, content in fixtures.items():
            with self.subTest(case=label):
                with self.assertRaises(ConfigError):
                    Config.from_dict(content)


if __name__ == "__main__":
    unittest.main()
