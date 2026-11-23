"""Rubric import, including the raw upload contract used by the browser."""

from email.message import Message
from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from dqi.rubric_import import MAX_RUBRIC_BYTES, parse_rubric
from dqi.web import Handler


class RubricImportTests(unittest.TestCase):
    def test_json_preserves_exact_values_and_accepts_wrapped_and_bare_lists(self):
        entries = b'[{"field":"score","description":"One point per fact.","min":0,"max":0.9000000000000000001,"integer":false}]'
        for body in (entries, b'\xef\xbb\xbf{"rubrics":' + entries + b'}'):
            with self.subTest(body=body):
                result = parse_rubric("guide.JSON", body)
                self.assertEqual(result, {"filename": "guide.JSON", "entries": [{
                    "field": "score", "description": "One point per fact.", "min": "0",
                    "max": "0.9000000000000000001", "integer": False,
                }]})

    def test_csv_handles_quoted_multiline_criteria_and_optional_scale(self):
        csv = ('description,max,field,integer,min\r\n'
               '"1 = useful, ""complete""\r\n0 = absent",1,quality,TRUE,0\r\n'
               'Credit clarity,,clarity,,\r\n')
        quality, clarity = parse_rubric("scales.csv", csv.encode())["entries"]
        self.assertEqual(quality, {"field": "quality", "description": '1 = useful, "complete"\r\n0 = absent',
                                   "min": "0", "max": "1", "integer": True})
        self.assertEqual(clarity, {"field": "clarity", "description": "Credit clarity",
                                   "min": None, "max": None, "integer": None})
        self.assertEqual(parse_rubric("r.csv", b"field,description\nclarity,Credit clarity")["entries"], [clarity])

    def test_markdown_and_text_are_preserved_as_one_criterion(self):
        criteria = '# Exactitude\n\n- 1 = réponse exacte.\n- 0 = <other>.\n'
        for ext in ("md", "txt"):
            with self.subTest(ext=ext):
                filename = f"Exactitude.{ext}"
                result = parse_rubric(filename, criteria.encode("utf-8-sig"))
                self.assertEqual(result, {"filename": filename, "entries": [{
                    "field": "Exactitude", "description": criteria, "min": None, "max": None, "integer": None,
                }]})

    def test_incomplete_unknown_and_duplicate_entries_fail_the_entire_file(self):
        entry = {"field": "quality", "description": "Credit coverage."}
        for payload in (
            [entry, entry], [entry, {**entry, "levelz": {}}], [{**entry, "max": 2}], [],
            [entry, None], [{}], {"rubrics": [entry], "ignored": True},
            [{**entry, "description": "x" * 6001}], [{**entry, "field": "  "}],
            [{**entry, "field": str(i)} for i in range(13)],
        ):
            with self.subTest(payload=str(payload)[:120]), self.assertRaises(ValueError):
                parse_rubric("rubric.json", json.dumps(payload).encode())
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            parse_rubric("rubric.json", b'[{"field":"one","field":"two","description":"Score it."}]')

    def test_scales_must_be_usable_by_the_answer_reviewer(self):
        for low, high, whole in (
            (1, 1, True), (3, 1, True), (0.5, 1, True), (True, 4, True),
            (0, None, False), ("1e-1001", 1, False), (0, "1e13", False),
            (0, "NaN", False), (0, 2, "true"), (0, 2, 0),
        ):
            entry = {"field": "score", "description": "Assess.", "min": low, "max": high, "integer": whole}
            with self.subTest(scale=(low, high, whole)), self.assertRaises(ValueError):
                parse_rubric("r.json", json.dumps([entry]).encode())

    def test_csv_rejects_wrong_headers_broken_quoting_and_partial_rows(self):
        for csv in (
            "field,description,extra\nq,Quality,1", "field,field\nq,Quality",
            "field,description,min\nq,Quality,0", "field,description\nq,Quality,extra",
            'field,description\nq,"broken', "field,description\nq",
            "field,description,min,max,integer\nq,Quality,,1,true",
            "field,description,min,max,integer\nq,Quality,0,1,",
        ):
            with self.subTest(csv=csv), self.assertRaises(ValueError):
                parse_rubric("r.csv", csv.encode())

    def test_rejects_oversize_binary_invalid_text_and_unsupported_formats(self):
        for filename, body in (
            ("r.txt", b"x" * (MAX_RUBRIC_BYTES + 1)), ("r.csv", b"\xff"),
            ("r.md", b"\n\t"), ("r.txt", b"a\x00b"), ("r.docx", b"hello"),
            ("r.json", br'[{"field":"a","description":"\ud800"}]'),
        ):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                parse_rubric(filename, body)


class RubricImportRouteTests(unittest.TestCase):
    def post(self, headers=None, body=b"Rate coverage."):
        handler = Handler.__new__(Handler)
        handler.path = "/api/rubric-import"
        handler.server = SimpleNamespace(server_address=("127.0.0.1", 8765), state=SimpleNamespace(token="local-test"))
        handler.headers = Message()
        for key, value in {
            "Host": "127.0.0.1:8765", "Origin": "http://127.0.0.1:8765", "X-DQI-Token": "local-test",
            "X-Filename": "Scoring%20field.txt", "Content-Type": "application/octet-stream",
            "Content-Length": str(len(body)), **(headers or {}),
        }.items():
            if value is not None:
                handler.headers[key] = value
        handler.rfile = BytesIO(body)
        handler._json = Mock()
        handler.do_POST()
        handler._json.assert_called_once()
        return handler._json.call_args.args

    def test_local_route_returns_entries_with_decoded_filename(self):
        status, result = self.post()
        self.assertEqual(status, 200)
        self.assertEqual(result["filename"], "Scoring field.txt")
        self.assertEqual(result["entries"][0]["field"], "Scoring field")

    def test_route_applies_origin_token_filename_and_body_limits(self):
        for headers, expected in (
            ({"Host": "example.com"}, 403), ({"Origin": "https://example.com"}, 403),
            ({"X-DQI-Token": None}, 403), ({"X-Filename": "..%2Fother.txt"}, 400),
            ({"Content-Type": "text/plain"}, 400), ({"Content-Length": str(MAX_RUBRIC_BYTES + 1)}, 413),
            ({"Content-Length": "200"}, 400),
        ):
            with self.subTest(headers=headers):
                status, result = self.post(headers)
                self.assertEqual(status, expected)
                self.assertTrue(result["error"])
                if status == 413:
                    self.assertIn("256 KiB", result["error"])
