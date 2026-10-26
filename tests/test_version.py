import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wslbuild.candidate import candidate_version, version_at


class VersionTests(unittest.TestCase):
    def test_metadata_and_ci_alone_do_not_create_a_product_bump(self):
        with tempfile.TemporaryDirectory() as temp, patch("wslbuild.candidate.ROOT", Path(temp)):
            root = Path(temp)
            def git(*args):
                return subprocess.check_output(["git", "-C", temp, *args], stderr=subprocess.DEVNULL, text=True).strip()
            def commit(path, message):
                file = root / path
                file.parent.mkdir(exist_ok=True, parents=True)
                file.write_text(message)
                git("add", ".")
                git("-c", "commit.gpgsign=false", "commit", "-qm", message)
            git("init", "-q", "-b", "main")
            git("config", "user.name", "Test fixture")
            git("config", "user.email", "fixture@example.invalid")
            commit("kernel/wsl.config", "feat: initial product")
            self.assertEqual(version_at("HEAD"), "0.1.0")
            git("tag", "appsynergy-wsl-v0.1.0")
            commit("README.md", "feat: document usage")
            commit("acceptance/record.json", "feat: accept candidate")
            commit("tests/test.txt", "feat: add test")
            commit("wslbuild/checks.py", "ci!: change checks")
            self.assertIsNone(version_at("HEAD"))
            self.assertEqual(candidate_version("HEAD"), "0.1.0")
            commit("kernel/wsl.config", "fix: preserve boot storage")
            self.assertEqual(version_at("HEAD"), "0.1.1")
