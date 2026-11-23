"""Acceptance coverage for multiple score attributes and their summaries."""

from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dqi.__main__ import export, main
from dqi.core import Config, ConfigError, DatasetError, inspect, load_dataset
from dqi.json_utils import dumps_json


ATTRIBUTES = ("helpfulness", "correctness", "coherence", "complexity", "verbosity")


class MultiRatingTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def write_rows(self, rows, name="evaluations.json"):
        path = self.directory / name
        path.write_text(dumps_json(rows), encoding="utf-8")
        return path

    def helpsteer_config(self):
        return Config.from_dict(
            {
                "required": ["response"],
                "unique_id": False,
                "ratings": {
                    field: {"integer": True, "range": [0, 4]}
                    for field in ATTRIBUTES
                },
            }
        )

    def score_config(self, rules):
        return Config.from_dict({"required": ["response"], "unique_id": False, "ratings": {"score": rules}})

    def run_main(self, *arguments):
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            try:
                code = main([str(argument) for argument in arguments])
            except SystemExit as exc:
                code = exc.code
        return code, stdout.getvalue(), stderr.getvalue()

    def test_named_ratings_replace_the_legacy_score_and_keep_common_defaults(self):
        config = Config.from_dict({"ratings": {"helpfulness": {}}})
        source = self.write_rows([{"evaluation_id": "A1", "response": "Answer", "helpfulness": 4}])
        result = inspect(load_dataset(source), config)
        self.assertEqual(result.status, "passed")
        self.assertEqual([summary["field"] for summary in result.rating_summaries], ["helpfulness"])
        rules = result.rating_summaries[0]["rules"]
        self.assertEqual(rules, {"required": True, "numeric": True, "integer": False, "range": [Decimal(1), Decimal(5)]})
        missing_id = self.write_rows([{"response": "Answer", "helpfulness": 4}], "missing-id.json")
        with self.assertRaisesRegex(DatasetError, "evaluation_id"):
            inspect(load_dataset(missing_id), config)

    def test_five_scores_produce_independent_findings_and_union_affected_records(self):
        rows = [
            {"response": "First", **{field: 4 for field in ATTRIBUTES}},
            {"response": "Second", "helpfulness": None, "correctness": "oops", "coherence": Decimal("2.5"), "complexity": 5, "verbosity": Decimal("4.5")},
            {"response": "", **{field: 0 for field in ATTRIBUTES}},
            {"response": "Fourth", **{field: 2 for field in ATTRIBUTES}},
        ]
        result = inspect(load_dataset(self.write_rows(rows)), self.helpsteer_config())
        self.assertEqual(result.total_records, 4)
        self.assertEqual(result.affected_records, 2)
        self.assertEqual(len(result.findings), 7)
        self.assertEqual(len(result.skipped_checks), 2)
        self.assertEqual(
            {(f.record, f.field, f.rule) for f in result.findings},
            {
                (2, "helpfulness", "required"),
                (2, "correctness", "numeric"),
                (2, "coherence", "integer"),
                (2, "complexity", "range"),
                (2, "verbosity", "integer"),
                (2, "verbosity", "range"),
                (3, "response", "required"),
            },
        )
        summaries = {summary["field"]: summary for summary in result.rating_summaries}
        self.assertEqual(set(summaries), set(ATTRIBUTES))
        for field, summary in summaries.items():
            with self.subTest(field=field):
                counts = summary["counts"]
                self.assertEqual(counts["total"], 4)
                self.assertEqual(counts["valid"], 3)
                self.assertEqual(counts["total"] - counts["valid"], 1)
                self.assertEqual(summary["statistics"], {"count": 3, "minimum": Decimal(0), "maximum": Decimal(4), "mean": Decimal(2)})
                self.assertEqual(summary["distribution"], [{"value": Decimal(value), "count": 1 if value in (0, 2, 4) else 0} for value in range(5)])
        self.assertEqual(summaries["helpfulness"]["counts"]["missing"], 1)
        self.assertEqual(summaries["correctness"]["counts"]["invalid_numeric"], 1)
        self.assertEqual(summaries["correctness"]["counts"]["skipped_checks"], 2)
        self.assertEqual(summaries["coherence"]["counts"]["non_integer"], 1)
        self.assertEqual(summaries["complexity"]["counts"]["out_of_range"], 1)
        self.assertEqual(summaries["verbosity"]["counts"]["non_integer"], 1)
        self.assertEqual(summaries["verbosity"]["counts"]["out_of_range"], 1)
        self.assertEqual(summaries["verbosity"]["counts"]["findings"], 2)
        self.assertEqual(result.to_dict()["rating_summaries"], list(result.rating_summaries))

    def test_common_response_failure_is_once_and_does_not_discard_valid_scores(self):
        row = {"response": None, **{field: 3 for field in ATTRIBUTES}}
        result = inspect(load_dataset(self.write_rows([row])), self.helpsteer_config())
        self.assertEqual([(f.field, f.rule) for f in result.findings], [("response", "required")])
        self.assertEqual(result.affected_records, 1)
        self.assertEqual(sum(summary["counts"]["valid"] for summary in result.rating_summaries), 5)
        self.assertTrue(all(summary["statistics"]["mean"] == Decimal(3) for summary in result.rating_summaries))

    def test_missing_scores_generate_required_only_and_retain_presence_evidence(self):
        rows = [
            {"response": "Null", "score": None},
            {"response": "Blank", "score": "  "},
            {"response": "Absent"},
            {"response": "Zero", "score": 0},
        ]
        config = self.score_config({"integer": True, "range": [0, 4]})
        result = inspect(load_dataset(self.write_rows(rows)), config)
        self.assertEqual([(f.record, f.rule) for f in result.findings], [(1, "required"), (2, "required"), (3, "required")])
        self.assertEqual([f.field_present for f in result.findings], [True, True, False])
        self.assertEqual(list(result.skipped_checks), [])
        counts = result.rating_summaries[0]["counts"]
        self.assertEqual(counts["missing"], 3)
        self.assertEqual(counts["valid"], 1)

    def test_disabled_numeric_finding_still_skips_integer_and_range(self):
        config = self.score_config({"numeric": False, "integer": True, "range": [0, 4]})
        result = inspect(load_dataset(self.write_rows([{"response": "Answer", "score": "oops"}])), config)
        self.assertEqual(list(result.findings), [])
        self.assertEqual({check.rule for check in result.skipped_checks}, {"integer", "range"})
        self.assertEqual(result.status, "incomplete")
        summary = result.rating_summaries[0]
        self.assertEqual(summary["counts"]["invalid_numeric"], 1)
        self.assertEqual(summary["counts"]["valid"], 0)
        self.assertEqual(summary["counts"]["skipped_checks"], 2)
        self.assertEqual(summary["statistics"], {"count": 0, "minimum": None, "maximum": None, "mean": None})

    def test_optional_missing_and_invalid_numeric_values_are_observed_not_valid(self):
        config = self.score_config({"required": False, "numeric": False, "integer": False, "range": [0, 4]})
        rows = [{"response": "Missing", "score": None}, {"response": "Bad", "score": "oops"}, {"response": "Good", "score": 4}]
        result = inspect(load_dataset(self.write_rows(rows)), config)
        self.assertEqual(list(result.findings), [])
        counts = result.rating_summaries[0]["counts"]
        self.assertEqual((counts["missing"], counts["invalid_numeric"], counts["valid"]), (1, 1, 1))
        self.assertEqual(result.rating_summaries[0]["statistics"]["count"], 1)

    def test_decimal_precision_is_preserved_in_bounds_statistics_and_findings(self):
        exact_valid = Decimal("4.000000000000000000001")
        exact_invalid = Decimal("5.000000000000000000001")
        rows = [{"response": "Valid", "score": exact_valid}, {"response": "Invalid", "score": exact_invalid}]
        result = inspect(load_dataset(self.write_rows(rows)), self.score_config({"range": [0, 5]}))
        self.assertEqual([(f.record, f.rule) for f in result.findings], [(2, "range")])
        self.assertEqual(result.findings[0].value, exact_invalid)
        summary = result.rating_summaries[0]
        self.assertEqual(summary["statistics"]["minimum"], exact_valid)
        self.assertEqual(summary["statistics"]["maximum"], exact_valid)
        self.assertEqual(summary["statistics"]["mean"], Decimal("4.000000"))
        self.assertEqual(summary["distribution"], [{"value": exact_valid, "count": 1}])

    def test_means_round_six_decimals_half_up_for_positive_and_negative_values(self):
        for value, expected in (("1.0000005", "1.000001"), ("-1.0000005", "-1.000001")):
            with self.subTest(value=value):
                source = self.write_rows([{"response": "Answer", "score": Decimal(value)}])
                result = inspect(load_dataset(source), self.score_config({"range": None}))
                self.assertEqual(result.rating_summaries[0]["statistics"]["mean"], Decimal(expected))

    def test_small_integer_ranges_include_zero_bins_without_expanding_large_ranges(self):
        for upper, expected_length in ((20, 21), (21, 1)):
            with self.subTest(upper=upper):
                source = self.write_rows([{"response": "Answer", "score": 10}])
                result = inspect(load_dataset(source), self.score_config({"integer": True, "range": [0, upper]}))
                distribution = result.rating_summaries[0]["distribution"]
                self.assertEqual(len(distribution), expected_length)
                self.assertEqual(sum(bin_["count"] for bin_ in distribution), 1)
                self.assertEqual([bin_["value"] for bin_ in distribution if bin_["count"]], [Decimal(10)])

    def test_empty_data_has_zero_counts_and_absent_statistics_for_each_attribute(self):
        result = inspect(load_dataset(self.write_rows([])), self.helpsteer_config())
        self.assertEqual(result.status, "empty_dataset")
        self.assertEqual(len(result.rating_summaries), 5)
        for summary in result.rating_summaries:
            self.assertTrue(all(count == 0 for count in summary["counts"].values()))
            self.assertEqual(summary["statistics"], {"count": 0, "minimum": None, "maximum": None, "mean": None})
            self.assertTrue(all(bin_["count"] == 0 for bin_ in summary["distribution"]))

    def test_score_column_absent_globally_is_an_actionable_mapping_error(self):
        row = {"response": "Answer", **{field: 3 for field in ATTRIBUTES if field != "coherence"}}
        with self.assertRaisesRegex(DatasetError, "coherence"):
            inspect(load_dataset(self.write_rows([row])), self.helpsteer_config())

    def test_conflicting_or_malformed_named_rating_configuration_is_rejected(self):
        cases = {
            "legacy numeric option": {"ratings": {"score": {}}, "numeric_rating": True},
            "legacy range option": {"ratings": {"score": {}}, "rating_range": [0, 4]},
            "legacy required rating": {"ratings": {"score": {}}, "required": ["response", "rating"]},
            "unknown rule": {"ratings": {"score": {"integers": True}}},
            "required not boolean": {"ratings": {"score": {"required": "yes"}}},
            "numeric not boolean": {"ratings": {"score": {"numeric": 1}}},
            "integer not boolean": {"ratings": {"score": {"integer": "true"}}},
            "inverted range": {"ratings": {"score": {"range": [4, 0]}}},
            "boolean bound": {"ratings": {"score": {"range": [False, 4]}}},
            "invalid range shape": {"ratings": {"score": {"range": [1]}}},
            "empty source name": {"ratings": {"": {}}},
            "blank source name": {"ratings": {"  ": {}}},
            "ratings not object": {"ratings": ["score"]},
            "rules not object": {"ratings": {"score": True}},
            "common response alias": {"ratings": {"response": {}}},
            "mapped common response alias": {"columns": {"response": "answer"}, "ratings": {"answer": {}}},
        }
        for label, raw in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(ConfigError):
                    Config.from_dict(raw)

    def test_empty_rating_set_with_no_common_checks_is_no_checks_selected(self):
        config = Config.from_dict({"ratings": {}, "required": [], "unique_id": False})
        result = inspect(load_dataset(self.write_rows([{"unrelated": "data"}])), config)
        self.assertEqual(result.status, "no_checks_selected")
        self.assertEqual(list(result.findings), [])
        self.assertEqual(list(result.rating_summaries), [])

    def test_named_rating_config_round_trip_keeps_rules_without_legacy_conflicts(self):
        config = self.helpsteer_config()
        restored = Config.from_dict(config.to_dict())
        self.assertEqual(restored.to_dict(), config.to_dict())
        row = {"response": "Answer", **{field: 3 for field in ATTRIBUTES}}
        result = inspect(load_dataset(self.write_rows([row])), restored)
        self.assertEqual(result.status, "passed")
        self.assertEqual(len(result.rating_summaries), 5)

    def test_legacy_default_configuration_keeps_original_demo_counts(self):
        source = self.directory / "legacy.csv"
        source.write_text(
            "evaluation_id,response,rating\n"
            "A01,The total is 42.,4\nA02,,3\nA03,See attached.,9\n"
            "A01,The total is 43.,4\nA04,Approved.,good\nA05,Paris.,5\n",
            encoding="utf-8",
        )
        result = inspect(load_dataset(source), Config.from_dict({}))
        self.assertEqual((result.total_records, result.affected_records, len(result.findings), len(result.skipped_checks)), (6, 5, 5, 1))

    def test_helpsteer2_preset_cli_exports_all_five_attributes_and_preserves_source(self):
        from dqi.presets import HELPSTEER2

        self.assertEqual(set(HELPSTEER2["ratings"]), set(ATTRIBUTES))
        rows = [{"response": "First", **{field: 1 for field in ATTRIBUTES}}, {"response": "Second", **{field: 4 for field in ATTRIBUTES}}]
        source = self.write_rows(rows, "helpsteer-example.json")
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        destination = self.directory / "reports"
        code, stdout, stderr = self.run_main(source, "--preset", "helpsteer2", "--output-dir", destination)
        self.assertEqual(code, 0)
        self.assertEqual(stderr, "")
        self.assertIn("passed", stdout)
        for name in ("report.json", "findings.csv", "skipped-checks.csv", "summary.html", "summary.txt"):
            self.assertTrue((destination / name).is_file(), name)
        report = json.loads((destination / "report.json").read_text(encoding="utf-8"), parse_float=Decimal)
        self.assertEqual(report["total_records"], 2)
        self.assertEqual(len(report["rating_summaries"]), 5)
        self.assertEqual(sum(summary["counts"]["valid"] for summary in report["rating_summaries"]), 10)
        html = (destination / "summary.html").read_text(encoding="utf-8")
        text = (destination / "summary.txt").read_text(encoding="utf-8")
        self.assertIn(source.name, html)
        for field in ATTRIBUTES:
            self.assertIn(field, html)
            self.assertIn(field, text.lower())
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_cli_disallows_preset_and_custom_config_together(self):
        source = self.write_rows([])
        config = self.directory / "rules.json"
        config.write_text("{}", encoding="utf-8")
        destination = self.directory / "reports"
        code, stdout, stderr = self.run_main(source, "--preset", "helpsteer2", "--config", config, "--output-dir", destination)
        self.assertEqual(code, 2)
        self.assertEqual(stdout, "")
        self.assertTrue(stderr)
        self.assertFalse(destination.exists())

    def test_new_summary_files_cannot_alias_source_or_config_even_with_overwrite(self):
        row = {"response": "Answer", **{field: 3 for field in ATTRIBUTES}}
        source = self.write_rows([row])
        config = self.directory / "rules.json"
        config.write_text(dumps_json(self.helpsteer_config().to_dict()), encoding="utf-8")
        result = inspect(load_dataset(source), self.helpsteer_config())
        original_bytes = {path: path.read_bytes() for path in (source, config)}
        for name in ("summary.html", "summary.txt"):
            for kind, original in (("source", source), ("config", config)):
                with self.subTest(report=name, protected=kind):
                    destination = self.directory / f"{kind}-{name}"
                    destination.mkdir()
                    try:
                        os.link(original, destination / name)
                    except OSError as error:
                        self.skipTest(f"Filesystem does not support a hardlink fixture: {error}")
                    with self.assertRaises(ValueError):
                        export(result, destination, source, overwrite=True, config_source=config)
                    self.assertEqual({path.name for path in destination.iterdir()}, {name})
                    for path, before in original_bytes.items():
                        self.assertEqual(path.read_bytes(), before)

    def test_zero_with_extreme_exponent_keeps_accurate_mean(self):
        for values, expected in (([Decimal('0E-1000000')], Decimal('0')), ([Decimal('0E-1000000'), Decimal('4')], Decimal('2'))):
            with self.subTest(values=values):
                source = self.write_rows([
                    {'evaluation_id': str(index), 'response': 'Answer', 'rating': value}
                    for index, value in enumerate(values)
                ])
                result = inspect(load_dataset(source), Config.from_dict({'rating_range': [0, 4]}))
                self.assertEqual(result.status, 'passed')
                self.assertEqual(result.rating_summaries[0]['statistics']['mean'], expected)

    def test_existing_readable_summary_alone_blocks_export_without_overwrite(self):
        source = self.write_rows([{ "response": "Answer", **{field: 3 for field in ATTRIBUTES}}])
        result = inspect(load_dataset(source), self.helpsteer_config())
        for name in ("summary.html", "summary.txt"):
            with self.subTest(report=name):
                destination = self.directory / (name + "-existing")
                destination.mkdir()
                summary = destination / name
                summary.write_text("Keep this report", encoding="utf-8")
                with self.assertRaises(ValueError):
                    export(result, destination, source)
                self.assertEqual(summary.read_text(encoding="utf-8"), "Keep this report")
                self.assertEqual({path.name for path in destination.iterdir()}, {name})


if __name__ == "__main__":
    unittest.main()
