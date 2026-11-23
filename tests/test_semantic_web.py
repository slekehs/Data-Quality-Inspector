"""Bounded jobs, cancellation, exports, and HTTP contracts; all judges are fake."""

import http.client
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from dqi.core import Config, inspect, load_dataset
from dqi.json_utils import dumps_json
from dqi.semantic import prepare_review
from dqi.semantic_provider import ProviderError
from dqi.web import AppState, ReviewJob, Upload, _run_answer_review, make_server


CONFIG = {"columns": {"response": "response"}, "required": ["response"], "unique_id": False, "ratings": {"score": {"required": True, "numeric": True, "integer": True, "range": [0, 4]}}}
OPTIONS = {"prompt_field": "prompt", "response_field": "response", "reference_field": None, "selection": "first", "limit": 3, "records": [], "tolerance": 1, "rubrics": [{"field": "score", "description": "0 means incorrect; 4 means fully correct.", "min": 0, "max": 4, "integer": True}]}
SOURCE = [{"prompt": "What is two plus two?", "response": "The answer is five.", "score": 4}, {"prompt": "What is two plus two?", "response": "The answer is four.", "score": 4}, {"prompt": "", "response": "No question supplied.", "score": 2}]


class FakeJudge:
    def __init__(self, gate=None, fail_on=None, state=None):
        self.calls = []
        self.last_usage = {}
        self.last_model = "test-model-snapshot"
        self.last_response_received = False
        self.closed = False
        self.gate, self.fail_on, self.state = gate, fail_on, state
        self.entered = threading.Event()

    def __call__(self, request):
        self.calls.append(request)
        self.last_usage = {"input_tokens": 20, "output_tokens": 10}
        self.entered.set()
        if self.gate:
            if not self.gate.wait(timeout=5):
                raise RuntimeError("test gate timed out")
        if self.state:
            self.state.close()
        if self.fail_on == len(self.calls):
            raise ProviderError("credits", "No available API credits.")
        self.last_response_received = True
        answer = request["input"]["response"]
        return {"assessments": [{"field": "score", "suggested_score": 0 if "five" in answer else 4, "rationale": "The supplied answer can be checked against basic arithmetic.", "evidence": [{"source": "response", "quote": answer}], "limitations": []}]}

    def close(self):
        self.closed = True


class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState()
        self.addCleanup(self.state.close)
        source = self.state.directory / "source.json"
        source.write_text(dumps_json(SOURCE))
        dataset = load_dataset(source)
        self.plan = prepare_review(dataset, inspect(dataset, Config.from_dict(CONFIG)).to_dict(), OPTIONS)
        self.job = ReviewJob("test-job", "upload", 3, threading.Event())
        self.state.jobs[self.job.job_id] = self.job

    def run_job(self, judge):
        _run_answer_review(self.state, self.job, self.plan, judge, "test-model", {"filename": "source.json", "sha256": "a" * 64, "app_version": "0.6.0"})

    def test_worker_keeps_skips_and_uses_one_request_per_eligible_record(self):
        judge = FakeJudge()
        self.run_job(judge)
        self.assertEqual(self.job.status, "completed")
        self.assertEqual(len(judge.calls), 2)
        self.assertEqual(self.job.processed, 3)
        self.assertEqual(self.job.result["record_counts"]["skipped"], 1)
        self.assertEqual(self.job.result["counts"]["questionable"], 1)
        self.assertEqual(self.job.result["usage"], {"input_tokens": 40, "output_tokens": 20})
        self.assertTrue(self.job.result["usage_complete"])
        self.assertEqual(self.job.result["actual_models"], ["test-model-snapshot"])
        self.assertTrue(judge.closed)
        self.assertNotIn("original_ratings", dumps_json(judge.calls))
        self.assertEqual(set(self.job.urls), {"html", "json", "csv", "zip"})

    def test_provider_failure_stops_without_retry_and_exports_partial_results(self):
        judge = FakeJudge(fail_on=2)
        self.run_job(judge)
        self.assertEqual(self.job.status, "failed")
        self.assertEqual(len(judge.calls), 2)
        self.assertEqual(self.job.result["processed_count"], 2)
        self.assertEqual(self.job.result["pending_count"], 1)
        self.assertEqual(self.job.result["record_counts"]["error"], 1)
        self.assertIn("credits", self.job.error)
        self.assertTrue(judge.closed)
        self.assertTrue((self.state.reports[self.job.job_id] / "answer-review.zip").exists())

    def test_worker_uses_and_exports_instructions_frozen_before_execution(self):
        frozen = self.plan["reviewer_instructions"]
        judge = FakeJudge()
        with patch("dqi.semantic.INSTRUCTIONS", "Changed after the job was planned."):
            self.run_job(judge)
        self.assertEqual(self.job.status, "completed")
        self.assertTrue(judge.calls)
        self.assertTrue(all(call["instructions"] == frozen for call in judge.calls))
        exported = json.loads((self.state.reports[self.job.job_id] / "answer-review.json").read_text())
        self.assertEqual(exported["reviewer_instructions"], frozen)
        self.assertEqual(exported["instructions_sha256"], self.plan["instructions_sha256"])

    def test_cancellation_waits_for_current_call_then_stops_before_next(self):
        gate = threading.Event()
        judge = FakeJudge(gate=gate)
        thread = threading.Thread(target=self.run_job, args=(judge,))
        thread.start()
        self.assertTrue(judge.entered.wait(timeout=2))
        self.job.cancel.set()
        self.assertEqual(self.job.status, "running")
        gate.set()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.job.status, "cancelled")
        self.assertEqual(len(judge.calls), 1)
        self.assertEqual(self.job.result["pending_count"], 2)

    def test_close_prevents_late_export_and_clears_provider(self):
        judge = FakeJudge(state=self.state)
        self.run_job(judge)
        self.assertTrue(judge.closed)
        self.assertEqual(self.state.reports, {})
        self.assertFalse(self.state.directory.exists())
        self.assertEqual(len(judge.calls), 1)

    def test_unknown_provider_usage_is_not_reported_as_zero_cost_or_actual_model(self):
        class UnknownUsageJudge(FakeJudge):
            def __call__(self, request):
                self.calls.append(request)
                self.last_usage = {}
                raise ProviderError("timeout", "The request timed out and was not retried.")
        judge = UnknownUsageJudge()
        self.run_job(judge)
        self.assertEqual(self.job.status, "failed")
        self.assertFalse(self.job.result["usage_complete"])
        self.assertIn("may still incur charges", self.job.result["usage_note"])
        self.assertEqual(self.job.result["actual_models"], [])
        html = (self.state.reports[self.job.job_id] / "answer-review.html").read_text()
        self.assertIn("Token totals are incomplete", html)

    def test_upload_eviction_does_not_remove_a_running_jobs_input(self):
        def upload(name):
            directory = self.state.directory / name
            directory.mkdir()
            path = directory / "data.json"
            path.write_text(dumps_json(SOURCE))
            return Upload(name, "data.json", path, directory, load_dataset(path))
        with patch("dqi.web.MAX_UPLOADS", 2):
            self.state.add(upload("upload"))
            self.state.add(upload("disposable"))
            self.state.add(upload("new"))
        self.assertEqual(set(self.state.uploads), {"upload", "new"})


class SemanticHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        cls.server.server_close()

    def request(self, method, path, payload=None, *, token=True, raw=False, extra=None):
        headers = {"Origin": f"http://127.0.0.1:{self.port}"}
        if token:
            headers["X-DQI-Token"] = self.server.state.token
        if payload is not None:
            headers["Content-Type"] = "application/octet-stream" if raw else "application/json"
            body = payload if raw else dumps_json(payload).encode()
        else:
            body = None
        headers.update(extra or {})
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=3)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            result = response.read()
            return response.status, json.loads(result) if "application/json" in response.getheader("Content-Type", "") else result
        finally:
            connection.close()

    def setUp(self):
        status, upload = self.request("POST", "/api/upload", dumps_json(SOURCE).encode(), raw=True, extra={"X-Filename": "answers.json"})
        self.assertEqual(status, 200)
        self.upload_id = upload["upload_id"]
        status, inspected = self.request("POST", "/api/inspect", {"upload_id": self.upload_id, "config": CONFIG})
        self.assertEqual(status, 200)
        self.report_id = inspected["report_id"]
        self.original_url = inspected["urls"]["json"]

    def payload(self):
        settings = {"report_id": self.report_id, "options": OPTIONS, "model": "test-model",
                    "rates": {"input_per_million": 1, "output_per_million": 2}}
        status, preview = self.request("POST", "/api/answer-review/preview", settings)
        self.assertEqual(status, 200, preview)
        return {**settings, "quote_id": preview["quote_id"], "api_key": "fake-only-secret-key",
                "consent": True, "request_id": str(uuid.uuid4())}

    def poll(self, job_id):
        for _ in range(100):
            status, job = self.request("GET", "/api/answer-review/" + job_id)
            self.assertEqual(status, 200)
            if job["status"] != "running":
                return job
            time.sleep(.01)
        self.fail("Test job did not finish")

    def test_preview_is_free_and_consent_and_key_are_required_before_network(self):
        with patch("dqi.web.make_judge") as factory:
            status, preview = self.request("POST", "/api/answer-review/preview", {"report_id": self.report_id, "options": OPTIONS})
            self.assertEqual(status, 200)
            self.assertEqual((preview["eligible_count"], preview["skipped_count"]), (2, 1))
            self.assertEqual(preview["record_numbers"], [1, 2, 3])
            payload = self.payload(); payload["consent"] = False
            self.assertEqual(self.request("POST", "/api/answer-review", payload)[0], 400)
            factory.assert_not_called()
        payload = self.payload(); payload.pop("api_key")
        self.assertEqual(self.request("POST", "/api/answer-review", payload)[0], 400)
        self.assertEqual(self.request("POST", "/api/answer-review/preview", {"report_id": self.report_id, "options": OPTIONS}, token=False)[0], 403)

    def test_whole_dataset_quote_covers_every_eligible_record_and_cannot_authorize_other_work(self):
        rows = SOURCE * 9
        status, upload = self.request("POST", "/api/upload", dumps_json(rows).encode(), raw=True,
                                      extra={"Content-Type": "application/octet-stream", "X-Filename": "whole.json"})
        self.assertEqual(status, 200)
        status, inspected = self.request("POST", "/api/inspect", {"upload_id": upload["upload_id"], "config": CONFIG})
        self.assertEqual(status, 200)
        settings = {"report_id": inspected["report_id"], "model": "gpt-6.2-sol",
                    "options": {**OPTIONS, "selection": "all"}}
        judge = FakeJudge()
        with patch("dqi.web.make_judge", return_value=judge) as factory:
            code, preview = self.request("POST", "/api/answer-review/preview", settings)
            self.assertEqual(code, 200, preview)
            self.assertEqual((preview["selected_count"], preview["eligible_count"], preview["skipped_count"]),
                             (27, 18, 9))
            self.assertEqual(preview["record_numbers"], list(range(1, 21)))
            self.assertEqual(preview["estimate"]["eligible_count"], 18)
            start = {**settings, "quote_id": preview["quote_id"], "consent": True,
                     "api_key": "fake-for-local-tests", "request_id": uuid.uuid4().hex}
            for change in ({"quote_id": None}, {"report_id": self.report_id}, {"model": "gpt-6.2-astra"},
                           {"rates": {"input_per_million": 1, "output_per_million": 1}},
                           {"options": {**OPTIONS, "selection": "first"}},
                           {"options": {**settings["options"], "tolerance": 0}}):
                with self.subTest(change=change):
                    self.assertEqual(self.request("POST", "/api/answer-review", {**start, **change})[0], 409)
            factory.assert_not_called()
            self.assertEqual(self.request("POST", "/api/answer-review", {**start, "consent": False})[0], 400)
            factory.assert_not_called()
            code, accepted = self.request("POST", "/api/answer-review", start)
            self.assertEqual(code, 202, accepted)
            job = self.poll(accepted["job_id"])
            self.assertEqual(job["status"], "completed", job)
            self.assertEqual(job["result"]["cost_estimate"], preview["estimate"])
            self.assertEqual(job["result"]["record_counts"]["skipped"], 9)
            self.assertEqual(job["result"]["pending_count"], 0)
            self.assertEqual(len(judge.calls), 18)
            self.assertEqual(self.request("POST", "/api/answer-review", start), (202, accepted))
            self.assertEqual(self.request("POST", "/api/answer-review", {**start, "request_id": uuid.uuid4().hex})[0], 409)
            factory.assert_called_once()

    def test_job_exports_and_original_inspection_are_independent(self):
        original = self.request("GET", self.original_url)[1]
        judge = FakeJudge()
        with patch("dqi.web.make_judge", return_value=judge):
            status, started = self.request("POST", "/api/answer-review", self.payload())
            self.assertEqual(status, 202)
            job = self.poll(started["job_id"])
        self.assertEqual(job["status"], "completed")
        self.assertEqual(self.request("GET", self.original_url)[1], original)
        self.assertEqual(self.request("GET", "/api/answer-review/" + job["job_id"], token=False)[0], 403)
        for url in job["urls"].values():
            status, result = self.request("GET", url)
            self.assertEqual(status, 200)
            self.assertNotIn(b"fake-only-secret-key", result if isinstance(result, bytes) else dumps_json(result).encode())
        _, bootstrap = self.request("GET", "/api/bootstrap")
        self.assertEqual(bootstrap["semantic"]["latest_job_id"], job["job_id"])
        self.assertIsNone(bootstrap["semantic"]["active_job_id"])

    def test_cancel_and_single_running_job_and_reinspection_guard(self):
        gate = threading.Event()
        judge = FakeJudge(gate=gate)
        try:
            with patch("dqi.web.make_judge", return_value=judge):
                status, started = self.request("POST", "/api/answer-review", self.payload())
                self.assertEqual(status, 202)
                self.assertTrue(judge.entered.wait(timeout=2))
                self.assertEqual(self.request("POST", "/api/answer-review", self.payload())[0], 409)
                self.assertEqual(self.request("POST", "/api/inspect", {"upload_id": self.upload_id, "config": CONFIG})[0], 409)
                _, bootstrap = self.request("GET", "/api/bootstrap")
                self.assertEqual(bootstrap["semantic"]["active_job_id"], started["job_id"])
                status, cancelled = self.request("POST", "/api/answer-review/" + started["job_id"] + "/cancel", {})
                self.assertEqual(status, 200)
                self.assertEqual(cancelled["status"], "running")
                self.assertTrue(cancelled["cancel_requested"])
                gate.set()
                completed = self.poll(started["job_id"])
            self.assertEqual(completed["status"], "cancelled")
            self.assertEqual(len(judge.calls), 1)
        finally:
            gate.set()

    def test_request_id_recovers_accepted_jobs_without_duplicate_provider_calls(self):
        gate = threading.Event()
        judge = FakeJudge(gate=gate)
        payload = self.payload()
        try:
            with patch("dqi.web.make_judge", return_value=judge) as factory:
                status, started = self.request("POST", "/api/answer-review", payload)
                self.assertEqual(status, 202)
                self.assertTrue(judge.entered.wait(timeout=2))
                status, duplicate = self.request("POST", "/api/answer-review", {**payload, "api_key": "wrong"})
                self.assertEqual(status, 202)
                self.assertEqual(duplicate, started)
                status, recovered = self.request("GET", "/api/answer-review/request/" + payload["request_id"])
                self.assertEqual(status, 200)
                self.assertEqual(recovered["job_id"], started["job_id"])
                self.assertEqual(recovered["request_id"], payload["request_id"])
                gate.set()
                self.poll(started["job_id"])
                self.assertEqual(self.request("POST", "/api/answer-review", payload), (202, started))
                factory.assert_called_once()
                self.assertEqual(len(judge.calls), 2)
                with self.server.state.lock:
                    self.server.state.jobs.pop(started["job_id"])
                self.assertEqual(self.request("POST", "/api/answer-review", payload)[0], 409)
                self.assertEqual(self.request("GET", "/api/answer-review/request/" + payload["request_id"])[0], 409)
                factory.assert_called_once()
        finally:
            gate.set()

    def test_request_id_validation_unknown_lookup_and_session_limit(self):
        with patch("dqi.web.make_judge") as factory:
            for bad in (None, "short", "../x" * 10, "ü" * 20):
                self.assertEqual(self.request("POST", "/api/answer-review", {**self.payload(), "request_id": bad})[0], 400)
            with patch("dqi.web.MAX_REVIEW_REQUESTS", len(self.server.state.review_requests)):
                self.assertEqual(self.request("POST", "/api/answer-review", self.payload())[0], 409)
            factory.assert_not_called()
        unknown = str(uuid.uuid4())
        self.assertEqual(self.request("GET", "/api/answer-review/request/" + unknown)[0], 404)
        self.assertEqual(self.request("GET", "/api/answer-review/request/" + unknown, token=False)[0], 403)

    def test_pasted_task_is_previewed_and_evaluated_without_upload_or_inspection(self):
        task = {"prompt": "Compute two plus two.", "response": "The answer is four.",
                "reference": "2 + 2 = 4.", "rubric_filename": "arithmetic.csv", "rubrics": OPTIONS["rubrics"]}
        uploads_before = dict(self.server.state.uploads)
        before_preview = set(self.server.state.directory.iterdir())
        judge = FakeJudge()
        with patch("dqi.web.make_judge", return_value=judge) as factory:
            status, preview = self.request("POST", "/api/single-task/preview", task)
            self.assertEqual(status, 200, preview)
            self.assertEqual(preview["eligible_count"], 1)
            self.assertEqual(preview["record_numbers"], [1])
            self.assertEqual(before_preview, set(self.server.state.directory.iterdir()))
            self.assertEqual(self.request("POST", "/api/single-task/preview", task, token=False)[0], 403)
            self.assertEqual(self.request("POST", "/api/single-task/preview", {**task, "api_key": "never-send"})[0], 400)
            request_id = str(uuid.uuid4())
            payload = {**task, "api_key": "test-local", "model": "test-model", "request_id": request_id}
            self.assertEqual(self.request("POST", "/api/single-task/review", payload)[0], 400)
            factory.assert_not_called()
            payload["consent"] = True
            status, started = self.request("POST", "/api/single-task/review", payload)
            self.assertEqual(status, 202, started)
            self.assertEqual(self.request("POST", "/api/single-task/review", payload), (202, started))
            self.assertEqual(self.request("POST", "/api/single-task/review", {**payload, "response": "A different answer."})[0], 409)
            self.assertEqual(self.request("POST", "/api/answer-review", {**self.payload(), "request_id": request_id})[0], 409)
            job = self.poll(started["job_id"])
            factory.assert_called_once()
        self.assertEqual(job["status"], "completed", job.get("error"))
        self.assertEqual(len(judge.calls), 1)
        self.assertEqual(judge.calls[0]["input"]["response"], task["response"])
        self.assertEqual(judge.calls[0]["input"]["attributes"][0]["description"], task["rubrics"][0]["description"])
        self.assertNotIn("original_ratings", judge.calls[0]["input"])
        self.assertEqual(job["review_mode"], "single_task")
        _, restored = self.request("GET", "/api/answer-review/request/" + request_id)
        self.assertEqual(restored["review_mode"], "single_task")
        self.assertEqual(self.request("GET", "/api/bootstrap")[1]["semantic"]["restore_review_mode"], "single_task")
        status, exported = self.request("GET", job["urls"]["json"])
        self.assertEqual(status, 200)
        self.assertEqual(exported["source"]["kind"], "single_task")
        self.assertEqual(exported["source"]["rubric_filename"], "arithmetic.csv")
        self.assertEqual(exported["rows"][0]["assessments"][0]["suggested_score"], 4)
        self.assertEqual(exported["review_mode"], "single_task")
        self.assertNotIn("test-local", dumps_json(exported))
        self.assertEqual(self.server.state.uploads, uploads_before)


if __name__ == "__main__":
    unittest.main()
