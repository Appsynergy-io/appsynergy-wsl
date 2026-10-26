"""Isolated unsigned candidate construction. No signing credentials are consumed."""

import concurrent.futures
import hashlib
import os
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from .evidence import digest, load_json, require
from .inputs import CACHE, ROOT, fetch, prepare_sources


def product_files():
    excluded = {".git", ".cache", "dist", "__pycache__"}
    docs = {"README.md", "AGENTS.md", "CLAUDE.md", "CLAUDE.local.md", "assets/appsynergy.svg"}
    for directory, dirs, names in os.walk(ROOT):
        dirs[:] = sorted(d for d in dirs if d not in excluded and not (Path(directory) == ROOT and d == "acceptance"))
        for name in sorted(names):
            path = Path(directory) / name
            relative = path.relative_to(ROOT)
            if str(relative) in docs or name.endswith(".pyc"):
                continue
            require(not path.is_symlink(), f"product input must not be symlink: {relative}")
            yield relative, path


def input_hash():
    h = hashlib.sha256()
    for relative, path in product_files():
        h.update(str(relative).encode() + b"\0" + str(path.stat().st_mode & 0o111).encode() + b"\0")
        h.update(bytes.fromhex(digest(path)))
    return h.hexdigest()


def package_inputs(kind):
    lock = load_json(ROOT / "sources/packages.lock.json")
    directory = CACHE / "packages" / kind
    directory.mkdir(parents=True, exist_ok=True)

    def get(entry):
        require(entry["url"].startswith("https://geo.mirror.pkgbuild.com/"), "package origin")
        name = Path(urlsplit(entry["url"]).path).name
        require(name.endswith(".pkg.tar.zst") and "/" not in name, "package filename")
        archive_url = f"https://archive.archlinux.org/packages/{entry['name'][0]}/{entry['name']}/{name}"
        def download(url, fallback, sha256, maximum):
            try:
                return fetch(url, sha256, maximum)
            except subprocess.CalledProcessError:
                return fetch(fallback, sha256, maximum)
        archive = download(entry["url"], archive_url, entry["sha256"], 1024**3)
        signature = download(entry["url"] + ".sig", archive_url + ".sig", entry["signature_sha256"], 64 * 1024)
        shutil.copyfile(archive, directory / name)
        shutil.copyfile(signature, directory / (name + ".sig"))

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(get, lock["sets"][kind]["packages"]))
    expected = {Path(urlsplit(e["url"]).path).name + suffix
                for e in lock["sets"][kind]["packages"] for suffix in ("", ".sig")}
    require({p.name for p in directory.iterdir()} == expected, "unexpected cached package")
    return lock["image"], directory


def extract_kernel(archive, output):
    # data_filter rejects traversal, devices, and symlinks escaping the destination.
    with tarfile.open(archive, "r:gz") as stream:
        entries = []
        total = 0
        for entry in stream:
            total += entry.size
            require(len(entries) < 200000 and total < 8 * 1024**3, "kernel extraction limit")
            require(entry.name.split("/")[0] == "cachyos-7.2.3-2", "kernel archive prefix")
            entries.append(entry)
        stream.extractall(output, members=entries, filter="data")


def kernel_input_hash():
    h = hashlib.sha256()
    for relative, path in product_files():
        if relative.parts[0] == "kernel" or str(relative) in {"wslbuild/build.py", "wslbuild/inputs.py", "wslbuild/stable.py", "wslbuild/evidence.py", "wslbuild/policy.py", "build/install-packages", "sources/inputs.lock.json", "sources/packages.lock.json"}:
            h.update(str(relative).encode() + b"\0" + bytes.fromhex(digest(path)))
    return h.hexdigest()


def kernel():
    identity = kernel_input_hash()
    work = CACHE / "kernel" / identity
    work.mkdir(parents=True, exist_ok=True)
    receipt = work / "output/kernel-inputs.json"
    if receipt.is_file():
        record = load_json(receipt)
        require(record.get("schema") == 1 and record["inputs_sha256"] == identity, "cached kernel inputs mismatch")
        require(set(record["files"]) == {"kernel", "modules.vhdx", "kernel.config", "System.map", "kernel-release.txt", "kernel-checksums.txt", "toolchain-inventory.txt", "wsl2-compatibility.json", "build-parameters.json", "module-signing-cert.x509"}, "cached kernel inventory")
        for name, entry in record["files"].items():
            path = work / "output" / name
            require(not path.is_symlink() and path.is_file() and digest(path) == entry["sha256"] and path.stat().st_size == entry["size"], "tampered cached kernel artifact")
        print(f"Verified identical-input kernel artifact reused: {work / 'output'}")
        return work / "output"
    require(shutil.disk_usage(CACHE).free > 40 * 1024**3, "kernel build requires 40 GiB free (28 GiB measured compiler tree plus assembly/toolchain reserve)")
    archive, evidence = prepare_sources()
    from .stable import prepare_stable_sources
    stable_patch, stable_lock = prepare_stable_sources()
    image, packages = package_inputs("toolchain")
    inputs = CACHE / "kernel-inputs" / identity
    inputs.mkdir(exist_ok=True, parents=True)
    lock = load_json(ROOT / "sources/inputs.lock.json")
    shutil.copyfile(evidence / "kernel/upstream/config-7.2.3-1-appsynergy-linux", inputs / "appsynergy.config")
    for name in ("wsl.config", "compile", "verify-config", "wsl2-contract.json"):
        shutil.copyfile(ROOT / "kernel" / name, inputs / name)
    shutil.copyfile(ROOT / "build/install-packages", inputs / "install-packages")
    shutil.copyfile(stable_patch, inputs / "stable.patch")
    shutil.copyfile(ROOT / "kernel/patches/gud-tv-mode-bounds.patch", inputs / "gud-tv-mode-bounds.patch")
    (inputs / "kernel-version").write_text(stable_lock["version"] + "\n")
    patch = lock["kernel"]["patches"][0]
    shutil.copyfile(fetch(patch["url"], patch["sha256"]), inputs / "dkms-clang.patch")
    if not (work / "linux").exists():
        extract_kernel(archive, work)
        (work / "cachyos-7.2.3-2").rename(work / "linux")
    subprocess.run(["docker", "pull", image], check=True, timeout=300)
    name = "appsynergy-kernel-" + uuid.uuid4().hex
    jobs = min(os.cpu_count() or 2, 12)
    command = ["docker", "run", "--name", name, "--rm", "--init", "--network=none", "--pids-limit=4096",
               "--memory=12g", f"--cpus={jobs}", "--env", f"BUILD_JOBS={jobs}", "--security-opt=no-new-privileges",
               "--mount", f"type=bind,src={packages},dst=/packages,readonly",
               "--mount", f"type=bind,src={inputs},dst=/inputs,readonly",
               "--mount", f"type=bind,src={work},dst=/work", image, "bash", "-c",
               'bash /inputs/install-packages && exec setpriv --reuid="$1" --regid="$2" --clear-groups '
               'env HOME=/tmp PATH=/usr/bin bash /inputs/compile', "builder", str(os.getuid()), str(os.getgid())]
    log = work / "build.log"
    print(f"Kernel input identity: {identity}\nDetailed build log: {log}", flush=True)
    try:
        with log.open("w") as stream:
            subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=7200)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
    require(kernel_input_hash() == identity, "inputs changed during compilation; candidate rejected")
    print(f"Kernel candidate compiled: {work / 'output'}. Windows acceptance is NOT established.")
    return work / "output"


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=["kernel"])
    parser.parse_args()
    kernel()
