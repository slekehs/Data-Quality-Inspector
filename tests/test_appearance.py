"""Check the appearance control against simulated browser and system preferences."""

import shutil
import subprocess
import unittest
from pathlib import Path


NODE = shutil.which("node")
HERE = Path(__file__).resolve().parent


@unittest.skipUnless(NODE, "Node.js is needed for the appearance tests")
class AppearanceClientTests(unittest.TestCase):
    def test_theme_preferences(self):
        result = subprocess.run(
            [NODE, str(HERE / "appearance_client.cjs"), str(HERE.parent / "dqi" / "static")],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
