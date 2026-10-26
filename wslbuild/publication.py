"""Idempotent publication policy; accepted binaries are copied, never rebuilt."""

import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

from .evidence import require, digest


def build_database(package, output, image):
    """Build unsigned metadata in isolation; export only a regular database file."""
    package, output = Path(package), Path(output)
    require(re.fullmatch(r"appsynergy-wsl-integration-[0-9.]+-\d+-any\.pkg\.tar\.zst", package.name),
            "database package filename")
    target = output / "appsynergy-wsl.db"
    require(not target.exists() and not target.is_symlink(), "database destination already exists")
    with tempfile.TemporaryDirectory(prefix="wsl-database-") as temp:
        scratch = Path(temp)
        shutil.copyfile(package, scratch / package.name)
        subprocess.run(["docker", "run", "--rm", "--network=none", "--read-only", "--user", f"{os.getuid()}:{os.getgid()}",
                        "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=1g",
                        "--tmpfs=/tmp:rw,nosuid,nodev,size=64m,mode=1777", "--mount", f"type=bind,src={scratch},dst=/output", image,
                        "bash", "-c", "repo-add /output/appsynergy-wsl.db.tar.gz /output/*.pkg.tar.zst"], check=True, timeout=60)
        archive = scratch / "appsynergy-wsl.db.tar.gz"
        require(archive.is_file() and not archive.is_symlink() and archive.stat().st_size <= 1024**2,
                "database archive invalid")
        with tarfile.open(archive, "r:gz") as stream:
            entries = stream.getmembers()
            descriptions = [m for m in entries if m.isfile() and m.name.endswith("/desc")]
            require(len(entries) == 2 and len(descriptions) == 1 and descriptions[0].size <= 64 * 1024,
                    "database package inventory")
            description = stream.extractfile(descriptions[0]).read().decode()
            require(f"%FILENAME%\n{package.name}\n" in description
                    and f"%SHA256SUM%\n{digest(package)}\n" in description, "database package bytes mismatch")
        # repo-add's .db alias points to this archive. Export to a different
        # directory so copying cannot follow the alias back onto the source.
        shutil.copyfile(archive, target)
    return target


def sha256(data):
    return digest(data) if isinstance(data, Path) else hashlib.sha256(data).hexdigest()


def publish(store, version, source, acceptance_commit, files):
    """The store owns authenticated transport. Immutable assets never clobber.

    The release is made public without advancing latest; public bytes are then
    verified. The package channel advances only after those checks, and latest
    advances last. A retry resumes any completed step with byte comparisons.
    """
    proposed = tuple(map(int, version.split(".")))
    latest = store.latest_version()
    require(latest is None or tuple(map(int, latest.split("."))) <= proposed, "stale promotion")
    tag = f"appsynergy-wsl-v{version}"
    release = store.ensure_release(tag, source, acceptance_commit)
    # Packages are uploaded before database metadata, in both release and channel.
    def order(name):
        return (2 if name.startswith("appsynergy-wsl.db") else 0 if ".pkg.tar.zst" in name else 1, name)
    for name in sorted(files, key=order):
        expected = sha256(files[name])
        existing = store.asset_hash(release, name)
        require(existing is None or existing == expected, f"conflicting immutable release bytes: {name}")
        if existing is None:
            store.upload(release, name, files[name])
        require(store.asset_hash(release, name) == expected, f"uploaded bytes mismatch: {name}")
    store.make_public(release)
    for name, data in files.items():
        require(store.public_hash(tag, name) == sha256(data), f"public bytes mismatch: {name}")
    # Recheck immediately before moving any pointer, including retry paths.
    latest = store.latest_version()
    require(latest is None or tuple(map(int, latest.split("."))) <= proposed, "stale promotion")
    store.advance_channel(version, source, files)
    store.advance_latest(release)
    return tag
