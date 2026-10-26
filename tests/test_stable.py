import hashlib
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wslbuild.inputs import ROOT
from wslbuild.stable import prepare_stable_sources, verify_metadata


class StableSourceTests(unittest.TestCase):
    def test_tampered_patch_or_forged_tag_fails_before_download_or_build(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            shutil.copytree(ROOT / "kernel", root / "kernel")
            lock_path = root / "kernel/stable.lock.json"
            lock = json.loads(lock_path.read_text())
            self.assertEqual(verify_metadata(root)["version"], "7.2.13")
            adapted = root / lock["adapted_delta"]["path"]
            original = adapted.read_bytes()
            adapted.write_bytes(original + b"tamper")
            with patch("wslbuild.stable.fetch") as download:
                with self.assertRaisesRegex(ValueError, "stable adaptation hash"):
                    prepare_stable_sources(root)
                download.assert_not_called()
            adapted.write_bytes(original)
            # Even coordinated edits to the tag payload and all its local hashes
            # cannot forge the upstream maintainer's signature.
            entry = lock["tags"]["v7.2.13"]
            tag = root / "kernel/upstream/v7.2.13.tag"
            old_commit = entry["commit"]
            entry["commit"] = ("a" if old_commit[0] != "a" else "b") + old_commit[1:]
            raw = tag.read_bytes().replace(old_commit.encode(), entry["commit"].encode(), 1)
            tag.write_bytes(raw)
            entry["sha256"] = hashlib.sha256(raw).hexdigest()
            entry["object"] = hashlib.sha1(b"tag " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False).hexdigest()
            lock["upstream_delta"]["url"] = lock["upstream_delta"]["url"].replace(old_commit, entry["commit"])
            lock_path.write_text(json.dumps(lock))
            with patch("wslbuild.stable.fetch") as download:
                with self.assertRaises(subprocess.CalledProcessError):
                    prepare_stable_sources(root)
                download.assert_not_called()
