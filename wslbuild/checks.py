"""Validation stages used without alteration by CI and scripts/check."""

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .changes import event_range
from .evidence import load_json, require
from .inputs import ROOT, validate_lock
from .policy import lint_commit


def source_checks():
    for directory in ("wslbuild", "scripts", "tests", "kernel"):
        for path in (ROOT / directory).iterdir():
            if not path.is_file():
                continue
            with path.open(encoding="utf-8") as stream:
                first = stream.readline()
            if path.suffix == ".py" or (first.startswith("#!") and "python" in first):
                ast.parse(path.read_text(), filename=str(path.relative_to(ROOT)), feature_version=(3, 11))
    validate_lock(load_json(ROOT / "sources/inputs.lock.json"))
    from .stable import verify_metadata
    verify_metadata()
    lines = (ROOT / "kernel/wsl.config").read_text().splitlines()
    values = {}
    for line in lines:
        if line.startswith("CONFIG_"):
            key, value = line.split("=", 1)
            require(key not in values, f"duplicate configuration: {key}")
            values[key] = value
    for symbol in ("HYPERV", "HYPERV_VMBUS", "HYPERV_STORAGE", "HYPERV_NET", "HYPERV_VSOCKETS",
                   "EXT4_FS", "OVERLAY_FS", "VIRTIO_FS", "9P_FS", "LTO_CLANG_THIN", "FORTIFY_SOURCE", "MODULES", "CGROUPS", "USER_NS"):
        require(values.get("CONFIG_" + symbol) == "y", f"required built-in: {symbol}")
    require(values.get("CONFIG_X86_64_VERSION") == "3", "x86-64-v3 required")
    require("# CONFIG_X86_NATIVE_CPU is not set" in lines, "native CPU selection prohibited")
    upstream = load_json(ROOT / "sources/upstream-evidence.lock.json")
    require(upstream["commit"] == load_json(ROOT / "sources/inputs.lock.json")["cachyos"]["commit"], "upstream evidence commit")
    for entry in upstream["files"].values():
        require(re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) and upstream["commit"] in entry["url"], "upstream evidence pin")
    signing = load_json(ROOT / "sources/signing.lock.json")
    from .evidence import digest
    require(digest(ROOT / "sources/wsl-integration-signing.asc") == signing["integration_public_sha256"], "integration public key hash")
    require(digest(ROOT / "windows/trust/release.pem") == signing["manifest_public_sha256"], "manifest public key hash")
    packages = load_json(ROOT / "sources/packages.lock.json")
    require(re.fullmatch(r"docker.io/library/archlinux:base-devel@sha256:[0-9a-f]{64}", packages["image"]), "builder image pin")
    for group in packages["sets"].values():
        require(len(group["packages"]) == len({p["name"] for p in group["packages"]}), "duplicate package input")
        for package in group["packages"]:
            require(re.fullmatch(r"[0-9a-f]{64}", package["sha256"]) and re.fullmatch(r"[0-9a-f]{64}", package["signature_sha256"]), "package hash pin")
            require(package["url"].startswith("https://geo.mirror.pkgbuild.com/") and package["version"], "package origin/version")


def documentation():
    require((ROOT / "CLAUDE.md").read_bytes() == b"@AGENTS.md\n", "CLAUDE.md must be exact import")
    require(len((ROOT / "AGENTS.md").read_text().splitlines()) < 200, "AGENTS.md must be under 200 lines")
    for name in ("README.md", "AGENTS.md"):
        content = (ROOT / name).read_text()
        for link in re.findall(r"\]\(([^)]+)\)", content):
            if "://" not in link and not link.startswith("#"):
                require((ROOT / link.split("#")[0]).is_file(), f"broken local link: {link}")
    require((ROOT / "README.md").read_text().count("```") % 2 == 0, "unclosed README code fence")
    from .evidence import digest
    branding = load_json(ROOT / "sources/inputs.lock.json")["appsynergy"]["files"]
    require(digest(ROOT / "assets/appsynergy.svg") == branding["packages/pkgbuilds/appsynergy-branding-desktop/icons/hicolor/scalable/apps/appsynergy-linux.svg"]["sha256"], "approved branding bytes changed")
    for element in re.findall(r"<img\b[^>]*>", (ROOT / "README.md").read_text()):
        source = re.search(r'\bsrc="([^"]+)"', element)
        alt = re.search(r'\balt="([^"]+)"', element)
        require(source and alt, "README images require source and accessible alternative text")
        if "://" not in source[1]:
            require((ROOT / source[1]).is_file(), "missing README image")


def commits(base=None):
    if not subprocess.run(["git", "rev-parse", "--verify", "HEAD"], capture_output=True).returncode == 0:
        print("Commit history: not applicable (unborn repository)")
        return
    head = "HEAD"
    if os.environ.get("GITHUB_EVENT_PATH"):
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as stream:
            event = json.load(stream)
        base, head, _ = event_range(event, os.environ["GITHUB_EVENT_NAME"])
        if os.environ["GITHUB_EVENT_NAME"] == "pull_request":
            pr = event["pull_request"]
            lint_commit(pr["title"] + "\n\n" + (pr.get("body") or ""))
    if base and base != "0" * 40:
        # Fail if the requested range cannot be resolved; never silently lint less.
        subprocess.run(["git", "rev-parse", "--verify", f"{base}^{{commit}}"], check=True, capture_output=True)
        revision = f"{base}..{head}"
    else:
        revision = head
    from .policy import SKIP
    records = subprocess.check_output(["git", "log", "--format=%H%x00%P%x00%B%x00", revision], timeout=30).decode().split("\0")
    for index in range(0, len(records) - 2, 3):
        sha, parents, message = (part.strip() for part in records[index:index + 3])
        require(not SKIP.search(message), "native CI-skip token forbidden")
        synthetic_merge = os.environ.get("GITHUB_EVENT_NAME") == "merge_group" and sha == os.environ.get("GITHUB_SHA") and len(parents.split()) > 1
        if not synthetic_merge:
            lint_commit(message)


def secrets():
    # Scan every source file, including untracked files, but not reusable build caches.
    with tempfile.TemporaryDirectory(prefix="wsl-secret-scan-") as temp:
        snapshot = Path(temp) / "source"
        shutil.copytree(ROOT, snapshot, ignore=shutil.ignore_patterns(".git", ".cache", "dist", "__pycache__", "*.pyc"))
        subprocess.run(["gitleaks", "dir", "--redact", "--no-banner", str(snapshot)], check=True, timeout=120)
    if subprocess.run(["git", "rev-parse", "--verify", "HEAD"], capture_output=True).returncode == 0:
        subprocess.run(["gitleaks", "git", "--redact", "--no-banner", "--log-opts=--all", str(ROOT)], check=True, timeout=120)


def package_check():
    require(shutil.which("docker"), "Docker prerequisite missing")
    image = load_json(ROOT / "sources/package-builder.lock.json")["image"]
    require(re.fullmatch(r"docker.io/library/archlinux:base-devel@sha256:[0-9a-f]{64}", image), "builder digest")
    subprocess.run(["docker", "pull", image], check=True, timeout=300)
    # Exactly the integration/package-database checks used by CI. Both offline
    # builders receive only their public inputs, never signing credentials.
    from .rootfs import integration
    # A gate must never overwrite an existing separately signed build artifact.
    with tempfile.TemporaryDirectory(prefix="wsl-package-check-") as temp:
        integration(Path(temp))


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["sources", "docs", "commits", "secrets", "package"])
    parser.add_argument("--base")
    args = parser.parse_args()
    if args.stage == "commits":
        commits(args.base)
    else:
        {"sources": source_checks, "docs": documentation, "secrets": secrets, "package": package_check}[args.stage]()


if __name__ == "__main__":
    main()
