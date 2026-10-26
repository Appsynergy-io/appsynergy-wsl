"""Construct an integration package and clean rootfs using locked signed inputs."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from .build import input_hash, package_inputs
from .evidence import digest, load_json, require
from .inputs import CACHE, ROOT, prepare_sources


def integration(output_directory=None):
    image = load_json(ROOT / "sources/package-builder.lock.json")["image"]
    version = os.environ.get("WSL_PACKAGE_VERSION", "0.1.0")
    require(re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version), "package version")
    output = Path(output_directory) if output_directory is not None else CACHE / "integration" / (input_hash() + "-" + version)
    output.mkdir(parents=True, exist_ok=True)
    require(not list(output.glob("*.sig")), "integration output is already signed; preserve its bytes and use a fresh directory")
    name = "appsynergy-package-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="wsl-integration-") as temp:
        work = Path(temp)
        work.chmod(0o755)
        shutil.copytree(ROOT / "packaging", work / "packaging")
        shutil.copyfile(ROOT / "LICENSE", work / "LICENSE")
        (work / "sources").mkdir()
        shutil.copyfile(ROOT / "sources/wsl-integration-signing.asc", work / "sources/wsl-integration-signing.asc")
        try:
            subprocess.run(["docker", "run", "-d", "--name", name, "--network=none", "--read-only", "--cap-drop=ALL",
                            "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=1g", "--user=65534:65534",
                            "--tmpfs=/tmp:rw,nosuid,nodev,size=256m,mode=1777",
                            "--env", f"WSL_PACKAGE_VERSION={version}", "--env", "SOURCE_DATE_EPOCH=1792886400", "--mount",
                            f"type=bind,src={work},dst=/inputs,readonly", image, "sleep", "infinity"],
                           check=True, timeout=300)
            subprocess.run(["docker", "exec", name, "bash", "/inputs/packaging/check-package"], check=True, timeout=300)
            # docker cp cannot reliably read a container's tmpfs mounts. Stream
            # the one known output through exec instead of exporting its filesystem.
            filename = f"appsynergy-wsl-integration-{version}-1-any.pkg.tar.zst"
            with (output / filename).open("wb") as stream:
                subprocess.run(["docker", "exec", name, "cat", "/tmp/appsynergy-package/" + filename],
                               stdout=stream, check=True, timeout=30)
            archives = list(output.glob("*.pkg.tar.zst"))
            require(len(archives) == 1 and not archives[0].is_symlink(), "integration package output")
            print(f"Unsigned integration package: {output / archives[0].name}")
        finally:
            subprocess.run(["docker", "rm", "-f", name], check=True, capture_output=True)
    from .publication import build_database
    with tempfile.TemporaryDirectory(prefix="wsl-package-database-check-") as temp:
        build_database(archives[0], Path(temp), image)
    print("PASS integration package and isolated repository database construction")
    return output


def rootfs(signed_package_directory, testing_key=None):
    source_identity = input_hash()
    package_directory = Path(signed_package_directory).resolve()
    archives = list(package_directory.glob("*.pkg.tar.zst"))
    require(len(archives) == 1 and Path(str(archives[0]) + ".sig").is_file(), "one signed integration package required")
    signing = load_json(ROOT / "sources/signing.lock.json")
    require(digest(ROOT / "sources/wsl-integration-signing.asc") == signing["integration_public_sha256"], "integration key pin")
    _, evidence = prepare_sources()
    image, packages = package_inputs("rootfs")
    rootfs_identity = hashlib.sha256((source_identity + digest(archives[0]) + digest(Path(str(archives[0]) + ".sig")) + (digest(Path(testing_key)) if testing_key else signing["integration_public_sha256"])).encode()).hexdigest()
    output = CACHE / "rootfs" / rootfs_identity
    output.mkdir(parents=True, exist_ok=True)
    inputs = CACHE / "rootfs-inputs" / rootfs_identity
    inputs.mkdir(exist_ok=True, parents=True)
    shutil.copyfile(ROOT / "build/install-packages", inputs / "install-packages")
    shutil.copyfile(ROOT / "build/rootfs", inputs / "rootfs")
    public_key = ROOT / "sources/wsl-integration-signing.asc"
    fingerprint = signing["integration_fingerprint"]
    if testing_key:
        require(not (os.environ.get("GITHUB_EVENT_NAME") == "push" and os.environ.get("GITHUB_REF") == "refs/heads/main"), "test signing key prohibited on trusted main")
        public_key = Path(testing_key).resolve()
        with tempfile.TemporaryDirectory(prefix="wsl-test-key-read-") as keyring:
            result = subprocess.check_output(["gpg", "--no-options", "--homedir", keyring, "--batch", "--with-colons", "--show-keys", str(public_key)], text=True, timeout=30)
        fingerprints = [line.split(":")[9] for line in result.splitlines() if line.startswith("fpr:")]
        require(len(fingerprints) == 1, "exactly one test signing key required")
        fingerprint = fingerprints[0]
    shutil.copyfile(public_key, inputs / "package-signing.asc")
    (inputs / "package-signing-fingerprint").write_text(fingerprint + "\n")
    integration_dir = inputs / "integration"
    integration_dir.mkdir(exist_ok=True)
    shutil.copyfile(archives[0], integration_dir / archives[0].name)
    shutil.copyfile(str(archives[0]) + ".sig", integration_dir / (archives[0].name + ".sig"))
    for file in ("appsynergy.gpg", "appsynergy-trusted", "appsynergy-revoked"):
        shutil.copyfile(evidence / "packages/pkgbuilds/appsynergy-keyring" / file, inputs / file)
    for file in ("appsynergy.conf", "appsynergy-mirrorlist"):
        shutil.copyfile(evidence / "packages/pkgbuilds/appsynergy-mirrorlist" / file, inputs / file)
    log = output / "build.log"
    name = "appsynergy-rootfs-" + uuid.uuid4().hex
    try:
        with log.open("w") as stream:
            subprocess.run(["docker", "run", "--name", name, "--rm", "--init", "--network=none", "--pids-limit=1024", "--memory=4g",
                            "--security-opt=no-new-privileges", "--mount", f"type=bind,src={inputs},dst=/inputs,readonly",
                            "--mount", f"type=bind,src={packages},dst=/packages,readonly",
                            "--mount", f"type=bind,src={output},dst=/work", image, "bash", "/inputs/rootfs"],
                           check=True, stdout=stream, stderr=subprocess.STDOUT, timeout=1800)
    except subprocess.SubprocessError:
        print("\n".join(log.read_text(errors="replace").splitlines()[-60:]))
        print(f"Full rootfs build log: {log}")
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)

    require(input_hash() == source_identity, "inputs changed during rootfs assembly; candidate rejected")
    record = {"schema": 1, "inputs_sha256": source_identity, "testing_signer": testing_key is not None,
              "integration": {"sha256": digest(archives[0]), "signature_sha256": digest(Path(str(archives[0]) + ".sig"))},
              "files": {name: {"sha256": digest(output / name), "size": (output / name).stat().st_size}
                        for name in ("rootfs.tar.zst", "rootfs-inventory.txt")}}
    (output / "rootfs-inputs.json").write_text(json.dumps(record, indent=2) + "\n")
    print(f"Rootfs candidate: {output / 'rootfs.tar.zst'}; Windows acceptance NOT established")
    return output


def verify_rootfs_inputs(directory, source_identity, integration_package, trusted_main):
    directory, integration_package = Path(directory), Path(integration_package)
    record = load_json(directory / "rootfs-inputs.json")
    require(record.get("schema") == 1 and record.get("inputs_sha256") == source_identity, "rootfs source inputs mismatch")
    require(type(record.get("testing_signer")) is bool and not (trusted_main and record["testing_signer"]), "testing rootfs signer prohibited on trusted main")
    require(record["integration"] == {"sha256": digest(integration_package), "signature_sha256": digest(Path(str(integration_package) + ".sig"))}, "rootfs integration bytes mismatch")
    require(set(record["files"]) == {"rootfs.tar.zst", "rootfs-inventory.txt"}, "rootfs receipt inventory")
    for name, entry in record["files"].items():
        path = directory / name
        require(not path.is_symlink() and path.is_file() and digest(path) == entry["sha256"] and path.stat().st_size == entry["size"], "rootfs artifact bytes mismatch")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("component", choices=["integration", "rootfs"])
    parser.add_argument("--signed-package")
    parser.add_argument("--testing-key")
    args = parser.parse_args()
    if args.component == "integration":
        result = integration()
    elif args.signed_package:
        result = rootfs(args.signed_package, args.testing_key)
    else:
        parser.error("rootfs requires --signed-package")

    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as stream:
            stream.write(f"directory={result}\n")
