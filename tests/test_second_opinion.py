"""Exercise one-record follow-ups through the HTTP handler with a local fake judge."""
from email.message import Message
from io import BytesIO
import json
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from dqi.core import Config, inspect, load_dataset
from dqi.semantic import prepare_review
from dqi.semantic_provider import ProviderError
from dqi.web import AppState, Handler, ReviewJob, _run_answer_review


class Judge:
    last_model = "first-model-snapshot"
    last_response_received = True
    last_usage = {"input_tokens": 5, "output_tokens": 6}

    def __init__(self, score, *, gate=None, fail=False):
        self.score = score
        self.calls = []
        self.gate = gate
        self.entered = threading.Event()
        self.fail = fail
        self.closed = False

    def __call__(self, request):
        self.calls.append(request)
        self.entered.set()
        if self.gate and not self.gate.wait(timeout=3):
            raise RuntimeError("test gate timed out")
        if self.fail:
            raise ProviderError("credits", "Insufficient test credits.")
        return {"assessments": [{"field": "quality", "suggested_score": self.score,
            "rationale": "This is the test reviewer's assessment.",
            "evidence": [{"source": "response", "quote": request["input"]["response"]}],
            "limitations": []}]}

    def close(self):
        self.closed = True


class SecondOpinionTests(unittest.TestCase):
    endpoint = "/api/answer-review/second-opinion"

    def setUp(self):
        self.state = AppState()
        self.addCleanup(self.state.close)
        path = self.state.directory / "data.json"
        path.write_text(json.dumps([
            {"prompt": "First?", "answer": "First answer.", "ref": "First reference", "quality": 3},
            {"prompt": "Second?", "answer": "Second answer.", "ref": "Second reference", "quality": 4},
            {"prompt": "", "answer": "Skip me.", "ref": "", "quality": 1},
        ]))
        dataset = load_dataset(path)
        config = Config.from_dict({"columns": {"response": "answer"}, "required": ["response"], "unique_id": False,
                                   "ratings": {"quality": {"integer": True, "range": [0, 4]}}})
        options = {"prompt_field": "prompt", "response_field": "answer", "reference_field": "ref",
                   "selection": "first", "limit": 3, "tolerance": 0.5,
                   "rubrics": [{"field": "quality", "description": "0 for low, 4 for high.",
                                "min": 0, "max": 4, "integer": True}]}
        plan = prepare_review(dataset, inspect(dataset, config).to_dict(), options)
        self.primary = ReviewJob("original", "expired-upload", 3, threading.Event())
        self.state.jobs[self.primary.job_id] = self.primary
        self.first_judge = Judge(1)
        _run_answer_review(self.state, self.primary, plan, self.first_judge, "first-model",
                           {"filename": "data.json", "sha256": "a" * 64, "app_version": "test"})
        self.assertEqual(self.primary.status, "completed")

    def request(self, path, payload=None, *, token=None):
        # Dispatch real JSON bytes and authentication headers without binding a port.
        body = json.dumps(payload).encode() if payload is not None else b""
        handler = Handler.__new__(Handler)
        handler.server = SimpleNamespace(state=self.state, server_address=("127.0.0.1", 8765))
        handler.headers = Message()
        for name, value in {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765",
                            "Content-Type": "application/json", "Content-Length": str(len(body)),
                            "X-DQI-Token": self.state.token if token is None else token}.items():
            handler.headers[name] = value
        handler.path = path
        handler.rfile = BytesIO(body)
        replies = []
        handler._send = lambda status, data, *args: replies.append((status, json.loads(data)))
        handler._dispatch("POST" if payload is not None else "GET")
        return replies[0]

    def payload(self, **changes):
        return {"parent_job_id": "original", "record": 2, "model": "second-model",
                "request_id": str(uuid.uuid4()), "api_key": "fake-for-local-tests",
                "consent": True, **changes}

    def wait_for(self, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status, job = self.request("/api/answer-review/" + job_id)
            self.assertEqual(status, 200)
            if job["status"] != "running":
                return job
            time.sleep(.01)
        self.fail("Review worker did not finish")

    def test_frozen_record_request_and_linked_results_survive_reload_and_retry(self):
        payload = self.payload()
        original_export = self.state.reports["original"] / "answer-review.json"
        before = original_export.read_bytes()
        gate = threading.Event()
        self.addCleanup(gate.set)
        judge = Judge(3, gate=gate)
        with patch("dqi.web.make_judge", return_value=judge) as factory, patch("dqi.semantic.INSTRUCTIONS", "New instructions."):
            status, started = self.request(self.endpoint, payload)
            self.assertEqual(status, 202)
            self.assertTrue(judge.entered.wait(timeout=2))
            self.assertEqual(self.request(self.endpoint, payload), (202, started))
            self.assertEqual(self.request(self.endpoint, {**payload, "record": 1})[0], 409)
            self.assertEqual(self.request(self.endpoint, self.payload())[0], 409)
            recovered = self.request("/api/answer-review/request/" + payload["request_id"])[1]
            self.assertEqual(recovered["parent_job_id"], "original")
            gate.set()
            job = self.wait_for(started["job_id"])
            self.assertEqual(self.request(self.endpoint, payload), (202, started))
            factory.assert_called_once_with(api_key="fake-for-local-tests", model="second-model")
        self.assertTrue(judge.closed)
        self.assertEqual(judge.calls, [self.first_judge.calls[1]])
        self.assertEqual(job["status"], "completed")
        result = job["result"]
        self.assertEqual(result["rows"][0]["assessments"][0]["suggested_score"], 3)
        self.assertEqual(result["primary_review"], {"job_id": "original", "record": 2, "model": "first-model",
            "assessments": self.primary.result["rows"][1]["assessments"]})
        self.assertEqual(result["selected_count"], 1)
        self.assertEqual(result["options"]["records"], [2])
        self.assertEqual(result["source"], self.primary.result["source"])
        self.assertEqual(result["rubric_sha256"], self.primary.result["rubric_sha256"])
        _, exported = self.request(job["urls"]["json"])
        self.assertEqual(result, exported)
        self.assertNotIn(payload["api_key"], json.dumps(exported))
        self.assertEqual(original_export.read_bytes(), before)
        self.assertEqual(self.request(self.endpoint, self.payload(parent_job_id=job["job_id"]))[0], 400)

    def test_only_eligible_records_different_models_and_explicit_consent_are_accepted(self):
        with patch("dqi.web.make_judge") as factory:
            for change in ({"record": 3}, {"record": 99}, {"record": True}, {"record": 1.5},
                           {"model": "first-model"}, {"model": "first-model-snapshot"},
                           {"model": ""}, {"model": None}, {"consent": False}, {"options": {}}):
                with self.subTest(change=change):
                    self.assertEqual(self.request(self.endpoint, self.payload(**change))[0], 400)
            self.assertEqual(self.request(self.endpoint, self.payload(parent_job_id="expired"))[0], 404)
            self.assertEqual(self.request(self.endpoint, self.payload(), token="wrong")[0], 403)
            factory.assert_not_called()

    def test_child_failure_is_retained_and_can_expire_without_orphaning_its_parent(self):
        fail = Judge(0, fail=True)
        first_payload = self.payload()
        with patch("dqi.web.MAX_REVIEW_JOBS", 2):
            with patch("dqi.web.make_judge", return_value=fail):
                status, first = self.request(self.endpoint, first_payload)
                self.assertEqual(status, 202)
                failed = self.wait_for(first["job_id"])
            self.assertEqual(failed["status"], "failed")
            self.assertEqual(failed["result"]["primary_review"]["model"], "first-model")
            self.assertEqual(len(fail.calls), 1)
            with patch("dqi.web.make_judge", return_value=Judge(4)):
                status, next_job = self.request(self.endpoint, self.payload(record=1))
                self.assertEqual(status, 202)
                self.wait_for(next_job["job_id"])
            self.assertEqual(self.request(self.endpoint, first_payload)[0], 409)
            self.assertEqual(self.request("/api/answer-review/" + first["job_id"])[0], 404)
            self.assertEqual(self.request("/api/answer-review/original")[1]["status"], "completed")
            self.assertEqual(len(self.state.jobs), 2)


if __name__ == "__main__":
    unittest.main()
