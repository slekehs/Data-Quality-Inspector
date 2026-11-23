"""Compare preflight estimates to the actual blind request without calling a model."""

from copy import deepcopy
from decimal import Decimal
import unittest

from dqi.core import ConfigError
from dqi.json_utils import dumps_json
from dqi.review_cost import estimate_review_cost
from dqi.semantic import review_record
from dqi.semantic_provider import MAX_OUTPUT_TOKENS


def sample_plan():
    return {
        "reviewer_instructions": "Score independently – include evidence.",
        "attributes": [{"field": "accuracy", "description": "Score on the reference.",
                        "min": 0, "max": 4, "integer": True}],
        "records": [
            {"record": 1, "prompt": "What is 2+2?", "response": "Four ✓",
             "reference": "4", "original_ratings": {"accuracy": 4}},
            {"record": 2, "prompt": "Blank answer", "response": "", "original_ratings": {}},
        ],
    }


class ReviewCostTests(unittest.TestCase):
    def test_only_eligible_blind_requests_are_priced(self):
        plan = sample_plan()
        requests = []

        def mock_judge(request):
            requests.append(request)
            return {"assessments": []}

        for row in plan["records"]:
            review_record(row, plan["attributes"], mock_judge, reviewer_instructions=plan["reviewer_instructions"])
        self.assertEqual(len(requests), 1)
        request = requests[0]
        byte_count = len(request["instructions"].encode("utf-8"))
        byte_count += len(dumps_json(request["input"], indent=None).encode("utf-8"))
        byte_count += len(dumps_json(request["schema"], indent=None).encode("utf-8"))
        tokens = (byte_count + 2) // 3 + 32
        estimate = estimate_review_cost(plan, "gpt-6.2-sol")
        self.assertEqual(estimate["eligible_count"], 1)
        self.assertEqual(estimate["estimated_input_tokens"], tokens)
        self.assertEqual(estimate["estimated_output_tokens"], 800)
        self.assertEqual(estimate["max_output_tokens"], MAX_OUTPUT_TOKENS)
        self.assertEqual(estimate["estimated_usd"], Decimal(tokens * 2 + 800 * 10) / 1_000_000)
        self.assertEqual(estimate["output_limit_usd"], Decimal(tokens * 2 + MAX_OUTPUT_TOKENS * 10) / 1_000_000)
        self.assertTrue(estimate["pricing_url"].endswith("/gpt-6.2-sol"))
        self.assertEqual(estimate_review_cost(plan, "gpt-6.2-astra")["estimated_usd"], 4 * estimate["estimated_usd"])
        plan["records"][0]["original_ratings"] = {"accuracy": "hidden" * 100}
        self.assertEqual(estimate_review_cost(plan, "gpt-6.2-sol"), estimate)

    def test_zero_eligible_and_multiple_requests(self):
        plan = sample_plan()
        baseline = estimate_review_cost(plan, "gpt-6.2-sol")
        plan["records"].extend(deepcopy(plan["records"]))
        twice = estimate_review_cost(plan, "gpt-6.2-sol")
        for key in ("eligible_count", "estimated_usd", "output_limit_usd", "estimated_input_tokens"):
            self.assertEqual(twice[key], 2 * baseline[key])
        plan["records"] = [plan["records"][1]]
        empty = estimate_review_cost(plan, "gpt-6.2-sol")
        self.assertEqual(empty["eligible_count"], 0)
        self.assertEqual(empty["output_limit_usd"], 0)

    def test_custom_rates_require_an_exact_model_match_or_explicit_prices(self):
        plan = sample_plan()
        rates = {"input_per_million": "0.125", "output_per_million": "1.875"}
        for model in ("gpt-6.2-sol-unlisted", "my-model"):
            with self.subTest(model=model):
                with self.assertRaises(ConfigError):
                    estimate_review_cost(plan, model)
                estimate = estimate_review_cost(plan, model, rates)
                self.assertEqual(estimate["input_per_million"], Decimal("0.125"))
                self.assertEqual(estimate["rate_source"], "User supplied")
                self.assertIsNone(estimate["pricing_url"])
                self.assertEqual(estimate["estimated_usd"], (Decimal(".125") * estimate["estimated_input_tokens"]
                                                           + Decimal("1.875") * 800) / 1_000_000)
        for price in ("NaN", True, 0, -1, "1e999999"):
            with self.subTest(price=price), self.assertRaises(ConfigError):
                estimate_review_cost(plan, "my-model", {**rates, "input_per_million": price})
        with self.assertRaises(ConfigError):
            estimate_review_cost(plan, "bad id", rates)
        with self.assertRaises(ConfigError):
            estimate_review_cost(plan, "gpt-6.2-sol", rates)

    def test_long_context_multiplier_applies_to_individual_requests(self):
        plan = sample_plan()
        # Non-ASCII rubric text occupies JSON escapes in the provider's input string.
        plan["attributes"] = [dict(plan["attributes"][0], field=str(i), description="📗" * 6000) for i in range(12)]
        estimate = estimate_review_cost(plan, "gpt-6.2-sol")
        tokens = estimate["estimated_input_tokens"]
        self.assertGreater(tokens, 272_000)
        self.assertGreater(estimate["cache_write_allowance_usd"], 0)
        no_write = Decimal(tokens * 4 + estimate["estimated_output_tokens"] * 15) / 1_000_000
        self.assertEqual(estimate["estimated_usd"], no_write + estimate["cache_write_allowance_usd"])
        self.assertIn("Long-context", estimate["assumptions"])

    def test_standard_cache_allowance_uses_only_the_blind_user_message(self):
        plan = sample_plan()
        plan["reviewer_instructions"] *= 1000
        self.assertEqual(estimate_review_cost(plan, "gpt-6.2-sol")["cache_write_allowance_usd"], 0)
        plan["records"][0]["response"] = "text" * 2000
        payloads = []
        def judge(request):
            payloads.append(request["input"])
            return {"assessments": []}
        review_record(plan["records"][0], plan["attributes"], judge, reviewer_instructions=plan["reviewer_instructions"])
        message_tokens = (len(dumps_json(payloads[0], indent=None)) + 2) // 3
        estimate = estimate_review_cost(plan, "gpt-6.2-sol")
        self.assertEqual(estimate["cache_write_allowance_usd"], Decimal(message_tokens) * Decimal(".50") / 1_000_000)
