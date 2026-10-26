import json
import tempfile
import unittest
from pathlib import Path

from wslbuild.evidence import digest
from wslbuild.rootfs import verify_rootfs_inputs


class RootfsInputTests(unittest.TestCase):
    def test_stale_source_package_and_artifacts_cannot_enter_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "integration.pkg.tar.zst"
            package.write_bytes(b"built integration")
            Path(str(package) + ".sig").write_bytes(b"detached signature")
            for name in ("rootfs.tar.zst", "rootfs-inventory.txt"):
                (root / name).write_bytes(name.encode())
            record = {"schema": 1, "inputs_sha256": "a" * 64, "testing_signer": False,
                      "integration": {"sha256": digest(package), "signature_sha256": digest(Path(str(package) + ".sig"))},
                      "files": {name: {"sha256": digest(root / name), "size": (root / name).stat().st_size}
                                for name in ("rootfs.tar.zst", "rootfs-inventory.txt")}}
            receipt = root / "rootfs-inputs.json"
            receipt.write_text(json.dumps(record))
            verify_rootfs_inputs(root, "a" * 64, package, True)
            with self.assertRaises(ValueError):
                verify_rootfs_inputs(root, "b" * 64, package, True)
            package.write_bytes(b"different integration")
            with self.assertRaises(ValueError):
                verify_rootfs_inputs(root, "a" * 64, package, True)
            package.write_bytes(b"built integration")
            (root / "rootfs.tar.zst").write_bytes(b"changed filesystem")
            with self.assertRaises(ValueError):
                verify_rootfs_inputs(root, "a" * 64, package, True)
            (root / "rootfs.tar.zst").write_bytes(b"rootfs.tar.zst")
            record["testing_signer"] = True
            receipt.write_text(json.dumps(record))
            with self.assertRaises(ValueError):
                verify_rootfs_inputs(root, "a" * 64, package, True)
            verify_rootfs_inputs(root, "a" * 64, package, False)
