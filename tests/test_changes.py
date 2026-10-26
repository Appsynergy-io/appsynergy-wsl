import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from wslbuild.changes import changed_paths, event_range
from wslbuild.policy import classify


class DiffTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.old)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Test fixture")
        Path("README.md").write_text("docs")
        Path("build.sh").write_text("echo build")
        self.commit()
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL, text=True).strip()

    def commit(self):
        self.git("add", ".")
        self.git("-c", "commit.gpgsign=false", "commit", "-qm", "test: fixture")

    def test_deleted_product_file_cannot_be_docs_only(self):
        Path("build.sh").unlink()
        Path("README.md").write_text("new docs")
        self.commit()
        paths, uncertain = changed_paths(self.base, self.git("rev-parse", "HEAD"))
        self.assertIn("build.sh", paths)
        self.assertEqual(classify(paths, uncertain), "product")

    def test_rename_checks_old_and_new_names(self):
        Path("build.sh").rename("build.md")
        self.commit()
        paths, uncertain = changed_paths(self.base, self.git("rev-parse", "HEAD"))
        self.assertEqual(paths, {"build.sh", "build.md"})
        self.assertFalse(uncertain)

    def test_unknown_history_is_full_validation(self):
        paths, uncertain = changed_paths("a" * 40, self.base)
        self.assertTrue(uncertain)
        self.assertEqual(classify(paths, uncertain), "product")

    def test_local_staged_unstaged_and_untracked(self):
        Path("README.md").write_text("changed")
        self.git("add", "README.md")
        Path("build.sh").write_text("other")
        Path("new file\nwith newline").write_text("untracked")
        paths, _ = changed_paths(self.base, self.base, local=True)
        self.assertEqual(paths, {"README.md", "build.sh", "new file\nwith newline"})

    def test_all_event_ranges(self):
        self.assertEqual(event_range({"before": "old", "after": "new"}, "push"), ("old", "new", False))
        self.assertEqual(event_range({"pull_request": {"base": {"sha": "old"}, "head": {"sha": "new"}}}, "pull_request"), ("old", "new", True))
        self.assertEqual(event_range({"merge_group": {"base_sha": "old", "head_sha": "new"}}, "merge_group"), ("old", "new", False))
