"""Reviewed controlled-builder inputs; CI verifies bytes without compiling."""

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .build import kernel_input_hash, product_files
from .evidence import digest, load_json, require
from .inputs import CACHE, ROOT, fetch
from .policy import HASH, SHA

FILES = frozenset({"kernel", "modules.vhdx", "kernel.config", "System.map", "kernel-release.txt",
                   "kernel-checksums.txt", "toolchain-inventory.txt", "wsl2-compatibility.json",
                   "build-parameters.json", "module-signing-cert.x509"})
CORE = {"wslbuild/build.py", "wslbuild/inputs.py", "wslbuild/stable.py", "wslbuild/evidence.py",
        "wslbuild/policy.py", "build/install-packages", "sources/inputs.lock.json", "sources/packages.lock.json"}
LOCK = ROOT / "sources/kernel-artifact.lock.json"
REPOSITORY = "Appsynergy-io/appsynergy-wsl"


def validate_lock(lock):
    require(set(lock) == {"schema", "inputs_sha256", "recipe_commit", "artifact"} and lock["schema"] == 1,
            "kernel artifact lock schema")
    require(HASH.fullmatch(lock["inputs_sha256"]) and SHA.fullmatch(lock["recipe_commit"]), "kernel artifact source pins")
    artifact = lock["artifact"]
    identity = lock["inputs_sha256"]
    require(set(artifact) == {"url", "sha256", "size", "receipt_sha256"} and HASH.fullmatch(artifact["sha256"])
            and HASH.fullmatch(artifact["receipt_sha256"]), "kernel artifact hash pin")
    require(type(artifact["size"]) is int and 0 < artifact["size"] <= 512 * 1024**2, "kernel artifact size")
    require(artifact["url"] == f"https://github.com/{REPOSITORY}/releases/download/kernel-build-{identity}/kernel-{identity}.tar.gz",
            "kernel artifact immutable URL")


def verify_recipe(commit):
    """Prove the recorded commit has precisely the current kernel input bytes."""
    require(SHA.fullmatch(commit), "recipe full commit pin")
    paths = [str(relative) for relative, _ in product_files()
             if relative.parts[0] == "kernel" or str(relative) in CORE]
    tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", commit], cwd=ROOT, text=True, timeout=30).splitlines()
    previous = {p for p in tracked if p.startswith("kernel/") or p in CORE}
    require(previous == set(paths), "recipe kernel input inventory changed")
    value = hashlib.sha256()
    for name in paths:
        content = subprocess.check_output(["git", "show", f"{commit}:{name}"], cwd=ROOT, timeout=30)
        value.update(name.encode() + b"\0" + hashlib.sha256(content).digest())
    require(value.hexdigest() == kernel_input_hash(), "recipe commit kernel inputs changed")


def verify_directory(directory, identity, builder=None, receipt_sha256=None):
    directory = Path(directory)
    require(directory.is_dir() and not directory.is_symlink(), "kernel artifact directory invalid")
    require({p.name for p in directory.iterdir()} == FILES | {"kernel-inputs.json"}, "kernel artifact inventory")
    require((directory / "kernel-inputs.json").is_file() and not (directory / "kernel-inputs.json").is_symlink(),
            "kernel receipt must be regular")
    if receipt_sha256 is not None:
        require(digest(directory / "kernel-inputs.json") == receipt_sha256, "kernel receipt hash mismatch")
    record = load_json(directory / "kernel-inputs.json")
    require(record.get("schema") == 1 and record.get("inputs_sha256") == identity, "kernel artifact inputs changed")
    require(set(record["files"]) == FILES, "kernel receipt inventory")
    for name, entry in record["files"].items():
        path = directory / name
        require(path.is_file() and not path.is_symlink() and type(entry["size"]) is int and entry["size"] == path.stat().st_size
                and entry["sha256"] == digest(path), "kernel artifact hash mismatch: " + name)
    if builder is not None:
        require(record.get("builder") == builder, "controlled builder provenance mismatch")
    parameters = load_json(directory / "build-parameters.json")
    require(parameters["module_certificate_sha256"] == digest(directory / "module-signing-cert.x509"),
            "module certificate provenance mismatch")
    version = load_json(ROOT / "kernel/stable.lock.json")["version"]
    require((directory / "kernel-release.txt").read_text().strip() == version + "-1-appsynergy-wsl", "kernel release mismatch")
    subprocess.run([sys.executable, str(ROOT / "kernel/verify-config"), str(directory / "kernel.config"),
                    str(ROOT / "kernel/wsl.config"), str(ROOT / "kernel/wsl2-contract.json")],
                   check=True, stdout=subprocess.DEVNULL, timeout=30)
    return record


def extract(archive, directory):
    expected = FILES | {"kernel-inputs.json"}
    with tarfile.open(archive, "r:gz") as stream:
        seen = set()
        total = 0
        for member in stream:
            require(member.name in expected and member.name not in seen and member.isfile(), "unsafe kernel archive entry")
            require(0 < member.size <= 1024**3, "kernel archive member size")
            seen.add(member.name)
            total += member.size
            require(total <= 2 * 1024**3, "kernel archive extraction limit")
            with stream.extractfile(member) as source, (Path(directory) / member.name).open("xb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
        require(seen == expected, "incomplete kernel archive")


def retrieve():
    lock = load_json(LOCK)
    validate_lock(lock)
    identity = kernel_input_hash()
    require(lock["inputs_sha256"] == identity, "kernel inputs changed; build and review a new controlled-builder artifact")
    verify_recipe(lock["recipe_commit"])
    artifact = lock["artifact"]
    archive = fetch(artifact["url"], artifact["sha256"])
    require(archive.stat().st_size == artifact["size"], "kernel archive size mismatch")
    CACHE.mkdir(exist_ok=True)
    output = CACHE / "verified-kernel" / artifact["sha256"]
    builder = {"kind": "maintainer-controlled-linux", "recipe_commit": lock["recipe_commit"], "inputs_sha256": identity}
    if not output.exists():
        output.parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="kernel-verify-", dir=output.parent) as temp:
            extract(archive, temp)
            verify_directory(temp, identity, builder, artifact["receipt_sha256"])
            Path(temp).rename(output)
    verify_directory(output, identity, builder, artifact["receipt_sha256"])
    print("PASS exact controlled-builder kernel inputs, provenance, hashes, module certificate and WSL configuration; no compilation")
    return output


def export(directory, output, commit):
    identity = kernel_input_hash()
    verify_recipe(commit)
    record = verify_directory(directory, identity)
    record["builder"] = {"kind": "maintainer-controlled-linux", "recipe_commit": commit, "inputs_sha256": identity}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"kernel-{identity}.tar.gz"
    require(not archive.exists(), "kernel export already exists; preserve its exact bytes")
    with tempfile.TemporaryDirectory(prefix="kernel-export-") as temp:
        receipt = Path(temp) / "kernel-inputs.json"
        receipt.write_text(json.dumps(record, indent=2) + "\n")
        with archive.open("xb") as raw, gzip.GzipFile(fileobj=raw, filename="", mode="wb", mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as stream:
                for name in sorted(FILES | {"kernel-inputs.json"}):
                    path = receipt if name == "kernel-inputs.json" else Path(directory) / name
                    info = tarfile.TarInfo(name)
                    info.size, info.mode, info.mtime = path.stat().st_size, 0o644, 1792886400
                    with path.open("rb") as source:
                        stream.addfile(info, source)
    with tempfile.TemporaryDirectory(prefix="kernel-export-check-") as temp:
        extract(archive, temp)
        verify_directory(temp, identity, record["builder"])
    lock = {"schema": 1, "inputs_sha256": identity, "recipe_commit": commit,
            "artifact": {"url": f"https://github.com/{REPOSITORY}/releases/download/kernel-build-{identity}/{archive.name}",
                         "sha256": digest(archive), "size": archive.stat().st_size,
                         "receipt_sha256": hashlib.sha256((json.dumps(record, indent=2) + "\n").encode()).hexdigest()}}
    validate_lock(lock)
    LOCK.write_text(json.dumps(lock, indent=2) + "\n")
    # The same hash-checking fetch path can verify the export before upload.
    downloads = CACHE / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive, downloads / lock["artifact"]["sha256"])
    print(f"Exported unsigned testing build input: {archive}")
    print("Review and upload this immutable component before CI. This is not a Windows-accepted release.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["export", "verify"])
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--recipe-commit")
    args = parser.parse_args()
    if args.operation == "export":
        if not all([args.directory, args.output, args.recipe_commit]):
            parser.error("export requires --directory, --output and --recipe-commit")
        export(args.directory, args.output, args.recipe_commit)
    else:
        print(retrieve())


if __name__ == "__main__":
    main()
