"""Authenticate stable ancestry before applying the reviewed CachyOS backport."""

import hashlib
import re
import subprocess
import tempfile
from pathlib import Path

from .evidence import digest, load_json, require
from .inputs import ROOT, fetch
from .policy import SHA


def metadata(root=ROOT):
    root = Path(root)
    lock = load_json(root / "kernel/stable.lock.json")
    require(lock.get("schema") == 1 and lock["base_version"] == "7.2.3", "stable base lineage")
    require(re.fullmatch(r"7\.2\.\d+", lock["version"]), "reviewed stable series")
    require(lock["repository"] == "https://github.com/gregkh/linux.git", "stable origin")
    require(lock["signer"] in {"647F28654894E3BD457199BE38DBBDC86092693E",
                               "E27E5D8A3403A2EF66873BBCDEA66FF797772CDC"}, "stable release signer")
    require(lock["public_key"]["path"] == "kernel/upstream/gregkh-release-key.asc" and
            digest(root / lock["public_key"]["path"]) == lock["public_key"]["sha256"], "stable key pin")
    require(set(lock["tags"]) == {"v" + lock["base_version"], "v" + lock["version"]}, "stable tag inventory")
    for tag, entry in lock["tags"].items():
        require(SHA.fullmatch(entry["commit"]) and SHA.fullmatch(entry["object"]), "stable full commit/object pins")
        path = root / "kernel/upstream" / (tag + ".tag")
        require(not path.is_symlink() and path.stat().st_size <= 64 * 1024 and digest(path) == entry["sha256"], "stable tag bytes")
        raw = path.read_bytes()
        oid = hashlib.sha1(b"tag " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False).hexdigest()
        require(oid == entry["object"] and raw.startswith(f"object {entry['commit']}\ntype commit\ntag {tag}\n".encode()), "stable tag object/commit mismatch")
    base, tip = (lock["tags"]["v" + lock[name]]["commit"] for name in ("base_version", "version"))
    require(lock["upstream_delta"]["url"] == f"https://github.com/gregkh/linux/compare/{base}..{tip}.diff", "stable delta commit pins")
    require(lock["adapted_delta"]["path"] == "kernel/patches/stable-7.2.3-13.patch", "reviewed adaptation path")
    patch = root / lock["adapted_delta"]["path"]
    require(not patch.is_symlink() and patch.stat().st_size <= 32 * 1024**2 and digest(patch) == lock["adapted_delta"]["sha256"], "stable adaptation hash")
    return lock


def verify_metadata(root=ROOT):
    root = Path(root)
    lock = metadata(root)
    with tempfile.TemporaryDirectory(prefix="wsl-stable-signature-") as temp:
        home = Path(temp)
        gpg = ["gpg", "--no-options", "--homedir", temp, "--batch", "--no-auto-key-retrieve"]
        subprocess.run([*gpg, "--import", str(root / "kernel/upstream/gregkh-release-key.asc")],
                       check=True, capture_output=True, timeout=30)
        for tag in lock["tags"]:
            raw = (root / "kernel/upstream" / (tag + ".tag")).read_bytes()
            payload, marker, signature = raw.partition(b"-----BEGIN PGP SIGNATURE-----")
            require(marker, "unsigned stable tag")
            (home / "payload").write_bytes(payload)
            (home / "signature").write_bytes(marker + signature)
            result = subprocess.run([*gpg, "--status-fd", "1", "--verify", str(home / "signature"), str(home / "payload")],
                                    check=True, capture_output=True, text=True, timeout=30)
            signatures = [line.split() for line in result.stdout.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
            require(any(fields[2] == lock["signer"] or fields[-1] == lock["signer"] for fields in signatures), "unapproved stable tag signature")
    return lock


def prepare_stable_sources(root=ROOT):
    lock = verify_metadata(root)
    fetch(lock["upstream_delta"]["url"], lock["upstream_delta"]["sha256"], 32 * 1024**2)
    return Path(root) / lock["adapted_delta"]["path"], lock
