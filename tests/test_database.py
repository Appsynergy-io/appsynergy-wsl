import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from wslbuild.evidence import digest
from wslbuild.publication import build_database


class DatabaseTests(unittest.TestCase):
    def test_repo_add_alias_is_exported_as_one_regular_hash_bound_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            package = root / "appsynergy-wsl-integration-0.1.0-1-any.pkg.tar.zst"
            package.write_bytes(b"package fixture")
            output = root / "output"
            output.mkdir()
            def repo_add(command, **kwargs):
                bind = next(arg for arg in command if arg.startswith("type=bind,"))
                scratch = Path(bind.split("src=", 1)[1].split(",dst=", 1)[0])
                archive = scratch / "appsynergy-wsl.db.tar.gz"
                content = f"%FILENAME%\n{package.name}\n\n%SHA256SUM%\n{digest(package)}\n".encode()
                with tarfile.open(archive, "w:gz") as stream:
                    directory = tarfile.TarInfo("fixture/")
                    directory.type = tarfile.DIRTYPE
                    stream.addfile(directory)
                    entry = tarfile.TarInfo("fixture/desc")
                    entry.size = len(content)
                    stream.addfile(entry, io.BytesIO(content))
                (scratch / "appsynergy-wsl.db").symlink_to(archive.name)
                (scratch / "appsynergy-wsl.files").write_bytes(b"auxiliary metadata")
            with patch("wslbuild.publication.subprocess.run", side_effect=repo_add):
                database = build_database(package, output, "pinned-image-fixture")
            self.assertFalse(database.is_symlink())
            self.assertEqual(list(output.iterdir()), [database])
            with tarfile.open(database) as stream:
                self.assertIn(digest(package).encode(), stream.extractfile("fixture/desc").read())
            with self.assertRaisesRegex(ValueError, "destination already exists"):
                build_database(package, output, "pinned-image-fixture")
