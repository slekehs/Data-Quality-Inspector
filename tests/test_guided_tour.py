"""Exercise the guided-tour navigation without opening a browser or a user session."""

import json
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from urllib.parse import urljoin, urlsplit

from dqi.web import AppState, Handler


TESTS = Path(__file__).resolve().parent
NODE = shutil.which("node")


class PageAssets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.styles = []

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "script" and attrs.get("src"):
            self.scripts.append(attrs["src"])
        if tag == "link" and attrs.get("rel") == "stylesheet":
            self.styles.append(attrs["href"])


class GuidedTourPreviewTests(unittest.TestCase):
    def test_preview_assets_and_sample_are_read_only(self):
        state = AppState()
        self.addCleanup(state.close)
        upload = object()
        state.uploads["current-upload"] = upload

        def get(path):
            request = object.__new__(Handler)
            request.path = path
            request.server = SimpleNamespace(server_address=("127.0.0.1", 8765), state=state)
            request._send = Mock()
            request._get()
            status, body, *_ = request._send.call_args.args
            self.assertEqual(status, 200, path)
            return body

        assets = PageAssets()
        assets.feed(get("/tour-preview").decode("utf-8"))
        scripts = {urlsplit(url).path.split("/")[-1] for url in assets.scripts}
        self.assertIn("tour-preview.js", scripts)
        self.assertTrue(scripts.isdisjoint({"guided-tour.js", "tour-stops.js", "tour-overlay.js"}))
        for url in assets.scripts + assets.styles:
            asset = urlsplit(urljoin("http://127.0.0.1:8765/tour-preview", url))
            self.assertEqual(asset.netloc, "127.0.0.1:8765")
            self.assertTrue(get(asset.path), url)
        sample = json.loads(get("/tour-example.json"))
        other = json.loads(get("/tour-example.json"))
        self.assertTrue(sample["upload"]["preview"])
        self.assertTrue(sample["result"]["report"]["findings"])
        self.assertEqual(sample["upload"], other["upload"])
        self.assertEqual(sample["result"]["report"]["findings"], other["result"]["report"]["findings"])
        self.assertEqual(state.uploads, {"current-upload": upload})
        self.assertFalse(state.reports or state.jobs or state.review_requests)
        self.assertFalse(any(state.directory.iterdir()))


@unittest.skipUnless(NODE, "Node.js is needed for the tour navigation regression")
class GuidedTourTests(unittest.TestCase):
    def test_tour_navigation_preserves_the_live_workspace(self):
        result = subprocess.run(
            [NODE, str(TESTS / "guided_tour_client.cjs"), str(TESTS.parent / "dqi" / "static")],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
