import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

from wslbuild.runner import run_stages


class RunnerTests(unittest.TestCase):
    def test_cheap_failure_prevents_expensive_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            marker = Path(temp) / "expensive"
            stages = [("cheap", [sys.executable, "-c", "raise SystemExit(1)"]),
                      ("expensive", [sys.executable, "-c", f"open({str(marker)!r}, 'w').close()"])]
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(run_stages(stages, temp), 1)
            self.assertFalse(marker.exists())
            self.assertIn("Rerun:", output.getvalue())
            self.assertTrue((Path(temp) / "cheap.log").exists())

    def test_diagnostic_collects_failures(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            stages = [(name, [sys.executable, "-c", "raise SystemExit(1)"]) for name in ["first", "second"]]
            self.assertEqual(run_stages(stages, temp, diagnostic=True), 1)
            self.assertTrue((Path(temp) / "second.log").exists())

    def test_missing_tool_is_exit_two(self):
        with tempfile.TemporaryDirectory() as temp, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(run_stages([("missing", [str(Path(temp) / "absent")])], temp), 2)
