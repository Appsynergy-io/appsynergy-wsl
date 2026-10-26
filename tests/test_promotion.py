import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wslbuild.candidate import acceptance


class PromotionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Test fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.commit("kernel/wsl.config", "feat: candidate fixture")
        self.source = self.git("rev-parse", "HEAD")
        self.commit("acceptance/report.json", "test: acceptance fixture", "{}")
        self.addCleanup(patch.stopall)
        patch("wslbuild.candidate.ROOT", self.root).start()
        # Authentication and exact artifact/input checks belong to retrieve;
        # these tests isolate the additional source-history promotion gate.
        patch("wslbuild.candidate.retrieve", return_value=(self.root, {
            "source": self.source, "version": "0.1.0"})).start()

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args],
                                       text=True, stderr=subprocess.DEVNULL).strip()

    def commit(self, path, subject, content="fixture"):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(content)
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", subject)

    def test_unchanged_candidate_can_follow_documentation_changes(self):
        self.commit("README.md", "docs: clarify installation")
        self.commit("AGENTS.md", "docs: clarify checks")
        output, manifest, record = acceptance()
        self.assertEqual(manifest["source"], self.source)
        self.assertEqual(record, self.root / "acceptance/report.json")
        self.assertEqual(output, self.root)

    def test_product_changes_remain_rejected(self):
        self.commit("kernel/wsl.config", "fix: change product", "changed")
        with self.assertRaisesRegex(ValueError, "product inputs"):
            acceptance()
