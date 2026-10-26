"""Fetch locked public inputs into a disposable cache, without Git credentials."""

import hashlib
import os
import re
import subprocess
import tarfile
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .evidence import digest, load_json, require
from .policy import HASH, SHA

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache"


def validate_lock(lock):
    require(lock.get("schema") == 1, "source lock schema")
    for name in ("appsynergy", "cachyos", "recipes"):
        require(SHA.fullmatch(lock[name]["commit"]), f"{name}: full commit SHA required")
        require(re.fullmatch(r"https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\.git",
                             lock[name]["repository"]), "repository URL")
    for name, value in lock["appsynergy"]["files"].items():
        require(not Path(name).is_absolute() and ".." not in Path(name).parts, "source path")
        require(set(value) == {"sha256"} and HASH.fullmatch(value["sha256"]), "source hash")
    require(HASH.fullmatch(lock["kernel"]["sha256"]), "kernel hash")
    require(HASH.fullmatch(lock["kernel"]["signature_sha256"]), "signature hash")
    require(re.fullmatch(r"[0-9a-f]{128}", lock["kernel"]["blake2b"]), "kernel BLAKE2")
    require(lock["kernel"]["signers"] and all(re.fullmatch(r"[A-F0-9]{40}", s)
            for s in lock["kernel"]["signers"]), "kernel signer fingerprints")
    for patch in lock["kernel"]["patches"]:
        require(HASH.fullmatch(patch["sha256"]) and re.fullmatch(r"[0-9a-f]{128}", patch["blake2b"]), "patch hash")
        require(re.search(r"/[0-9a-f]{40}/", patch["url"]), "patch URL must pin commit")


def fetch(url, sha256, limit=512 * 1024**2):
    require(HASH.fullmatch(sha256), "download hash")
    parsed = urlsplit(url)
    require(parsed.scheme == "https" and parsed.hostname in {"github.com", "raw.githubusercontent.com", "geo.mirror.pkgbuild.com", "archive.archlinux.org"}
            and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,
            "download URL")
    directory = CACHE / "downloads"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / sha256
    require(not target.is_symlink(), "cache must not be a symlink")
    if target.exists():
        require(target.stat().st_size <= limit and digest(target) == sha256, "tampered cached input")
        return target
    with tempfile.NamedTemporaryFile(dir=directory, delete=False) as temporary:
        path = Path(temporary.name)
    try:
        subprocess.run(["curl", "--disable", "--fail", "--silent", "--show-error", "--location",
                        "--proto", "=https", "--proto-redir", "=https", "--connect-timeout", "20",
                        "--max-time", "600", "--max-filesize", str(limit), "--output", str(path), url],
                       check=True, timeout=620)
        require(path.stat().st_size <= limit and digest(path) == sha256, f"download hash mismatch: {url}")
        path.replace(target)
    finally:
        path.unlink(missing_ok=True)
    return target


def bootstrap():
    tools = load_json(ROOT / "sources/tools.lock.json")
    bindir = CACHE / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for name, entry in tools.items():
        require(re.fullmatch(r"[a-z]+", name), "tool name")
        archive = fetch(entry["url"], entry["sha256"])
        # Extract only a single regular executable; never extract archive paths.
        with tarfile.open(archive, "r:gz") as stream:
            member = stream.getmember(entry["member"])
            require(member.isfile() and member.size <= 128 * 1024**2, "tool archive member")
            with tempfile.NamedTemporaryFile(dir=bindir, delete=False) as output:
                temporary = Path(output.name)
                with stream.extractfile(member) as source:
                    while block := source.read(1024 * 1024):
                        output.write(block)
            temporary.chmod(0o755)
            temporary.replace(bindir / name)
        print(f"verified {name} {entry['version']}")


def prepare_sources():
    lock = load_json(ROOT / "sources/inputs.lock.json")
    validate_lock(lock)
    parent = lock["appsynergy"]
    directory = CACHE / "source-evidence" / parent["commit"]
    directory.mkdir(parents=True, exist_ok=True)
    for name, entry in parent["files"].items():
        sha256 = entry["sha256"]
        url = parent["repository"].replace("https://github.com/", "https://raw.githubusercontent.com/").removesuffix(".git")
        cached = fetch(f"{url}/{parent['commit']}/{name}", sha256, 4 * 1024**2)
        output = directory / name
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(cached.read_bytes())
    kernel = lock["kernel"]
    archive = fetch(kernel["url"], kernel["sha256"])
    h = hashlib.blake2b()
    with archive.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    require(h.hexdigest() == kernel["blake2b"], "upstream kernel BLAKE2 mismatch")
    signature = fetch(kernel["url"] + ".asc", kernel["signature_sha256"], 64 * 1024)
    with tempfile.TemporaryDirectory(prefix="gnupg-", dir=CACHE) as home:
        os.chmod(home, 0o700)
        gpg = ["gpg", "--no-options", "--homedir", home, "--batch", "--no-auto-key-retrieve"]
        subprocess.run([*gpg, "--import", str(directory / "kernel/upstream/cachyos-source-keys.asc")], check=True, timeout=30)
        result = subprocess.run([*gpg, "--status-fd", "1", "--verify", str(signature), str(archive)],
                                check=True, capture_output=True, text=True, timeout=60)
        signatures = [line.split() for line in result.stdout.splitlines() if line.startswith("[GNUPG:] VALIDSIG ")]
        require(any(fields[2] in kernel["signers"] or fields[-1] in kernel["signers"]
                    for fields in signatures), "unapproved source signer")
    for patch in kernel["patches"]:
        data = fetch(patch["url"], patch["sha256"], 4 * 1024**2).read_bytes()
        require(hashlib.blake2b(data).hexdigest() == patch["blake2b"], "upstream patch BLAKE2 mismatch")
    print(f"Verified kernel source and lineage: {directory}")
    return archive, directory
