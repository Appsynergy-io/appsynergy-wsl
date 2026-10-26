"""Idempotent publication policy; accepted binaries are copied, never rebuilt."""

import hashlib
from pathlib import Path

from .evidence import require, digest


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
