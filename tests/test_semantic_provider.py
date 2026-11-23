"""Transport contract tests use fake responses; no API call or real key is used."""

from copy import deepcopy
from decimal import Decimal
import io
import json
import socket
import ssl
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, ProxyHandler

from dqi.json_utils import dumps_json
from dqi.semantic_provider import (
    DEFAULT_MODEL, ENDPOINT, MAX_OUTPUT_TOKENS, MAX_RESPONSE_BYTES,
    ProviderError, _NoRedirect, make_judge,
)


FAKE_KEY = "test-only-key-never-valid"
REVIEW = {"assessments": [{"field": "helpfulness", "suggested_score": 2}]}
REQUEST = {
    "instructions": "Assess the supplied answer using the rubric. Return JSON.",
    "input": {"prompt": "What is 2 + 2?", "response": "Five.", "attributes": [{"field": "helpfulness", "min": 0, "max": 4}]},
    "schema": {"type": "object", "properties": {"assessments": {"type": "array", "items": {"type": "object"}}}, "required": ["assessments"], "additionalProperties": False},
}


def envelope(**overrides):
    return {
        "status": "completed", "model": DEFAULT_MODEL,
        "output": [{"type": "message", "role": "assistant", "status": "completed", "content": [{"type": "output_text", "text": dumps_json(REVIEW)}]}],
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        **overrides,
    }


class Response(io.BytesIO):
    def __init__(self, data, status=200, url=ENDPOINT):
        super().__init__(data if isinstance(data, bytes) else dumps_json(data).encode("utf-8"))
        self.status = status
        self.url = url
        self.read_limits = []

    def getcode(self):
        return self.status

    def geturl(self):
        return self.url

    def read(self, size=-1):
        self.read_limits.append(size)
        return super().read(size)


class SemanticProviderTests(unittest.TestCase):
    def setUp(self):
        self.opener = Mock()
        self.factory = patch("dqi.semantic_provider.build_opener", return_value=self.opener)
        self.build_opener = self.factory.start()
        self.addCleanup(self.factory.stop)

    def judge(self, model=DEFAULT_MODEL):
        judge = make_judge(api_key=FAKE_KEY, model=model)
        self.addCleanup(judge.close)
        return judge

    def assert_provider_error(self, code, call):
        with self.assertRaises(ProviderError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        self.assertNotIn(FAKE_KEY, str(caught.exception))
        self.assertNotIn("UPSTREAM_SECRET", str(caught.exception))
        return caught.exception

    def test_fixed_https_endpoint_schema_no_storage_bounded_output_and_usage(self):
        response = Response(envelope())
        self.opener.open.return_value = response
        judge = self.judge()
        self.assertEqual(judge(deepcopy(REQUEST)), REVIEW)
        self.opener.open.assert_called_once()
        request = self.opener.open.call_args.args[0]
        self.assertEqual(request.full_url, ENDPOINT)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + FAKE_KEY)
        self.assertEqual(self.opener.open.call_args.kwargs, {"timeout": 60})
        body = json.loads(request.data)
        self.assertEqual(body["instructions"], REQUEST["instructions"])
        self.assertEqual(json.loads(body["input"][0]["content"][0]["text"]), REQUEST["input"])
        self.assertFalse(body["store"])
        self.assertEqual(body["service_tier"], "default")
        self.assertEqual(body["model"], DEFAULT_MODEL)
        self.assertEqual(body["reasoning"], {"effort": "medium"})
        self.assertEqual(body["max_output_tokens"], MAX_OUTPUT_TOKENS)
        self.assertEqual(body["text"]["format"], {"type": "json_schema", "name": "answer_review", "strict": True, "schema": REQUEST["schema"]})
        self.assertNotIn("tools", body)
        self.assertNotIn("temperature", body)
        self.assertNotIn(FAKE_KEY, request.data.decode())
        self.assertEqual(judge.last_usage, {"input_tokens": 100, "output_tokens": 50})
        self.assertEqual(judge.last_model, DEFAULT_MODEL)
        self.assertTrue(judge.last_response_received)
        self.assertEqual(response.read_limits, [MAX_RESPONSE_BYTES + 1])
        self.assertTrue(response.closed)

    def test_other_models_are_not_given_unsupported_reasoning_settings(self):
        self.opener.open.return_value = Response(envelope(model="custom-model-2026"))
        judge = self.judge("custom-model")
        judge(REQUEST)
        self.assertNotIn("reasoning", json.loads(self.opener.open.call_args.args[0].data))
        self.assertEqual(judge.last_model, "custom-model-2026")

    def test_environment_proxies_are_disabled_and_tls_verification_enabled(self):
        self.judge()
        handlers = self.build_opener.call_args.args
        self.assertTrue(any(isinstance(handler, ProxyHandler) and handler.proxies == {} for handler in handlers))
        self.assertTrue(any(isinstance(handler, _NoRedirect) for handler in handlers))
        https = next(handler for handler in handlers if isinstance(handler, HTTPSHandler))
        self.assertEqual(https._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(https._context.check_hostname)

    def test_redirect_does_not_forward_credentials(self):
        handler = _NoRedirect()
        self.assert_provider_error("redirect", lambda: handler.redirect_request(None, None, 302, "UPSTREAM_SECRET", {}, "https://example.invalid/"))
        self.opener.open.assert_not_called()

    def test_invalid_credentials_and_model_are_rejected_before_transport(self):
        for key in (None, "", "short", FAKE_KEY + "\r\nInjected: value", " " + FAKE_KEY, FAKE_KEY + "é", "a" * 4097):
            with self.subTest(key_type=type(key).__name__):
                self.assert_provider_error("api_key", lambda: make_judge(api_key=key, model=DEFAULT_MODEL))
        for model in (None, "", "model\nInjected", "../../secret", "model?secret=1", "a" * 201):
            self.assert_provider_error("model", lambda: make_judge(api_key=FAKE_KEY, model=model))
        self.build_opener.assert_not_called()

    def test_bad_internal_request_does_not_send(self):
        judge = self.judge()
        for request in (None, {}, {**REQUEST, "extra": FAKE_KEY}, {**REQUEST, "input": []}, {**REQUEST, "instructions": " "}, {**REQUEST, "schema": None}):
            self.assert_provider_error("request", lambda: judge(request))
        self.assert_provider_error("request", lambda: judge({**REQUEST, "input": {"bad": float("nan")}}))
        self.opener.open.assert_not_called()

    def test_successful_scores_and_rubric_bounds_keep_decimal_precision(self):
        review = {"assessments": [{"field": "rating", "suggested_score": Decimal("0.10000000000000000001")}]}
        data = envelope()
        data["output"][0]["content"][0]["text"] = dumps_json(review)
        self.opener.open.return_value = Response(data)
        self.assertEqual(self.judge()(REQUEST), review)

    def test_refusal_incomplete_and_unexpected_tool_output_do_not_become_reviews(self):
        scenarios = [
            ("refusal", envelope(output=[{"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "UPSTREAM_SECRET"}]}])),
            ("incomplete", envelope(status="incomplete")),
            ("incomplete", envelope(error={"message": "UPSTREAM_SECRET"})),
            ("invalid_response", envelope(output=[{"type": "function_call", "name": "do_something"}])),
            ("invalid_response", envelope(output=[])),
            ("invalid_response", envelope(output=None)),
            ("invalid_response", envelope(output=[False])),
        ]
        for code, data in scenarios:
            with self.subTest(code=code):
                self.opener.open.return_value = Response(data)
                self.assert_provider_error(code, lambda: self.judge()(REQUEST))
        self.assertEqual(self.opener.open.call_count, len(scenarios))

    def test_malformed_json_and_duplicate_keys_are_rejected(self):
        for raw in (b"not json UPSTREAM_SECRET", b"[]", b'{"status":"completed","status":"failed"}', b'\xff', b'{"status":NaN}'):
            self.opener.open.return_value = Response(raw)
            self.assert_provider_error("invalid_response", lambda: self.judge()(REQUEST))
        for output_text in ("```json\n{}\n```", '{"assessments":[],"assessments":[]}', '{"assessments":null}', '[]', '{"assessments":NaN}'):
            data = envelope()
            data["output"][0]["content"][0]["text"] = output_text
            self.opener.open.return_value = Response(data)
            self.assert_provider_error("invalid_response", lambda: self.judge()(REQUEST))

    def test_reasoning_output_is_not_exposed_as_explanation(self):
        data = envelope()
        data["output"].insert(0, {"type": "reasoning", "summary": [{"text": "UPSTREAM_SECRET"}]})
        self.opener.open.return_value = Response(data)
        self.assertEqual(self.judge()(REQUEST), REVIEW)

    def test_invalid_usage_and_returned_model_are_not_reported(self):
        for usage in (None, [], {"input_tokens": True, "output_tokens": 3}, {"input_tokens": -1, "output_tokens": 3}, {"input_tokens": 2, "output_tokens": "3"}, {"input_tokens": 2, "output_tokens": 3, "total_tokens": 99}):
            self.opener.open.return_value = Response(envelope(usage=usage))
            self.assert_provider_error("invalid_response", lambda: self.judge()(REQUEST))
        self.opener.open.return_value = Response(envelope(model="invalid\nUPSTREAM_SECRET"))
        self.assert_provider_error("invalid_response", lambda: self.judge()(REQUEST))

    def test_refused_or_incomplete_reviews_keep_valid_provider_usage(self):
        for status in ("incomplete", "refusal", "malformed"):
            data = envelope(model=DEFAULT_MODEL + "-snapshot")
            if status == "incomplete":
                data["status"] = "incomplete"
            elif status == "refusal":
                data["output"][0]["content"] = [{"type": "refusal", "refusal": "UPSTREAM_SECRET"}]
            else:
                data["output"][0]["content"][0]["text"] = "not JSON"
            judge = self.judge()
            self.opener.open.return_value = Response(data)
            self.assert_provider_error("invalid_response" if status == "malformed" else status, lambda: judge(REQUEST))
            self.assertEqual(judge.last_usage, {"input_tokens": 100, "output_tokens": 50})
            self.assertEqual(judge.last_model, DEFAULT_MODEL + "-snapshot")
            self.assertTrue(judge.last_response_received)

    def test_missing_provider_metadata_stays_unknown(self):
        data = envelope()
        del data["model"]
        del data["usage"]
        judge = self.judge()
        self.opener.open.return_value = Response(data)
        self.assertEqual(judge(REQUEST), REVIEW)
        self.assertEqual(judge.last_usage, {})
        self.assertIsNone(judge.last_model)
        self.assertFalse(judge.last_response_received)

    def test_response_size_limit_and_wrong_endpoint(self):
        response = Response(b"a" * (MAX_RESPONSE_BYTES + 100))
        self.opener.open.return_value = response
        self.assert_provider_error("response_limit", lambda: self.judge()(REQUEST))
        self.assertEqual(response.read_limits, [MAX_RESPONSE_BYTES + 1])
        self.assertTrue(response.closed)
        self.opener.open.return_value = Response(envelope(), url="https://example.invalid/")
        self.assert_provider_error("invalid_response", lambda: self.judge()(REQUEST))

    def test_http_errors_are_sanitized_bounded_and_never_retried(self):
        cases = [(401, None, "authentication"), (403, None, "permission"), (404, None, "model_or_request"), (400, None, "model_or_request"), (429, "credit_balance_exhausted", "credits"), (429, "insufficient_quota", "credits"), (429, "rate_limit_exceeded", "rate_limit"), (500, None, "unavailable"), (504, None, "timeout"), (302, None, "redirect")]
        for status, error_code, expected in cases:
            body = io.BytesIO(dumps_json({"error": {"code": error_code, "message": FAKE_KEY + " UPSTREAM_SECRET"}}).encode())
            self.opener.open.side_effect = HTTPError(ENDPOINT, status, "UPSTREAM_SECRET", {}, body)
            before = self.opener.open.call_count
            self.assert_provider_error(expected, lambda: self.judge()(REQUEST))
            self.assertEqual(self.opener.open.call_count, before + 1)
            self.assertTrue(body.closed)

    def test_network_errors_and_stale_usage(self):
        judge = self.judge()
        self.opener.open.return_value = Response(envelope())
        judge(REQUEST)
        for error, expected in [(socket.timeout(FAKE_KEY), "timeout"), (URLError(socket.timeout(FAKE_KEY)), "timeout"), (URLError(FAKE_KEY), "connection"), (RuntimeError(FAKE_KEY), "connection")]:
            self.opener.open.side_effect = error
            self.assert_provider_error(expected, lambda: judge(REQUEST))
            self.assertEqual(judge.last_usage, {})
            self.assertIsNone(judge.last_model)
            self.assertFalse(judge.last_response_received)

    def test_close_releases_credentials_and_prevents_reuse(self):
        judge = self.judge()
        judge.close()
        self.assertIsNone(judge._api_key)
        self.assert_provider_error("api_key", lambda: judge(REQUEST))
        self.opener.open.assert_not_called()


if __name__ == "__main__":
    unittest.main()
