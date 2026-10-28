"""Complete event diffs; uncertainty is expensive, never a reason to skip."""

import json
import os
import subprocess

from .policy import classify, SHA


def git(*args):
    return subprocess.check_output(["git", *args], stderr=subprocess.DEVNULL, timeout=30)


def changed_paths(base, head, three_dot=False, local=False):
    if not SHA.fullmatch(base or "") or not SHA.fullmatch(head or "") or base == "0" * 40:
        return set(), True
    try:
        git("cat-file", "-e", f"{base}^{{commit}}")
        git("cat-file", "-e", f"{head}^{{commit}}")
        # --no-renames reports old and new names independently, including deletions.
        raw = git("diff", "--name-only", "--no-renames", "-z", f"{base}{'...' if three_dot else '..'}{head}", "--")
        if local:
            raw += git("diff", "--name-only", "--no-renames", "-z", "HEAD", "--")
            raw += git("ls-files", "--others", "--exclude-standard", "-z")
        return {p.decode("utf-8", errors="surrogateescape") for p in raw.split(b"\0") if p}, False
    except (subprocess.SubprocessError, OSError):
        return set(), True


def event_range(event, name):
    if name == "pull_request":
        return event["pull_request"]["base"]["sha"], event["pull_request"]["head"]["sha"], True
    if name == "merge_group":
        return event["merge_group"]["base_sha"], event["merge_group"]["head_sha"], False
    if name == "push":
        return event["before"], event["after"], False
    raise ValueError("unsupported event")


def main():
    try:
        with open(os.environ["GITHUB_EVENT_PATH"], encoding="utf-8") as stream:
            event = json.load(stream)
        base, head, three_dot = event_range(event, os.environ["GITHUB_EVENT_NAME"])
        paths, uncertain = changed_paths(base, head, three_dot)
    except (KeyError, OSError, ValueError):
        paths, uncertain = set(), True
    kind = classify(paths, uncertain)
    from .candidate import version_at
    release = kind == "product" and version_at("HEAD") is not None
    print(json.dumps({"kind": kind, "uncertain": uncertain, "paths": sorted(paths)}))
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
        stream.write(f"kind={kind}\nrelease={str(release).lower()}\n")


if __name__ == "__main__":
    main()
