from copy import deepcopy
from decimal import Decimal
import unittest

from dqi.core import ConfigError
from dqi.single_task import prepare_single_task


class SingleTaskTests(unittest.TestCase):
    def setUp(self):
        self.criterion = {"field": "response", "description": "1 if the answer provides a usable response.",
                          "min": Decimal("0.0000000000000000001"), "max": 1, "integer": False}
        self.payload = {"prompt": "Prompt\n", "response": "  Reply  ", "rubrics": [self.criterion]}

    def test_preserves_text_exact_scale_and_freezes_the_plan_without_column_name_constraints(self):
        plan = prepare_single_task(self.payload)
        self.assertEqual(plan["review_mode"], "single_task")
        self.assertEqual(plan["records"][0]["prompt"], self.payload["prompt"])
        self.assertEqual(plan["records"][0]["response"], self.payload["response"])
        self.assertEqual(plan["records"][0]["original_ratings"], {})
        self.assertIsNone(plan["records"][0]["source_line"])
        self.assertIsNone(plan["records"][0]["reference"])
        self.assertEqual(plan["attributes"][0]["min"], self.criterion["min"])
        self.criterion["description"] = "Updated after the plan was built."
        self.assertNotEqual(plan["attributes"][0]["description"], self.criterion["description"])
        self.assertNotEqual(plan["rubric_sha256"], prepare_single_task(self.payload)["rubric_sha256"])

    def test_rejects_missing_text_invalid_unicode_oversized_input_and_invalid_rubrics(self):
        for changes in (
            {"prompt": " \n "}, {"response": None}, {"reference": 123}, {"reference": "x" * 40_000},
            {"response": "text\x00"}, {"prompt": "\ud800"}, {"rubric_filename": "../private.txt"},
            {"rubrics": []}, {"rubrics": [self.criterion, deepcopy(self.criterion)]},
            {"rubrics": [dict(self.criterion, min=1)]}, {"rubrics": [dict(self.criterion, integer=True)]},
        ):
            with self.subTest(case=str(changes)[:90]), self.assertRaises(ConfigError):
                prepare_single_task({**self.payload, **changes})
