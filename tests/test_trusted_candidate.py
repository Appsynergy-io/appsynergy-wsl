import unittest
from unittest.mock import patch

from wslbuild.candidate import REPOSITORY, retrieve


class TrustedCandidateTests(unittest.TestCase):
    def setUp(self):
        self.report = {"source": "a" * 40, "build": {"run_id": 10, "run_attempt": 1}}
        self.run = {"workflow_id": 20, "event": "push", "head_branch": "main", "conclusion": "success",
                    "head_sha": "a" * 40, "repository": {"full_name": REPOSITORY}}

    def test_untrusted_or_failed_run_never_downloads(self):
        for key, value in [("event", "pull_request"), ("head_branch", "feature"), ("conclusion", "failure"),
                           ("head_sha", "b" * 40), ("workflow_id", 99)]:
            with self.subTest(key=key), patch("wslbuild.candidate.api", side_effect=[self.run | {key: value}, {"id": 20}]), \
                 patch("wslbuild.candidate.subprocess.run") as execute, self.assertRaisesRegex(ValueError, "trusted-main"):
                retrieve(self.report)
            execute.assert_not_called()

    def test_expired_artifact_rejected(self):
        artifact = {"name": "candidate-" + "a" * 40 + "-1", "expired": True, "size_in_bytes": 100}
        with patch("wslbuild.candidate.api", side_effect=[self.run, {"id": 20}, {"artifacts": [artifact]}]), \
             patch("wslbuild.candidate.subprocess.run") as execute, self.assertRaisesRegex(ValueError, "unavailable"):
            retrieve(self.report)
        execute.assert_not_called()
