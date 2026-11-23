"""Exercise review queue navigation and download links in the browser controller."""

import shutil
import subprocess
import unittest
from pathlib import Path


NODE = shutil.which("node")
HERE = Path(__file__).resolve().parent


@unittest.skipUnless(NODE, "Node.js is needed for the review queue browser test")
class ReviewQueueClientTests(unittest.TestCase):
    def test_pagination_and_downloads_follow_the_current_filters(self):
        result = subprocess.run(
            [NODE, str(HERE / "review_queue_client.cjs"), str(HERE.parent / "dqi" / "static")],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
