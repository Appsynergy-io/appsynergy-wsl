import json
import os
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOBS = {"changes", "gates", "package-check", "package-sign", "test-package-sign", "kernel-build", "rootfs-build",
        "candidate-build", "candidate-sign", "acceptance-check", "release"}


class AggregateTests(unittest.TestCase):
    def invoke(self, kind, main, override=None):
        required = {"changes", "gates"}
        if kind == "product":
            required |= {"package-check", "kernel-build", "rootfs-build", "candidate-build"}
            required |= {"package-sign", "candidate-sign"} if main else {"test-package-sign"}
        if kind == "acceptance":
            required |= {"acceptance-check"}
            if main:
                required |= {"release"}
        needs = {j: {"result": "success" if j in required else "skipped"} for j in JOBS}
        needs["changes"]["outputs"] = {"kind": kind}
        for key, value in (override or {}).items():
            if value is None:
                needs.pop(key)
            else:
                needs[key]["result"] = value
        env = os.environ | {"NEEDS_JSON": json.dumps(needs), "EVENT_NAME": "push" if main else "pull_request",
                            "EVENT_REF": "refs/heads/main" if main else "refs/pull/1/merge"}
        return subprocess.run([sys.executable, str(ROOT / "scripts/ci-aggregate")], env=env, capture_output=True).returncode

    def test_every_event_class_has_present_expected_statuses(self):
        for kind in ["docs", "product", "acceptance"]:
            for main in [False, True]:
                with self.subTest(kind=kind, main=main):
                    self.assertEqual(self.invoke(kind, main), 0)

    def test_missing_cancelled_and_unexpected_skips_fail(self):
        for status in [None, "cancelled", "failure", "skipped"]:
            self.assertNotEqual(self.invoke("product", True, {"kernel-build": status}), 0)
        self.assertNotEqual(self.invoke("acceptance", True, {"release": "skipped"}), 0)
        self.assertNotEqual(self.invoke("docs", False, {"package-check": "success"}), 0)

    def test_unknown_class_fails(self):
        self.assertNotEqual(self.invoke("uncertain", False), 0)
