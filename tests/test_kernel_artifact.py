import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wslbuild.evidence import digest
from wslbuild.kernel_artifact import FILES, extract, retrieve, validate_lock, verify_directory


class ControlledKernelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "output"
        self.directory.mkdir()
        for name in FILES:
            (self.directory / name).write_bytes(b"explicit test fixture")
        (self.directory / "kernel-release.txt").write_text("7.2.13-1-appsynergy-wsl\n")
        (self.directory / "build-parameters.json").write_text(json.dumps({
            "module_certificate_sha256": digest(self.directory / "module-signing-cert.x509")}))
        self.builder = {"kind": "maintainer-controlled-linux", "recipe_commit": "a" * 40, "inputs_sha256": "b" * 64}
        self.record = {"schema": 1, "inputs_sha256": "b" * 64, "builder": self.builder,
                       "files": {name: {"size": (self.directory / name).stat().st_size,
                                        "sha256": digest(self.directory / name)} for name in FILES}}
        self.receipt()
        self.lock = {"schema": 1, "inputs_sha256": "b" * 64, "recipe_commit": "a" * 40,
                     "artifact": {"url": "https://github.com/Appsynergy-io/appsynergy-wsl/releases/download/kernel-build-" +
                                         "b" * 64 + "/kernel-" + "b" * 64 + ".tar.gz",
                                  "sha256": "c" * 64, "size": 100, "receipt_sha256": "d" * 64}}

    def receipt(self):
        (self.directory / "kernel-inputs.json").write_text(json.dumps(self.record))

    def test_exact_files_and_provenance_verify_without_compilation(self):
        with patch("wslbuild.kernel_artifact.subprocess.run") as execute:
            verify_directory(self.directory, "b" * 64, self.builder)
        self.assertEqual(execute.call_count, 1)
        self.assertIn("verify-config", execute.call_args.args[0][1])

    def test_tampered_or_missing_bytes_fail_before_configuration_execution(self):
        for missing in (False, True):
            path = self.directory / "kernel"
            if missing:
                path.unlink()
            else:
                path.write_bytes(b"tampered test fixture")
            with self.subTest(missing=missing), patch("wslbuild.kernel_artifact.subprocess.run") as execute:
                with self.assertRaises(ValueError):
                    verify_directory(self.directory, "b" * 64, self.builder)
                execute.assert_not_called()

    def test_symlinks_and_changed_provenance_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "provenance"):
            verify_directory(self.directory, "b" * 64, self.builder | {"recipe_commit": "d" * 40})
        path = self.directory / "kernel"
        path.unlink()
        path.symlink_to(self.directory / "modules.vhdx")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            verify_directory(self.directory, "b" * 64, self.builder)

    def test_module_certificate_binding_is_required(self):
        parameters = self.directory / "build-parameters.json"
        parameters.write_text(json.dumps({"module_certificate_sha256": "d" * 64}))
        self.record["files"][parameters.name] = {"size": parameters.stat().st_size, "sha256": digest(parameters)}
        self.receipt()
        with self.assertRaisesRegex(ValueError, "certificate"):
            verify_directory(self.directory, "b" * 64, self.builder)

    def test_coordinated_cache_and_receipt_tampering_is_rejected(self):
        receipt_hash = digest(self.directory / "kernel-inputs.json")
        path = self.directory / "kernel"
        path.write_bytes(b"tampered kernel and locally forged receipt")
        self.record["files"][path.name] = {"size": path.stat().st_size, "sha256": digest(path)}
        self.receipt()
        with self.assertRaisesRegex(ValueError, "receipt hash"):
            verify_directory(self.directory, "b" * 64, self.builder, receipt_hash)

    def test_invalid_pins_and_mutable_urls_are_rejected(self):
        validate_lock(self.lock)
        for change in [{"recipe_commit": "main"}, {"inputs_sha256": "latest"},
                       {"artifact": self.lock["artifact"] | {"size": True}},
                       {"artifact": self.lock["artifact"] | {"sha256": "invalid"}},
                       {"artifact": self.lock["artifact"] | {"url": "https://example.invalid/latest.tar.gz"}}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_lock(self.lock | change)

    def test_stale_inputs_block_network_and_compilation(self):
        lock = self.root / "lock.json"
        lock.write_text(json.dumps(self.lock))
        with patch("wslbuild.kernel_artifact.LOCK", lock), patch("wslbuild.kernel_artifact.kernel_input_hash", return_value="d" * 64), \
                patch("wslbuild.kernel_artifact.fetch") as fetch, patch("wslbuild.kernel_artifact.subprocess.run") as execute:
            with self.assertRaisesRegex(ValueError, "inputs changed"):
                retrieve()
            fetch.assert_not_called()
            execute.assert_not_called()

    def test_archive_paths_links_duplicates_and_incomplete_inventory_are_rejected(self):
        for name, kind in [("../kernel", tarfile.REGTYPE), ("kernel", tarfile.SYMTYPE),
                           ("kernel", tarfile.REGTYPE)]:
            archive = self.root / "test.tar.gz"
            with tarfile.open(archive, "w:gz") as stream:
                member = tarfile.TarInfo(name)
                member.type, member.size, member.linkname = kind, 1, "../outside"
                stream.addfile(member, io.BytesIO(b"x") if kind == tarfile.REGTYPE else None)
            with tempfile.TemporaryDirectory() as output, self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                extract(archive, output)
        with tarfile.open(archive, "w:gz") as stream:
            for _ in range(2):
                member = tarfile.TarInfo("kernel")
                member.size = 1
                stream.addfile(member, io.BytesIO(b"x"))
        with tempfile.TemporaryDirectory() as output, self.assertRaisesRegex(ValueError, "entry"):
            extract(archive, output)

    def test_complete_archive_round_trip(self):
        archive = self.root / "complete.tar.gz"
        with tarfile.open(archive, "w:gz") as stream:
            for file in sorted(self.directory.iterdir()):
                stream.add(file, arcname=file.name)
        with tempfile.TemporaryDirectory() as output:
            extract(archive, output)
            with patch("wslbuild.kernel_artifact.subprocess.run"):
                verify_directory(output, "b" * 64, self.builder)
