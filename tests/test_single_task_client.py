"""Run the single-task browser workflow regression with a simulated API."""

import shutil
import subprocess
import unittest
from pathlib import Path


NODE = shutil.which("node")
HERE = Path(__file__).resolve().parent


@unittest.skipUnless(NODE, "Node.js is needed for the single-task browser test")
class SingleTaskClientTests(unittest.TestCase):
    def test_evaluate_and_restore_one_task(self):
        result = subprocess.run(
            [NODE, str(HERE / "single_task_client.cjs"), str(HERE.parent / "dqi" / "static")],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
