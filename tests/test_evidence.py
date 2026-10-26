import copy
import json
import tempfile
import unittest
from pathlib import Path

from wslbuild.evidence import ARTIFACTS, digest, load_json, validate_acceptance, validate_manifest
from wslbuild.policy import SCENARIOS


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        artifacts = {}
        for name in ARTIFACTS:
            path = self.root / name
            path.write_bytes(b"test fixture, not a release artifact")
            artifacts[name] = {"sha256": digest(path), "size": path.stat().st_size}
        self.trusted = {"source": "a" * 40, "inputs_sha256": "b" * 64, "version": "0.1.0",
                        "build": {"repository": "Appsynergy-io/appsynergy-wsl", "event": "push",
                                  "ref": "refs/heads/main", "run_id": 12, "run_attempt": 1}}
        self.manifest = {"schema": 1, **copy.deepcopy(self.trusted), "artifacts": artifacts}
        self.record = {"schema": 1, "kind": "maintainer-acceptance", "source": "a" * 40,
                       "build": copy.deepcopy(self.trusted["build"]), "inputs_sha256": "b" * 64,
                       "manifest_sha256": "c" * 64, "artifacts": copy.deepcopy(artifacts),
                       "host": {"edition": "Enterprise", "build": 26100, "wsl_version": "2.6.0",
                                "architecture": "x64", "cpu_baseline": "x86-64-v3",
                                "kernel_release": "7.2.3-1-appsynergy-wsl"},
                       "scenarios": {s: {"result": "PASS", "diagnostic": "test fixture"} for s in SCENARIOS},
                       "reboot": {"before_boot_id": "before", "after_boot_id": "after", "resumed": True},
                       "maintainer": "fixture-maintainer"}

    def check_report(self):
        validate_acceptance(self.record, self.manifest, "c" * 64, "b" * 64)

    def test_valid_fixture(self):
        validate_manifest(self.manifest, self.root, self.trusted)
        self.check_report()

    def test_tampered_bytes(self):
        path = self.root / "kernel"
        path.write_bytes(b"x" * path.stat().st_size)
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            validate_manifest(self.manifest, self.root, self.trusted)

    def test_missing_artifact(self):
        (self.root / "modules.vhdx").unlink()
        with self.assertRaisesRegex(ValueError, "unavailable"):
            validate_manifest(self.manifest, self.root, self.trusted)

    def test_symlink_artifact(self):
        (self.root / "kernel").unlink()
        (self.root / "kernel").symlink_to(self.root / "modules.vhdx")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            validate_manifest(self.manifest, self.root, self.trusted)

    def test_path_traversal_not_in_allowlist(self):
        self.manifest["artifacts"]["../kernel"] = self.manifest["artifacts"].pop("kernel")
        with self.assertRaisesRegex(ValueError, "inventory"):
            validate_manifest(self.manifest, self.root, self.trusted)

    def test_pr_candidate_rejected_even_if_caller_supplies_matching_build(self):
        self.manifest["build"]["event"] = "pull_request"
        self.trusted["build"]["event"] = "pull_request"
        with self.assertRaisesRegex(ValueError, "trusted main"):
            validate_manifest(self.manifest, self.root, self.trusted)

    def test_forged_build_identity(self):
        self.manifest["build"]["run_id"] += 1
        with self.assertRaisesRegex(ValueError, "build identity"):
            validate_manifest(self.manifest, self.root, self.trusted)

    def test_each_scenario_required(self):
        for scenario in SCENARIOS:
            original = self.record["scenarios"].pop(scenario)
            with self.subTest(scenario=scenario), self.assertRaisesRegex(ValueError, "scenario"):
                self.check_report()
            self.record["scenarios"][scenario] = original

    def test_nonpassing_and_empty_evidence(self):
        for result in ["FAIL", "NOT RUN", "SKIP"]:
            self.record["scenarios"]["systemd"]["result"] = result
            with self.assertRaisesRegex(ValueError, "incomplete"):
                self.check_report()
        self.record["scenarios"]["systemd"] = {"result": "PASS", "diagnostic": ""}
        with self.assertRaisesRegex(ValueError, "incomplete"):
            self.check_report()

    def test_candidate_changes_require_fresh_acceptance(self):
        for key in ["source", "inputs_sha256", "manifest_sha256"]:
            original = self.record[key]
            self.record[key] = "d" * len(original)
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.check_report()
            self.record[key] = original
        with self.assertRaisesRegex(ValueError, "inputs changed"):
            validate_acceptance(self.record, self.manifest, "c" * 64, "d" * 64)

    def test_reboot_required(self):
        self.record["reboot"]["after_boot_id"] = "before"
        with self.assertRaisesRegex(ValueError, "reboot"):
            self.check_report()

    def test_wrong_host(self):
        for field, value in [("edition", "Professional"), ("build", 19045), ("architecture", "ARM64"),
                             ("cpu_baseline", "x86-64"), ("kernel_release", "microsoft-standard-WSL2")]:
            original = self.record["host"][field]
            self.record["host"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                self.check_report()
            self.record["host"][field] = original

    def test_duplicate_json_keys_and_resource_limit(self):
        path = self.root / "report.json"
        path.write_text('{"schema": 1, "schema": 2}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_json(path)
        path.write_bytes(b" " * (1024**2 + 1))
        with self.assertRaisesRegex(ValueError, "1 MiB"):
            load_json(path)
