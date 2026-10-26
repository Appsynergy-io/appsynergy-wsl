import contextlib
import io
import os
import runpy
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


class LocalCommandTests(unittest.TestCase):
    def setUp(self):
        self.main = runpy.run_path(str(ROOT / "scripts/check"))["main"]
        self.previous_directory = Path.cwd()

    def tearDown(self):
        os.chdir(self.previous_directory)

    def invoke(self, arguments):
        output = io.StringIO()
        with patch.object(sys, "argv", ["scripts/check", *arguments]), patch.dict(os.environ), contextlib.redirect_stdout(output):
            result = self.main()
        return result, output.getvalue()

    def test_build_preserves_worker_success_failure_and_missing_prerequisite_codes(self):
        for status in (0, 1, 2):
            with self.subTest(status=status), patch("subprocess.run", side_effect=[
                subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], status)
            ]):
                result, output = self.invoke(["build", "--component", "kernel"])
                self.assertEqual(result, status)
                if status:
                    self.assertIn("Rerun:", output)
                else:
                    self.assertIn("does not establish Windows acceptance", output)

    def test_missing_signed_inputs_exit_two_without_gate_or_build(self):
        with tempfile.TemporaryDirectory() as temp, patch("subprocess.run") as execution:
            for archive in (False, True):
                if archive:
                    (Path(temp) / "integration.pkg.tar.zst").write_bytes(b"unsigned input")
                result, _ = self.invoke(["build", "--component", "rootfs", "--signed-package", temp])
                self.assertEqual(result, 2)
            execution.assert_not_called()

    def test_unavailable_archive_safety_feature_is_a_prerequisite_failure(self):
        with patch.dict(self.main.__globals__, {"tarfile": types.SimpleNamespace()}), patch("subprocess.run") as execution:
            result, output = self.invoke(["fast"])
            self.assertEqual(result, 2)
            self.assertIn("data-filter support", output)
            execution.assert_not_called()
