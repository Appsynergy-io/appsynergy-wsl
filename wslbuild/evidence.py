"""Validate bytes and maintainer reports against separately trusted build data.

This module does not establish GitHub provenance or authenticate a manifest.
Callers must verify a signature and retrieve trusted run metadata independently.
"""

import hashlib
import json
import re
from pathlib import Path

from .policy import HASH, SHA, SCENARIOS

MAX_JSON = 1024 * 1024
ARTIFACTS = frozenset({"rootfs.tar.zst", "kernel", "modules.vhdx", "tools.zip",
                       "inventory.json", "source.tar.zst"})


def reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_JSON + 1)
    if len(data) > MAX_JSON:
        raise ValueError("JSON exceeds 1 MiB")
    return json.loads(data, object_pairs_hook=reject_duplicates)


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_manifest(manifest, directory, trusted):
    """trusted must come from an authenticated trusted-main run, not the report."""
    require(isinstance(manifest, dict) and manifest.get("schema") == 1, "manifest schema")
    require(set(manifest) == {"schema", "source", "inputs_sha256", "version", "build", "artifacts"},
            "manifest fields")
    require(isinstance(manifest["source"], str) and SHA.fullmatch(manifest["source"]), "source SHA")
    require(isinstance(manifest["inputs_sha256"], str) and HASH.fullmatch(manifest["inputs_sha256"]),
            "input hash")
    require(re.fullmatch(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", manifest["version"]), "version")
    build = manifest["build"]
    require(build == trusted["build"], "untrusted build identity")
    require(set(build) == {"repository", "event", "ref", "run_id", "run_attempt"}, "build fields")
    require(build["repository"] == "Appsynergy-io/appsynergy-wsl" and build["event"] == "push"
            and build["ref"] == "refs/heads/main", "candidate must come from trusted main")
    require(type(build["run_id"]) is int and build["run_id"] > 0
            and type(build["run_attempt"]) is int and build["run_attempt"] > 0, "build IDs")
    for key in ("source", "inputs_sha256", "version"):
        require(manifest[key] == trusted[key], f"trusted {key} mismatch")
    entries = manifest["artifacts"]
    require(isinstance(entries, dict) and set(entries) == ARTIFACTS, "artifact inventory")
    root = Path(directory).resolve()
    for name, entry in entries.items():
        require(isinstance(entry, dict) and set(entry) == {"sha256", "size"}, "artifact fields")
        require(isinstance(entry["sha256"], str) and HASH.fullmatch(entry["sha256"]), "artifact hash")
        require(type(entry["size"]) is int and 0 < entry["size"] <= 32 * 1024**3, "artifact size")
        path = root / name
        require(not path.is_symlink() and path.is_file(), f"unavailable artifact: {name}")
        require(path.stat().st_size == entry["size"], f"size mismatch: {name}")
        require(digest(path) == entry["sha256"], f"hash mismatch: {name}")


def validate_acceptance(report, manifest, manifest_hash, current_inputs_hash):
    require(isinstance(report, dict) and report.get("schema") == 1, "acceptance schema")
    require(set(report) == {"schema", "kind", "source", "build", "manifest_sha256", "inputs_sha256",
                           "artifacts", "host", "scenarios", "reboot", "maintainer"}, "report fields")
    require(report["kind"] == "maintainer-acceptance", "not independent attestation")
    for key in ("source", "build", "inputs_sha256", "artifacts"):
        require(report[key] == manifest[key], f"acceptance {key} mismatch")
    require(report["manifest_sha256"] == manifest_hash, "manifest bytes changed")
    require(current_inputs_hash == manifest["inputs_sha256"], "product inputs changed")
    host = report["host"]
    require(isinstance(host, dict) and set(host) == {"edition", "build", "wsl_version", "architecture",
                                                  "cpu_baseline", "kernel_release"}, "host fields")
    require(host["edition"] == "Enterprise" and host["architecture"] == "x64", "Windows target")
    require(type(host["build"]) is int and host["build"] >= 22000, "Windows 11 build")
    require(host["cpu_baseline"] == "x86-64-v3", "CPU baseline")
    require(isinstance(host["wsl_version"], str) and bool(host["wsl_version"].strip()), "WSL version")
    require(isinstance(host["kernel_release"], str) and host["kernel_release"].endswith("-appsynergy-wsl"),
            "kernel identity")
    require(isinstance(report["maintainer"], str) and bool(report["maintainer"].strip()), "maintainer")
    scenarios = report["scenarios"]
    require(isinstance(scenarios, dict) and set(scenarios) == SCENARIOS, "missing or unexpected scenario")
    for name, result in scenarios.items():
        require(isinstance(result, dict) and set(result) == {"result", "diagnostic"}, f"scenario fields: {name}")
        require(result["result"] == "PASS" and isinstance(result["diagnostic"], str)
                and 0 < len(result["diagnostic"]) <= 8192, f"scenario incomplete: {name}")
    reboot = report["reboot"]
    require(isinstance(reboot, dict) and set(reboot) == {"before_boot_id", "after_boot_id", "resumed"}, "reboot fields")
    require(reboot["resumed"] is True and isinstance(reboot["before_boot_id"], str)
            and isinstance(reboot["after_boot_id"], str) and bool(reboot["before_boot_id"])
            and bool(reboot["after_boot_id"]) and reboot["before_boot_id"] != reboot["after_boot_id"],
            "reboot was not completed")
