"""Saved key lifecycle and review contracts with synthetic keys; no Keychain writes."""
import ctypes as c
from email.message import Message
from io import BytesIO
import json
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from uuid import uuid4

from dqi import credentials
from dqi.semantic_provider import ProviderError
from dqi.web import AppState, Handler


KEY = "synthetic-key-for-local-tests"
ONE_TASK = {
    "prompt": "Say hello.", "response": "Hello there.",
    "rubrics": [{"field": "tone", "description": "1 for a friendly greeting and 0 otherwise.",
                 "min": 0, "max": 1, "integer": True}],
}
SETTINGS = "/api/settings/api-key"


class CredentialTests(unittest.TestCase):
    def test_unsupported_platform_and_invalid_key_do_not_open_keychain(self):
        with patch.object(credentials.sys, "platform", "linux"):
            self.assertEqual(credentials.key_status(), {"supported": False, "saved": False})
        with patch.object(credentials, "_Request") as native:
            for invalid in ("", "short", "x" * 4097, "has embedded spaces", "new\nline-secret-secret", 22, None):
                with self.assertRaises(credentials.CredentialError):
                    credentials.save_key(invalid)
            native.assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin", "native CoreFoundation types are specific to macOS")
    def test_native_operations_and_queries_without_accessing_a_real_keychain(self):
        cf, security = credentials._frameworks()
        cf.CFDictionaryGetValue.argtypes = [c.c_void_p, c.c_void_p]
        cf.CFDictionaryGetValue.restype = c.c_void_p
        native = Mock()
        stored = None

        def constant(name):
            return c.c_void_p.in_dll(security, name).value

        def value(dictionary, name):
            return cf.CFDictionaryGetValue(dictionary, constant(name))

        def bytes_value(dictionary):
            data = value(dictionary, "kSecValueData")
            return c.string_at(cf.CFDataGetBytePtr(data), cf.CFDataGetLength(data))

        def copy(query, output):
            self.assertTrue(value(query, "kSecAttrAccount"))
            self.assertTrue(value(query, "kSecAttrService"))
            self.assertEqual(value(query, "kSecClass"), constant("kSecClassGenericPassword"))
            metadata = bool(value(query, "kSecReturnAttributes"))
            self.assertEqual(bool(value(query, "kSecReturnData")), not metadata)
            if metadata:
                self.assertEqual(value(query, "kSecUseAuthenticationUI"), constant("kSecUseAuthenticationUIFail"))
            if stored is None:
                return -25300
            # Supply an owned CF object for each successful native Copy result.
            data = b"" if metadata else stored
            c.cast(output, c.POINTER(c.c_void_p))[0] = cf.CFDataCreate(None, data, len(data))
            return 0

        def add(query, _):
            nonlocal stored
            if stored is not None:
                return -25299
            stored = bytes_value(query)
            return 0

        def update(query, changes):
            nonlocal stored
            self.assertTrue(value(query, "kSecAttrService"))
            stored = bytes_value(changes)
            return 0

        def delete(query):
            nonlocal stored
            self.assertTrue(value(query, "kSecAttrAccount"))
            if stored is None:
                return -25300
            stored = None
            return 0

        native.SecItemCopyMatching.side_effect = copy
        native.SecItemAdd.side_effect = add
        native.SecItemUpdate.side_effect = update
        native.SecItemDelete.side_effect = delete
        with patch.object(credentials, "_frameworks", return_value=(cf, native)), \
             patch.object(credentials._Request, "constant", staticmethod(constant)):
            self.assertFalse(credentials.key_status()["saved"])
            self.assertEqual(credentials.save_key(KEY), {"supported": True, "saved": True})
            self.assertTrue(credentials.key_status()["saved"])
            self.assertEqual(credentials.load_key(), KEY)
            self.assertTrue(credentials.save_key(KEY + "-replacement")["saved"])
            self.assertEqual(credentials.load_key(), KEY + "-replacement")
            self.assertFalse(credentials.remove_key()["saved"])
            self.assertFalse(credentials.remove_key()["saved"])
            with self.assertRaisesRegex(credentials.CredentialError, "No saved API key"):
                credentials.load_key()
            native.SecItemCopyMatching.side_effect = lambda *_: -25308
            with self.assertRaisesRegex(credentials.CredentialError, "Unlock"):
                credentials.load_key()


class SettingsHandlerTests(unittest.TestCase):
    def setUp(self):
        self.state = AppState()
        self.addCleanup(self.state.close)

    def call(self, method, path, payload=None, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(payload).encode()
        handler = object.__new__(Handler)
        handler.path = path
        handler.server = SimpleNamespace(server_address=("127.0.0.1", 8765), state=self.state)
        handler.headers = Message()
        for name, val in {"Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765",
                          "X-DQI-Token": self.state.token, "Content-Type": "application/json",
                          "Content-Length": str(len(body)), **(headers or {})}.items():
            handler.headers[name] = val
        handler.rfile = BytesIO(body)
        handler._json = Mock()
        handler._dispatch(method)
        return handler._json.call_args.args

    def finished(self, job_id):
        deadline = time.monotonic() + 3
        while True:
            _, job = self.call("GET", "/api/answer-review/" + job_id)
            if job["status"] != "running" or time.monotonic() >= deadline:
                break
            time.sleep(.01)
        self.assertEqual(job["status"], "completed")
        return job

    def test_only_metadata_returned_and_foreign_or_malformed_changes_rejected(self):
        saved = {"supported": True, "saved": True}
        empty = {"supported": True, "saved": False}
        with patch("dqi.web.credentials.key_status", return_value=saved) as status, \
             patch("dqi.web.credentials.save_key", return_value=saved) as save, \
             patch("dqi.web.credentials.remove_key", return_value=empty) as remove, \
             patch("dqi.web.credentials.load_key") as load:
            self.assertEqual(self.call("GET", SETTINGS, headers={"X-DQI-Token": ""})[0], 403)
            self.assertEqual(self.call("POST", SETTINGS, {"action": "remove"}, {"Origin": "https://foreign.invalid"})[0], 403)
            status.assert_not_called()
            remove.assert_not_called()
            self.assertEqual(self.call("GET", SETTINGS), (200, saved))
            self.assertEqual(self.call("POST", SETTINGS, {"api_key": KEY}), (200, saved))
            save.assert_called_once_with(KEY)
            self.assertEqual(self.call("POST", SETTINGS, {"action": "remove"}), (200, empty))
            self.assertEqual(self.call("POST", SETTINGS, {"api_key": KEY, "action": "remove"})[0], 400)
            self.assertEqual(self.call("POST", SETTINGS, {"action": "load"})[0], 400)
            malformed = ('{"%s":0,"%s":1}' % (KEY, KEY)).encode()
            code, response = self.call("POST", SETTINGS, raw=malformed)
            self.assertEqual(code, 400)
            self.assertNotIn(KEY, str(response))
            load.assert_not_called()

    def test_saved_key_read_only_for_consented_new_job_and_never_in_results(self):
        task = ONE_TASK | {"model": "fake-model", "request_id": uuid4().hex, "use_saved_key": True, "consent": True}
        judge = Mock()
        judge.last_usage = {}
        judge.last_response_received = False
        judge.return_value = {"assessments": [{"field": "tone", "suggested_score": 1,
                              "rationale": "Friendly greeting.", "limitations": [],
                              "evidence": [{"source": "response", "quote": "Hello there."}]}]}
        with patch("dqi.web.credentials.load_key", return_value=KEY) as load, \
             patch("dqi.web.make_judge", return_value=judge) as factory:
            self.assertEqual(self.call("POST", "/api/single-task/preview", ONE_TASK)[0], 200)
            for updated in ({"consent": False}, {"api_key": KEY}, {"use_saved_key": 1}, {"prompt": ""}):
                self.assertEqual(self.call("POST", "/api/single-task/review", task | updated)[0], 400)
            load.assert_not_called()
            code, accepted = self.call("POST", "/api/single-task/review", task)
            self.assertEqual(code, 202)
            factory.assert_called_once_with(api_key=KEY, model="fake-model")
            self.assertEqual(self.call("POST", "/api/single-task/review", task)[1]["job_id"], accepted["job_id"])
            load.assert_called_once_with()
            job = self.finished(accepted["job_id"])
            self.assertNotIn(KEY, str(job))
            for output in self.state.reports[accepted["job_id"]].iterdir():
                self.assertNotIn(KEY.encode(), output.read_bytes())
            opinion = {"model": "second-model", "request_id": uuid4().hex, "consent": True, "use_saved_key": True,
                       "parent_job_id": accepted["job_id"], "record": 1}
            code, second = self.call("POST", "/api/answer-review/second-opinion", opinion)
            self.assertEqual(code, 202)
            self.assertEqual(load.call_count, 2)
            factory.assert_called_with(api_key=KEY, model="second-model")
            self.finished(second["job_id"])

    def test_dataset_review_uses_selected_key_source_and_reports_keychain_error(self):
        _, upload = self.call("POST", "/api/upload", raw=b"prompt,response,tone\nSay hello.,Hello there.,1\n",
                              headers={"X-Filename": "dataset.csv", "Content-Type": "application/octet-stream"})
        config = {"columns": {"response": "response"}, "required": ["response"], "unique_id": False,
                  "ratings": {"tone": {"integer": True, "range": [0, 1]}}}
        _, report = self.call("POST", "/api/inspect", {"upload_id": upload["upload_id"], "config": config})
        options = {"prompt_field": "prompt", "response_field": "response", "rubrics": ONE_TASK["rubrics"]}
        settings = {"report_id": report["report_id"], "options": options, "model": "fake-model",
                    "rates": {"input_per_million": 1, "output_per_million": 2}}
        status, preview = self.call("POST", "/api/answer-review/preview", settings)
        self.assertEqual(status, 200, preview)
        payload = {**settings, "quote_id": preview["quote_id"], "request_id": uuid4().hex, "consent": True}
        with patch("dqi.web.credentials.load_key", side_effect=credentials.CredentialError("No saved API key.")) as load, \
             patch("dqi.web.make_judge") as factory:
            self.assertEqual(self.call("POST", "/api/answer-review", payload | {"use_saved_key": True}),
                             (400, {"error": "No saved API key."}))
            factory.assert_not_called()
            # Stop after resolving each credential, before creating a billed worker.
            factory.side_effect = ProviderError("test", "Simulated provider.")
            self.assertEqual(self.call("POST", "/api/answer-review", payload | {"api_key": KEY})[0], 400)
            load.assert_called_once_with()
            factory.assert_called_once_with(api_key=KEY, model="fake-model")
            load.side_effect = None
            load.return_value = KEY + "-saved"
            self.call("POST", "/api/answer-review", payload | {"use_saved_key": True})
            factory.assert_called_with(api_key=KEY + "-saved", model="fake-model")
