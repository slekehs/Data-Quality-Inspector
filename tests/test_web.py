"""Exercise the local web service through actual loopback HTTP requests."""

import csv
from decimal import Decimal
import gzip
import hashlib
import http.client
from io import BytesIO, StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import quote, urlencode, urlsplit
from zipfile import ZipFile

from dqi.json_utils import dumps_json
from dqi.shortcuts import DesktopShortcutError
from dqi.web import make_server


ATTRIBUTES = ("helpfulness", "correctness", "coherence", "complexity", "verbosity")


class WebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.thread.join(timeout=2)
        cls.server.server_close()

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.shortcut_status = {
            "supported": True,
            "installed": False,
            "path": str(self.directory / "Desktop" / "Data Quality Inspector.app"),
            "can_create": True,
            "message": "Create the Desktop shortcut.",
            "reason": None,
        }
        status_patch = patch("dqi.web.shortcuts.desktop_shortcut_status", return_value=self.shortcut_status)
        self.shortcut_status_helper = status_patch.start()
        self.addCleanup(status_patch.stop)
        create_patch = patch("dqi.web.shortcuts.create_desktop_shortcut", return_value={
            **self.shortcut_status, "installed": True, "can_create": False, "message": "The shortcut is ready."
        })
        self.shortcut_create_helper = create_patch.start()
        self.addCleanup(create_patch.stop)
        self.token = None
        status, _, body = self.request("GET", "/api/bootstrap")
        self.assertEqual(status, 200)
        self.bootstrap = self.decode(body)
        self.token = self.bootstrap["csrf_token"]

    @staticmethod
    def decode(body):
        return json.loads(body, parse_float=Decimal)

    def request(self, method, path, body=None, headers=None, token=True):
        request_headers = dict(headers or {})
        if method == "POST":
            request_headers.setdefault("Origin", f"http://127.0.0.1:{self.port}")
            if token:
                request_headers.setdefault("X-DQI-Token", self.token)
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post_json(self, path, data, **options):
        headers = {"Content-Type": "application/json", **options.pop("headers", {})}
        return self.request("POST", path, dumps_json(data).encode("utf-8"), headers=headers, **options)

    def upload(self, filename, content, path="/api/upload"):
        payload = content.encode("utf-8") if isinstance(content, str) else content
        status, _, body = self.request(
            "POST",
            path,
            payload,
            {"Content-Type": "application/octet-stream", "X-Filename": quote(filename, safe="")},
        )
        self.assertEqual(status, 200, body.decode("utf-8", errors="replace"))
        return self.decode(body)

    def inspect_upload(self, upload, config):
        status, _, body = self.post_json("/api/inspect", {"upload_id": upload["upload_id"], "config": config})
        self.assertEqual(status, 200, body.decode("utf-8", errors="replace"))
        return self.decode(body)

    def download(self, url):
        parsed = urlsplit(url)
        self.assertFalse(parsed.netloc, "Report URLs should remain on the local server")
        return self.request("GET", parsed.path + ("?" + parsed.query if parsed.query else ""))

    def helpsteer_rows(self, count=2):
        return [{"response": f"Answer {index}", **{field: index % 5 for field in ATTRIBUTES}} for index in range(count)]

    def test_health_bootstrap_preset_and_unknown_static_route(self):
        status, _, body = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(self.decode(body), {"app": "data-quality-inspector", "version": "0.15.0"})
        self.assertIsInstance(self.token, str)
        self.assertTrue(self.token)
        self.assertEqual(self.bootstrap["max_upload_bytes"], 25 * 1024 * 1024)
        self.assertEqual(set(self.bootstrap["supported_formats"]), {"csv", "tsv", "json", "jsonl", "ndjson"})
        self.assertEqual(self.bootstrap["desktop_shortcut"], self.shortcut_status)
        self.shortcut_status_helper.assert_called_once_with()
        preset = self.bootstrap["presets"]["helpsteer2"]
        self.assertEqual(set(preset["ratings"]), set(ATTRIBUTES))
        status, headers, body = self.request("GET", "/exact-json.js")
        self.assertEqual(status, 200)
        self.assertIn("application/javascript", headers["Content-Type"])
        self.assertTrue(body)
        self.assertEqual(self.request("GET", "/static/does-not-exist.js")[0], 404)

    def test_download_and_import_both_rubric_templates(self):
        status, headers, script = self.request("GET", "/rubric-import.js")
        self.assertEqual(status, 200)
        self.assertIn("application/javascript", headers["Content-Type"])
        self.assertIn(b"/api/rubric-import", script)
        templates = []
        for extension, mime in (("csv", "text/csv"), ("json", "application/json")):
            name = f"rubric-template.{extension}"
            status, headers, body = self.request("GET", f"/{name}")
            self.assertEqual(status, 200)
            self.assertIn(mime, headers["Content-Type"])
            self.assertIn(name, headers["Content-Disposition"])
            status, _, parsed = self.request("POST", "/api/rubric-import", body,
                                            {"Content-Type": "application/octet-stream", "X-Filename": name})
            self.assertEqual(status, 200)
            templates.append(self.decode(parsed)["entries"])
        self.assertEqual(templates[0], templates[1])
        self.assertEqual([entry["field"] for entry in templates[0]], ["resolution_score", "tone_score"])

    def test_desktop_shortcut_creation_returns_status_without_caller_paths(self):
        status, _, body = self.post_json("/api/desktop-shortcut", {})
        self.assertEqual(status, 200)
        self.assertEqual(self.decode(body), self.shortcut_create_helper.return_value)
        self.shortcut_create_helper.assert_called_once_with()
        self.shortcut_create_helper.reset_mock()
        status, _, body = self.request("POST", "/api/desktop-shortcut")
        self.assertEqual(status, 200)
        self.assertTrue(self.decode(body)["installed"])
        self.shortcut_create_helper.assert_called_once_with()

    def test_desktop_shortcut_rejects_foreign_requests_and_filesystem_options(self):
        cases = (
            ("/api/desktop-shortcut", {}, {"X-DQI-Token": "incorrect"}, 403),
            ("/api/desktop-shortcut", {}, {"Origin": "https://foreign.invalid"}, 403),
            ("/api/desktop-shortcut", {"path": str(self.directory / "elsewhere")}, {}, 400),
            ("/api/desktop-shortcut?path=elsewhere", {}, {}, 400),
            ("/api/desktop-shortcut", [], {}, 400),
        )
        for path, payload, headers, expected in cases:
            with self.subTest(path=path, payload=payload, headers=headers):
                status, _, body = self.post_json(path, payload, headers=headers)
                self.assertEqual(status, expected)
                self.assertTrue(self.decode(body)["error"])
        self.shortcut_create_helper.assert_not_called()
        self.assertFalse((self.directory / "Desktop").exists())

    def test_desktop_shortcut_permission_and_conflict_errors_are_readable(self):
        cases = (
            ("permission_denied", 403, "Allow Desktop access in macOS settings, then try again."),
            ("conflict", 409, "Another app already exists on your Desktop. It has not been changed."),
            ("missing_app", 409, "Keep the original inspector app inside its project folder."),
            ("unsupported", 501, "Desktop shortcuts are available on macOS."),
        )
        for code, expected, message in cases:
            with self.subTest(code=code):
                self.shortcut_create_helper.side_effect = DesktopShortcutError(message, code=code, status_code=expected)
                status, _, body = self.post_json("/api/desktop-shortcut", {})
                self.assertEqual(status, expected)
                self.assertEqual(self.decode(body), {"error": message, "code": code})
        self.assertFalse((self.directory / "Desktop").exists())

    def test_gzipped_helpsteer_upload_inspect_and_all_downloads_are_consistent(self):
        rows = self.helpsteer_rows()
        payload = gzip.compress(("\n".join(dumps_json(row, indent=None) for row in rows) + "\n").encode("utf-8"))
        source = self.directory / "synthetic evaluations.jsonl.gz"
        source.write_bytes(payload)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        upload = self.upload(source.name, source.read_bytes())
        self.assertEqual(upload["filename"], source.name)
        self.assertEqual(upload["input_format"], "jsonl")
        self.assertEqual(upload["record_count"], 2)
        self.assertEqual(set(upload["columns"]), {"response", *ATTRIBUTES})
        inspected = self.inspect_upload(upload, self.bootstrap["presets"]["helpsteer2"])
        report = inspected["report"]
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["total_records"], 2)
        self.assertEqual(sum(summary["counts"]["valid"] for summary in report["rating_summaries"]), 10)
        self.assertEqual(report["finding_count"], 0)
        self.assertEqual(set(inspected["urls"]), {"html", "text", "json", "findings", "skipped", "zip"})
        downloaded = {}
        for kind, url in inspected["urls"].items():
            status, _, body = self.download(url)
            self.assertEqual(status, 200, kind)
            downloaded[kind] = body
        self.assertEqual(self.decode(downloaded["json"]), report)
        self.assertIn(source.name, downloaded["html"].decode("utf-8"))
        for field in ATTRIBUTES:
            self.assertIn(field, downloaded["html"].decode("utf-8").lower())
            self.assertIn(field, downloaded["text"].decode("utf-8").lower())
        self.assertEqual(list(csv.DictReader(StringIO(downloaded["findings"].decode("utf-8")))), [])
        self.assertEqual(list(csv.DictReader(StringIO(downloaded["skipped"].decode("utf-8")))), [])
        names = {"html": "summary.html", "text": "summary.txt", "json": "report.json", "findings": "findings.csv", "skipped": "skipped-checks.csv"}
        with ZipFile(BytesIO(downloaded["zip"])) as archive:
            self.assertEqual(set(archive.namelist()), set(names.values()))
            for kind, name in names.items():
                self.assertEqual(archive.read(name), downloaded[kind])
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_inline_report_uses_browser_theme_and_zip_stays_portable(self):
        upload = self.upload("example.json", dumps_json(self.helpsteer_rows()))
        urls = self.inspect_upload(upload, self.bootstrap["presets"]["helpsteer2"])["urls"]
        original = self.download(urls["html"])[2]
        for value in ("dark", "light", "system", "dark-extra"):
            with self.subTest(value=value):
                headers = {"Cookie": f"other=1; dqi_appearance={value}; example=1"}
                status, _, report = self.request("GET", urls["html"], headers=headers)
                self.assertEqual(status, 200)
                if value in ("dark", "light"):
                    self.assertIn(f'<html lang="en" data-theme="{value}">'.encode(), report)
                else:
                    self.assertEqual(report, original)
        status, _, bundle = self.request("GET", urls["zip"], headers={"Cookie": "dqi_appearance=dark"})
        self.assertEqual(status, 200)
        with ZipFile(BytesIO(bundle)) as archive:
            self.assertEqual(archive.read("summary.html"), original)

    def test_configurable_failed_checks_still_generate_downloadable_reports(self):
        content = "answer,score\n,9\nA valid answer,4\n"
        upload = self.upload("scores.csv", content)
        config = {"columns": {"response": "answer"}, "required": ["response"], "unique_id": False, "ratings": {"score": {"integer": True, "range": [0, 4]}}}
        inspected = self.inspect_upload(upload, config)
        report = inspected["report"]
        self.assertEqual((report["status"], report["finding_count"], report["affected_records"]), ("issues_found", 2, 1))
        self.assertEqual({(finding["field"], finding["rule"]) for finding in report["findings"]}, {("answer", "required"), ("score", "range")})
        status, _, body = self.download(inspected["urls"]["findings"])
        self.assertEqual(status, 200)
        findings = list(csv.DictReader(StringIO(body.decode("utf-8"))))
        self.assertEqual(len(findings), 2)
        self.assertEqual({row["record"] for row in findings}, {"1"})

    def test_bad_and_unsupported_uploads_return_input_errors(self):
        fixtures = (
            ("broken.json", b"[{bad json}]"),
            ("broken.csv", b"response,rating\nAnswer,4,extra\n"),
            ("unsupported.exe", b"not a supported dataset"),
            ("broken.jsonl.gz", b"not gzip data"),
        )
        for filename, content in fixtures:
            with self.subTest(filename=filename):
                status, _, body = self.request("POST", "/api/upload", content, {"Content-Type": "application/octet-stream", "X-Filename": quote(filename, safe="")})
                self.assertEqual(status, 400)
                self.assertTrue(self.decode(body)["error"])

    def test_bad_configuration_and_unknown_upload_are_distinct_errors(self):
        upload = self.upload("data.json", dumps_json(self.helpsteer_rows()))
        status, _, body = self.post_json("/api/inspect", {"upload_id": upload["upload_id"], "config": {"ratings": {"helpfulness": {"range": [4, 0]}}}})
        self.assertEqual(status, 400)
        self.assertTrue(self.decode(body)["error"])
        status, _, body = self.post_json("/api/inspect", {"upload_id": "does-not-exist", "config": self.bootstrap["presets"]["helpsteer2"]})
        self.assertEqual(status, 404)
        self.assertTrue(self.decode(body)["error"])

    def test_missing_token_foreign_origin_and_invalid_host_are_rejected(self):
        payload = b"response,rating\nAnswer,4\n"
        cases = (
            ({"X-Filename": "data.csv"}, False),
            ({"X-Filename": "data.csv", "X-DQI-Token": "incorrect"}, False),
            ({"X-Filename": "data.csv", "Origin": "https://foreign.invalid"}, True),
            ({"X-Filename": "data.csv", "Host": "foreign.invalid"}, True),
        )
        for headers, token in cases:
            with self.subTest(headers=headers):
                status, _, body = self.request("POST", "/api/upload", payload, headers, token=token)
                self.assertEqual(status, 403)
                self.assertTrue(self.decode(body)["error"])
        self.assertEqual(self.request("GET", "/api/bootstrap", headers={"Host": "foreign.invalid"})[0], 403)

    def test_oversized_content_length_is_rejected_before_reading_the_payload(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            connection.putrequest("POST", "/api/upload")
            connection.putheader("X-DQI-Token", self.token)
            connection.putheader("Origin", f"http://127.0.0.1:{self.port}")
            connection.putheader("X-Filename", "large.json")
            connection.putheader("Content-Type", "application/octet-stream")
            connection.putheader("Content-Length", str(self.bootstrap["max_upload_bytes"] + 1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            self.assertTrue(self.decode(response.read())["error"])
        finally:
            connection.close()

    def test_unknown_and_traversal_paths_cannot_download_local_files_or_uploads(self):
        secret = self.directory / "sentinel.txt"
        marker = "local-file-sentinel-that-must-not-be-served"
        secret.write_text(marker, encoding="utf-8")
        upload = self.upload("data.json", dumps_json(self.helpsteer_rows()))
        paths = (
            "/../" + str(secret).lstrip("/"),
            "/static/%2e%2e/" + str(secret).lstrip("/"),
            "/api/reports/unknown/report.json",
            "/api/reports/../../" + str(secret).lstrip("/"),
            f"/api/uploads/{upload['upload_id']}",
            f"/api/uploads/{upload['upload_id']}/data.json",
        )
        for path in paths:
            with self.subTest(path=path):
                status, _, body = self.request("GET", path)
                self.assertEqual(status, 404)
                self.assertNotIn(marker.encode("utf-8"), body)

    def test_preview_limits_do_not_truncate_inspection_records_or_values(self):
        long_value = "x" * 1000
        rows = [{"response": f"Answer {index}", "score": 4} for index in range(8)]
        rows[0]["score"] = long_value
        rows[7]["score"] = 9
        upload = self.upload("preview.json", dumps_json(rows))
        self.assertEqual(upload["record_count"], 8)
        self.assertEqual(len(upload["preview"]), 5)
        self.assertIs(upload["preview_truncated"], True)
        self.assertLess(len(upload["preview"][0]["values"]["score"]), len(long_value))
        config = {"required": ["response"], "unique_id": False, "ratings": {"score": {"range": [0, 4]}}}
        inspected = self.inspect_upload(upload, config)
        report = inspected["report"]
        self.assertEqual(report["total_records"], 8)
        self.assertEqual({finding["record"] for finding in report["findings"]}, {1, 8})
        original = next(finding["value"] for finding in report["findings"] if finding["record"] == 1)
        self.assertEqual(original, long_value)

    def test_web_config_and_json_values_keep_exact_decimal_precision(self):
        exact_value = Decimal("4.000000000000000000001")
        exact_lower = Decimal("4.000000000000000000002")
        upload = self.upload("precise.json", dumps_json([{"response": "Answer", "score": exact_value}]))
        config = {"required": ["response"], "unique_id": False, "ratings": {"score": {"range": [exact_lower, 5]}}}
        inspected = self.inspect_upload(upload, config)
        report = inspected["report"]
        self.assertEqual(report["finding_count"], 1)
        self.assertEqual(report["findings"][0]["rule"], "range")
        self.assertEqual(report["findings"][0]["value"], exact_value)
        self.assertEqual(report["config"]["ratings"]["score"]["range"][0], exact_lower)
        status, _, body = self.download(inspected["urls"]["json"])
        self.assertEqual(status, 200)
        downloaded = self.decode(body)
        self.assertEqual(downloaded["findings"][0]["value"], exact_value)
        self.assertEqual(downloaded["config"]["ratings"]["score"]["range"][0], exact_lower)

    def test_explicit_format_can_upload_a_compressed_file_without_known_suffix(self):
        content = dumps_json({"response": "Answer", **{field: 3 for field in ATTRIBUTES}}, indent=None) + "\n"
        upload = self.upload("download.data.gz", gzip.compress(content.encode("utf-8")), path="/api/upload?format=ndjson")
        self.assertEqual(upload["input_format"], "jsonl")
        inspected = self.inspect_upload(upload, self.bootstrap["presets"]["helpsteer2"])
        self.assertEqual(inspected["report"]["status"], "passed")

    def test_gzip_expansion_limit_is_enforced_before_inspection(self):
        content = dumps_json([{ "response": "x" * 500, **{field: 3 for field in ATTRIBUTES}}]).encode("utf-8")
        with patch("dqi.web.MAX_EXPANDED_BYTES", 128):
            status, _, body = self.request(
                "POST",
                "/api/upload",
                gzip.compress(content),
                {"Content-Type": "application/octet-stream", "X-Filename": "expanded.json.gz"},
            )
        self.assertEqual(status, 413)
        self.assertTrue(self.decode(body)["error"])

    def test_upload_eviction_expires_the_old_upload_and_its_report(self):
        preset = self.bootstrap["presets"]["helpsteer2"]
        content = dumps_json(self.helpsteer_rows())
        with patch("dqi.web.MAX_UPLOADS", 2):
            oldest = self.upload("oldest.json", content)
            old_report = self.inspect_upload(oldest, preset)
            self.upload("second.json", content)
            newest = self.upload("newest.json", content)
            status, _, body = self.post_json("/api/inspect", {"upload_id": oldest["upload_id"], "config": preset})
            self.assertEqual(status, 404)
            self.assertTrue(self.decode(body)["error"])
            self.assertEqual(self.download(old_report["urls"]["json"])[0], 404)
            self.assertEqual(self.inspect_upload(newest, preset)["report"]["status"], "passed")

    def test_reinspection_replaces_previous_report_without_losing_the_upload(self):
        upload = self.upload("repeat.json", dumps_json(self.helpsteer_rows()))
        preset = self.bootstrap["presets"]["helpsteer2"]
        original = self.inspect_upload(upload, preset)
        replacement = self.inspect_upload(upload, preset)
        self.assertNotEqual(original["report_id"], replacement["report_id"])
        self.assertEqual(self.download(original["urls"]["json"])[0], 404)
        status, _, body = self.download(replacement["urls"]["json"])
        self.assertEqual(status, 200)
        self.assertEqual(self.decode(body), replacement["report"])

    def test_audit_review_preserves_structural_status_and_exports_contextual_queue(self):
        rows = [
            {"prompt": "=QUESTION", "response": "+RESPONSE", "helpfulness": 1, "category": "alpha"},
            {"prompt": "=QUESTION", "response": "+RESPONSE", "helpfulness": 3, "category": "alpha"},
            {"prompt": "Different question", "response": "Another answer", "helpfulness": 4, "category": "beta"},
        ]
        uploaded_bytes = gzip.compress(dumps_json(rows).encode("utf-8"))
        upload = self.upload("audit.json.gz", uploaded_bytes)
        config = {"required": ["response"], "unique_id": False, "ratings": {"helpfulness": {"integer": True, "range": [0, 4]}}}
        options = {"prompt_field": "prompt", "response_field": "response", "duplicates": True, "conflicts": True, "review_thresholds": {"helpfulness": Decimal("1.000000000000000000001")}, "coverage": {"field": "category", "minimum": 2, "expected_values": ["alpha", "beta", "gamma"]}, "rule_origin": "custom"}
        status, _, body = self.post_json("/api/inspect", {"upload_id": upload["upload_id"], "config": config, "audit": options})
        self.assertEqual(status, 200, body.decode())
        inspected = self.decode(body)
        report, audit = inspected["report"], inspected["report"]["audit"]
        self.assertEqual(set(audit["source"]), {"filename", "sha256", "app_version", "inspected_at"})
        self.assertEqual(audit["source"]["filename"], "audit.json.gz")
        self.assertEqual(audit["source"]["sha256"], hashlib.sha256(uploaded_bytes).hexdigest())
        self.assertEqual(audit["source"]["app_version"], "0.15.0")
        self.assertRegex(audit["source"]["inspected_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["finding_count"], 0)
        self.assertEqual(audit["review_record_count"], 2)
        self.assertEqual(audit["duplicate_group_count"], 1)
        self.assertEqual(audit["conflict_group_count"], 1)
        self.assertEqual(audit["coverage"]["missing_expected_values"], ["gamma"])
        self.assertEqual(audit["options"]["review_thresholds"], options["review_thresholds"])
        self.assertEqual({row["record"] for row in audit["review_queue"]}, {1, 2})
        self.assertEqual(set(inspected["urls"]), {"html", "text", "json", "findings", "skipped", "zip", "review"})
        status, _, review_bytes = self.download(inspected["urls"]["review"])
        self.assertEqual(status, 200)
        review_rows = list(csv.DictReader(StringIO(review_bytes.decode())))
        self.assertEqual(len(review_rows), 2)
        self.assertEqual(review_rows[0]["prompt"], "'=QUESTION")
        self.assertEqual(review_rows[0]["response"], "'+RESPONSE")
        self.assertEqual(json.loads(review_rows[0]["ratings"]), {"helpfulness": 1})
        for kind in ("html", "text"):
            status, _, content = self.download(inspected["urls"][kind])
            self.assertEqual(status, 200)
            self.assertIn(b"Data validation: Passed", content)
            self.assertIn(b"Review needed", content)
            self.assertIn(b"1.000000000000000000001", content)
            self.assertIn(audit["source"]["sha256"].encode(), content)
        status, _, zipped = self.download(inspected["urls"]["zip"])
        self.assertEqual(status, 200)
        with ZipFile(BytesIO(zipped)) as archive:
            self.assertEqual(len(archive.namelist()), 6)
            self.assertEqual(archive.read("review-queue.csv"), review_bytes)
            self.assertEqual(self.decode(archive.read("report.json")), report)
        legacy = self.inspect_upload(upload, config)
        self.assertNotIn("audit", legacy["report"])
        self.assertNotIn("review", legacy["urls"])
        self.assertEqual(self.download(f"/api/reports/{legacy['report_id']}/review-queue.csv")[0], 404)

    def test_filtered_queue_download_matches_records_across_all_pages(self):
        self.assertIs(self.bootstrap["filtered_review_csv"], True)
        field = "Clarté & ton + précision"
        score = Decimal("0.500000000000000000001")
        shared = {"prompt": "=QUESTION", "response": "+first line,\nsecond line"}
        rows = [
            {**shared, field: score, "fluency": 9},
            {**shared, field: 9, "fluency": 0},
        ]
        rows.extend({"prompt": str(i), "response": "Answer", field: 0, "fluency": 3} for i in range(3, 63))
        rows.append({"prompt": "Last flagged", "response": "Answer", field: 3, "fluency": 0})
        rows.append({"prompt": "Passes", "response": "Answer", field: 3, "fluency": 3})
        upload = self.upload("evaluations.json", dumps_json(rows))
        rules = {"range": [0, 4]}
        config = {"required": ["response"], "unique_id": False, "ratings": {field: rules, "fluency": rules}}
        audit = {"prompt_field": "prompt", "duplicates": True, "review_thresholds": {field: 1, "fluency": 1}}
        status, _, body = self.post_json("/api/inspect", {"upload_id": upload["upload_id"], "config": config, "audit": audit})
        self.assertEqual(status, 200, body.decode())
        urls = self.decode(body)["urls"]
        status, full_headers, full_csv = self.download(urls["review"])
        self.assertEqual(status, 200)
        full_reader = csv.DictReader(StringIO(full_csv.decode()))
        full_rows = list(full_reader)
        self.assertEqual(len(full_rows), 63)
        self.assertEqual(full_rows[0]["prompt"], "'=QUESTION")
        self.assertEqual(full_rows[0]["response"], "'" + shared["response"])
        self.assertEqual(self.decode(full_rows[0]["ratings"])[field], score)
        self.assertEqual({reason["kind"] for reason in self.decode(full_rows[0]["reasons"])},
                         {"data_error", "duplicate", "low_rating"})

        # Record 2 has a low fluency score and a validation error on the selected
        # field. Only a single reason satisfying both filters qualifies a record.
        expected = {1, *range(3, 63)}
        cases = [
            ({"reason": "low_rating", "attribute": field}, expected),
            ({"reason": "duplicate"}, {1, 2}),
            ({"attribute": field}, set(range(1, 63))),
            ({"reason": "duplicate", "attribute": field}, set()),
        ]
        for filters, record_ids in cases:
            with self.subTest(filters=filters):
                status, headers, body = self.download(f"{urls['review']}?{urlencode(filters)}")
                self.assertEqual(status, 200)
                self.assertIn("text/csv", headers["Content-Type"])
                self.assertIn("attachment", headers["Content-Disposition"])
                self.assertNotEqual(headers["Content-Disposition"], full_headers["Content-Disposition"])
                reader = csv.DictReader(StringIO(body.decode()))
                self.assertEqual(reader.fieldnames, full_reader.fieldnames)
                self.assertEqual(list(reader), [row for row in full_rows if int(row["record"]) in record_ids])
        self.assertEqual(self.download(urls["review"] + "?reason=&attribute=")[2], full_csv)
        with ZipFile(BytesIO(self.download(urls["zip"])[2])) as archive:
            self.assertEqual(archive.read("review-queue.csv"), full_csv)

    def test_invalid_audit_options_are_input_errors(self):
        upload = self.upload("data.json", dumps_json(self.helpsteer_rows()))
        for options in (None, [], {"unknown": True}, {"review_thresholds": {"helpfulness": "1"}}, {"coverage": {"field": "response", "minimum": 0}}):
            with self.subTest(options=options):
                status, _, body = self.post_json("/api/inspect", {"upload_id": upload["upload_id"], "config": self.bootstrap["presets"]["helpsteer2"], "audit": options})
                self.assertEqual(status, 400)
                self.assertTrue(self.decode(body)["error"])


class WebCloseTests(unittest.TestCase):
    def test_close_requires_local_origin_and_token_then_stops_and_cleans_server(self):
        server = make_server(port=0)
        port = server.server_address[1]
        origin = f"http://127.0.0.1:{port}"
        working_directory = server.state.directory
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(method, path, token=None, request_origin=None):
            headers = {}
            if token is not None:
                headers["X-DQI-Token"] = token
            if request_origin is not None:
                headers["Origin"] = request_origin
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            try:
                connection.request(method, path, body=b"" if method == "POST" else None, headers=headers)
                response = connection.getresponse()
                return response.status, json.loads(response.read())
            finally:
                connection.close()

        try:
            with patch("dqi.web.shortcuts.desktop_shortcut_status", return_value={"supported": False}):
                status, bootstrap = request("GET", "/api/bootstrap")
            self.assertEqual(status, 200)
            token = bootstrap["csrf_token"]
            for invalid_token, invalid_origin in (("incorrect", origin), (token, "https://foreign.invalid")):
                with self.subTest(token_valid=invalid_token == token):
                    status, response = request("POST", "/api/close", invalid_token, invalid_origin)
                    self.assertEqual(status, 403)
                    self.assertTrue(response["error"])
                    self.assertTrue(thread.is_alive())
                    self.assertEqual(request("GET", "/api/health")[0], 200)
            status, response = request("POST", "/api/close", token, origin)
            self.assertEqual(status, 200)
            self.assertEqual(response, {"closed": True})
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
        finally:
            if thread.is_alive():
                server.shutdown()
                thread.join(timeout=3)
            server.server_close()
        self.assertFalse(working_directory.exists())


if __name__ == "__main__":
    unittest.main()
