import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wslbuild import inputs
from wslbuild.evidence import load_json


class InputTests(unittest.TestCase):
    def test_short_or_mutable_source_pins_fail(self):
        lock = load_json(inputs.ROOT / "sources/inputs.lock.json")
        inputs.validate_lock(lock)
        for section in ["appsynergy", "cachyos", "recipes"]:
            bad = copy.deepcopy(lock)
            bad[section]["commit"] = "main"
            with self.subTest(section=section), self.assertRaises(ValueError):
                inputs.validate_lock(bad)

    def test_tampered_cache_fails_before_network(self):
        expected = hashlib.sha256(b"genuine").hexdigest()
        with tempfile.TemporaryDirectory() as temp, patch.object(inputs, "CACHE", Path(temp)):
            target = Path(temp) / "downloads" / expected
            target.parent.mkdir()
            target.write_bytes(b"tampered")
            with patch.object(inputs.subprocess, "run") as run, self.assertRaisesRegex(ValueError, "tampered"):
                inputs.fetch("https://github.com/example/input", expected)
            run.assert_not_called()

    def test_unsafe_urls_rejected_before_network(self):
        for url in ["http://github.com/input", "https://evil.invalid/input", "https://token@github.com/input"]:
            with self.subTest(url=url), self.assertRaises(ValueError):
                inputs.fetch(url, "a" * 64)
