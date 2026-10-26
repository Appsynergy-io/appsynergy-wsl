import unittest

from wslbuild.publication import publish, sha256


class Store:
    def __init__(self):
        self.assets = {}
        self.version = None
        self.source = None
        self.uploads = []
        self.public = False
        self.fail_after = None
        self.bad_download = False
        self.channel = None

    def latest_version(self):
        return self.version

    def ensure_release(self, tag, source, acceptance_commit):
        if self.source is not None and self.source != source:
            raise ValueError("conflicting tag source")
        self.source = source
        return tag

    def asset_hash(self, release, name):
        return sha256(self.assets[name]) if name in self.assets else None

    def upload(self, release, name, data):
        if self.fail_after is not None and len(self.uploads) >= self.fail_after:
            raise ConnectionError("interrupted upload")
        self.assets[name] = data
        self.uploads.append(name)

    def make_public(self, release):
        self.public = True

    def public_hash(self, tag, name):
        return "bad" if self.bad_download else sha256(self.assets[name])

    def advance_channel(self, version, source, files):
        self.channel = version

    def advance_latest(self, release):
        self.version = release.removeprefix("appsynergy-wsl-v")


class PublicationTests(unittest.TestCase):
    files = {"kernel": b"accepted kernel", "appsynergy-wsl-integration-0.1.0-1-any.pkg.tar.zst": b"signed package",
             "appsynergy-wsl.db": b"signed database", "manifest.sig": b"signature"}

    def test_interrupted_upload_resumes_without_reuploading_completed_bytes(self):
        store = Store()
        store.fail_after = 2
        with self.assertRaises(ConnectionError):
            publish(store, "0.1.0", "source", "acceptance", self.files)
        completed = list(store.uploads)
        self.assertIsNone(store.version)
        store.fail_after = None
        publish(store, "0.1.0", "source", "acceptance", self.files)
        self.assertEqual(store.uploads[:2], completed)
        self.assertEqual(len(store.uploads), len(self.files))
        self.assertLess(store.uploads.index("appsynergy-wsl-integration-0.1.0-1-any.pkg.tar.zst"), store.uploads.index("appsynergy-wsl.db"))
        publish(store, "0.1.0", "source", "acceptance", self.files)
        self.assertEqual(len(store.uploads), len(self.files))

    def test_conflicting_bytes_and_tag_source_are_rejected(self):
        store = Store()
        store.assets["kernel"] = b"conflicting bytes"
        with self.assertRaisesRegex(ValueError, "conflicting immutable"):
            publish(store, "0.1.0", "source", "acceptance", self.files)
        store = Store()
        store.source = "other source"
        with self.assertRaisesRegex(ValueError, "conflicting tag"):
            publish(store, "0.1.0", "source", "acceptance", self.files)

    def test_download_failure_never_advances_any_pointer(self):
        store = Store()
        store.bad_download = True
        with self.assertRaisesRegex(ValueError, "public bytes"):
            publish(store, "0.1.0", "source", "acceptance", self.files)
        self.assertIsNone(store.version)
        self.assertIsNone(store.channel)

    def test_stale_promotion_rejected(self):
        store = Store()
        store.version = "0.2.0"
        with self.assertRaisesRegex(ValueError, "stale"):
            publish(store, "0.1.0", "source", "acceptance", self.files)
        self.assertFalse(store.uploads)
