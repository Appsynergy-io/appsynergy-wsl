"""Manifest construction and authenticated trusted-main candidate retrieval."""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from .build import input_hash, product_files
from .evidence import ARTIFACTS, digest, load_json, require, validate_acceptance, validate_manifest
from .inputs import CACHE, ROOT, prepare_sources, fetch
from .policy import proposed_version

REPOSITORY = "Appsynergy-io/appsynergy-wsl"


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True, timeout=30).strip()


def version_at(source):
    tags = git("tag", "--merged", source, "--list", "appsynergy-wsl-v*").splitlines()
    parsed = [(tuple(map(int, t.removeprefix("appsynergy-wsl-v").split("."))), t) for t in tags
              if re.fullmatch(r"appsynergy-wsl-v\d+\.\d+\.\d+", t)]
    last = max(parsed)[1] if parsed else None
    revision = f"{last}..{source}" if last else source
    messages = []
    docs = {"README.md", "AGENTS.md", "CLAUDE.md", "assets/appsynergy.svg"}
    for commit in git("log", "--no-merges", "--format=%H", revision).splitlines():
        paths = git("diff-tree", "--root", "--no-commit-id", "--name-only", "--no-renames", "-r", commit).splitlines()
        if not any(p not in docs and not p.startswith(("acceptance/", "tests/", ".github/")) for p in paths):
            continue
        message = git("show", "-s", "--format=%B", commit)
        if message.startswith(("docs:", "docs(", "docs!:", "ci:", "ci(", "ci!:", "test:", "test(", "test!:")):
            continue
        messages.append(message)
    return proposed_version(tags, messages)


def candidate_version(source):
    value = version_at(source)
    if value is not None:
        return value
    tags = git("tag", "--merged", source, "--list", "appsynergy-wsl-v*").splitlines()
    return ".".join(map(str, max(tuple(map(int, t.removeprefix("appsynergy-wsl-v").split("."))) for t in tags)))


def assemble(kernel_directory, rootfs_directory, package_directory, output):
    source = git("rev-parse", "HEAD")
    require(not git("status", "--porcelain", "--untracked-files=normal"), "candidate assembly requires a committed clean source tree")
    version = candidate_version(source)
    trusted_main = os.environ.get("GITHUB_EVENT_NAME") == "push" and os.environ.get("GITHUB_REF") == "refs/heads/main"
    if trusted_main:
        require(os.environ.get("GITHUB_REPOSITORY") == REPOSITORY and os.environ.get("GITHUB_SHA") == source, "CI source identity")
    integration_packages = list(Path(package_directory).glob("*.pkg.tar.zst"))
    require(len(integration_packages) == 1, "one integration package required")
    integration_package = integration_packages[0]
    from .rootfs import verify_rootfs_inputs
    verify_rootfs_inputs(rootfs_directory, input_hash(), integration_package, trusted_main)
    build = {"repository": REPOSITORY, "event": os.environ.get("GITHUB_EVENT_NAME", "local"),
             "ref": os.environ.get("GITHUB_REF", "local"), "run_id": int(os.environ.get("GITHUB_RUN_ID", "0")),
             "run_attempt": int(os.environ.get("GITHUB_RUN_ATTEMPT", "0"))}
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    require(not list(output.iterdir()), "candidate output directory must be empty")
    from .build import kernel_input_hash
    kernel_record = load_json(Path(kernel_directory) / "kernel-inputs.json")
    require(kernel_record["inputs_sha256"] == kernel_input_hash(), "kernel inputs changed; reuse rejected")
    for name, entry in kernel_record["files"].items():
        require("/" not in name and "\\" not in name and digest(Path(kernel_directory) / name) == entry["sha256"], "kernel artifact reuse hash mismatch")
    kernel_directory, rootfs_directory = Path(kernel_directory), Path(rootfs_directory)
    for name in ("kernel", "modules.vhdx"):
        shutil.copyfile(kernel_directory / name, output / name)
    shutil.copyfile(rootfs_directory / "rootfs.tar.zst", output / "rootfs.tar.zst")
    with zipfile.ZipFile(output / "tools.zip", "w", compression=zipfile.ZIP_DEFLATED) as stream:
        for relative, path in product_files():
            if relative.parts[0] == "windows" or str(relative) == "LICENSE":
                info = zipfile.ZipInfo(str(relative), (2026, 10, 25, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                stream.writestr(info, path.read_bytes())
    inventory = {"integration": {"filename": integration_package.name, "sha256": digest(integration_package),
                                  "signature_sha256": digest(Path(str(integration_package) + ".sig"))}, "schema": 1, "source": source, "version": version, "inputs_sha256": input_hash(),
                 "build": build, "source_lock": load_json(ROOT / "sources/inputs.lock.json"),
                 "package_lock": load_json(ROOT / "sources/packages.lock.json"),
                 "kernel_release": (kernel_directory / "kernel-release.txt").read_text().strip(),
                 "kernel_config_sha256": digest(kernel_directory / "kernel.config"),
                 "wsl2_configuration": load_json(kernel_directory / "wsl2-compatibility.json"),
                 "kernel_build_parameters": load_json(kernel_directory / "build-parameters.json"),
                 "stable_kernel_backport": load_json(ROOT / "kernel/stable.lock.json"),
                 "rootfs_build_inputs": load_json(rootfs_directory / "rootfs-inputs.json"),
                 "toolchain": (kernel_directory / "toolchain-inventory.txt").read_text().splitlines(),
                 "userspace": (rootfs_directory / "rootfs-inventory.txt").read_text().splitlines()}
    (output / "inventory.json").write_text(json.dumps(inventory, indent=2) + "\n")
    archive, evidence = prepare_sources()
    with tempfile.TemporaryDirectory(prefix="wsl-source-evidence-") as temp:
        bundle = Path(temp)
        shutil.copytree(evidence, bundle / "appsynergy")
        shutil.copyfile(archive, bundle / "cachyos-7.2.3-2.tar.gz")
        lock = load_json(ROOT / "sources/inputs.lock.json")
        shutil.copyfile(CACHE / "downloads" / lock["kernel"]["signature_sha256"], bundle / "cachyos-7.2.3-2.tar.gz.asc")
        # A URL and hash alone are not the corresponding patched source. Ship
        # every applied patch alongside the authenticated original source tar.
        (bundle / "kernel-patches").mkdir()
        for index, patch in enumerate(lock["kernel"]["patches"], 1):
            shutil.copyfile(fetch(patch["url"], patch["sha256"], 4 * 1024**2),
                            bundle / "kernel-patches" / f"{index:04d}.patch")
        (bundle / "kernel-patches/provenance.json").write_text(json.dumps(lock["kernel"]["patches"], indent=2) + "\n")
        from .stable import prepare_stable_sources
        _, stable_lock = prepare_stable_sources()
        shutil.copyfile(fetch(stable_lock["upstream_delta"]["url"], stable_lock["upstream_delta"]["sha256"], 32 * 1024**2),
                        bundle / "kernel-patches/upstream-stable.diff")
        upstream = load_json(ROOT / "sources/upstream-evidence.lock.json")
        (bundle / "cachyos-recipe").mkdir()
        for name, entry in upstream["files"].items():
            require(name in {"PKGBUILD", "config"}, "upstream evidence filename")
            shutil.copyfile(fetch(entry["url"], entry["sha256"]), bundle / "cachyos-recipe" / name)
        for relative, path in product_files():
            target = bundle / "wsl" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        for name in ("kernel.config", "kernel-release.txt", "kernel-inputs.json", "kernel-checksums.txt", "System.map", "module-signing-cert.x509", "build-parameters.json", "toolchain-inventory.txt", "wsl2-compatibility.json"):
            shutil.copyfile(kernel_directory / name, bundle / name)
        image = load_json(ROOT / "sources/package-builder.lock.json")["image"]
        subprocess.run(["docker", "run", "--rm", "--network=none", "--user", f"{os.getuid()}:{os.getgid()}",
                        "--mount", f"type=bind,src={bundle},dst=/source,readonly", "--mount",
                        f"type=bind,src={output},dst=/output", image, "tar", "--zstd", "--sort=name",
                        "--mtime=2026-10-25 00:00:00Z", "-cf", "/output/source.tar.zst", "-C", "/source", "."],
                       check=True, timeout=300)
    entries = {name: {"sha256": digest(output / name), "size": (output / name).stat().st_size} for name in sorted(ARTIFACTS)}
    manifest = {"schema": 1, "source": source, "inputs_sha256": input_hash(), "version": version, "build": build, "artifacts": entries}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    for package in Path(package_directory).glob("*.pkg.tar.zst*"):
        require(re.fullmatch(r"appsynergy-wsl-integration-[0-9.]+-\d+-any\.pkg\.tar\.zst(?:\.sig)?", package.name), "package filename")
        shutil.copyfile(package, output / package.name)
    (output / "SHA256SUMS").write_text("".join(f"{digest(p)}  {p.name}\n" for p in sorted(output.iterdir()) if p.is_file()))
    print(f"Candidate {version} assembled without release signing. Windows acceptance NOT established.")


def api(path):
    return json.loads(subprocess.check_output(["gh", "api", path], text=True, timeout=60))


def retrieve(report):
    build = report["build"]
    require(type(build["run_id"]) is int and build["run_id"] > 0 and type(build["run_attempt"]) is int
            and build["run_attempt"] > 0, "build IDs")
    run_id, attempt = build["run_id"], build["run_attempt"]
    run = api(f"repos/{REPOSITORY}/actions/runs/{run_id}/attempts/{attempt}")
    workflow = api(f"repos/{REPOSITORY}/actions/workflows/ci.yml")
    require(run["workflow_id"] == workflow["id"] and run["event"] == "push" and run["head_branch"] == "main"
            and run["conclusion"] == "success" and run["head_sha"] == report["source"]
            and run["repository"]["full_name"] == REPOSITORY, "trusted-main run unavailable or unsuccessful")
    artifacts = api(f"repos/{REPOSITORY}/actions/runs/{run_id}/artifacts?per_page=100")["artifacts"]
    name = f"candidate-{report['source']}-{attempt}"
    matches = [a for a in artifacts if a["name"] == name and not a["expired"]]
    require(len(matches) == 1 and matches[0]["size_in_bytes"] <= 8 * 1024**3, "candidate artifact unavailable or ambiguous")
    output = CACHE / "trusted-candidates" / f"{run_id}-{attempt}"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="wsl-retrieve-") as temp:
        archive = Path(temp) / "candidate.zip"
        with archive.open("wb") as stream:
            subprocess.run(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{matches[0]['id']}/zip"],
                           stdout=stream, check=True, timeout=600)
        expected_digest = matches[0].get("digest")
        require(expected_digest and expected_digest == "sha256:" + digest(archive), "GitHub artifact digest mismatch")
        with zipfile.ZipFile(archive) as stream:
            require(len(stream.infolist()) <= 16 and sum(m.file_size for m in stream.infolist()) <= 16 * 1024**3, "artifact extraction limit")
            names = set()
            for member in stream.infolist():
                require(member.filename not in names and "/" not in member.filename and "\\" not in member.filename
                        and member.filename not in {"", ".", ".."}, "artifact path or duplicate")
                names.add(member.filename)
                allowed = set(ARTIFACTS) | {"manifest.json", "manifest.sig", "SHA256SUMS"}
                require(member.filename in allowed or re.fullmatch(r"appsynergy-wsl-integration-[0-9.]+-\d+-any\.pkg\.tar\.zst(?:\.sig)?", member.filename), "unexpected candidate file")
                require((member.external_attr >> 16) & 0o170000 != 0o120000, "artifact symlink")
                require(member.file_size <= 32 * 1024**3, "artifact size")
            stream.extractall(output)
    manifest = load_json(output / "manifest.json")
    trusted = {"source": run["head_sha"], "build": {"repository": REPOSITORY, "event": "push", "ref": "refs/heads/main",
                                                   "run_id": run_id, "run_attempt": attempt},
               "inputs_sha256": input_hash(), "version": manifest["version"]}
    validate_manifest(manifest, output, trusted)
    subprocess.run(["openssl", "dgst", "-sha256", "-verify", str(ROOT / "windows/trust/release.pem"),
                    "-signature", str(output / "manifest.sig"), str(output / "manifest.json")], check=True, timeout=30)
    validate_acceptance(report, manifest, digest(output / "manifest.json"), input_hash())
    return output, manifest


def acceptance():
    records = sorted((ROOT / "acceptance").glob("*.json"))
    require(len(records) == 1, "exactly one current acceptance record required")
    report = load_json(records[0])
    output, manifest = retrieve(report)
    source = manifest["source"]
    subprocess.run(["git", "merge-base", "--is-ancestor", source, "HEAD"], check=True, timeout=30)
    paths = git("diff", "--name-only", "--no-renames", source, "HEAD", "--").splitlines()
    require(all(p.startswith("acceptance/") and p.endswith(".json") for p in paths), "acceptance promotion must change no product inputs")
    proposed = version_at(source)
    tag = f"appsynergy-wsl-v{manifest['version']}"
    existing = git("tag", "--points-at", source).splitlines()
    require(proposed == manifest["version"] or tag in existing, "stale proposed version")
    print(f"PASS maintainer acceptance for exact trusted candidate {source}: {output}")
    return output, manifest, records[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["assemble", "acceptance", "version"])
    parser.add_argument("--kernel")
    parser.add_argument("--rootfs")
    parser.add_argument("--package")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.operation == "acceptance":
        acceptance()
    elif args.operation == "version":
        print(candidate_version("HEAD"))
    elif all((args.kernel, args.rootfs, args.package, args.output)):
        assemble(args.kernel, args.rootfs, args.package, args.output)
    else:
        parser.error("assemble requires --kernel --rootfs --package --output")
