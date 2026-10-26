"""Pure policy functions shared by local validation and CI."""

import re

SHA = re.compile(r"[0-9a-f]{40}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
SUBJECT = re.compile(r"(feat|fix|perf|docs|chore|ci|test|style|refactor|build|revert)(\([^\n)]+\))?!?: \S.+")
SKIP = re.compile(r"\[(skip ci|ci skip|no ci|skip actions|actions skip)\]|skip-checks\s*:", re.I)
SCENARIOS = frozenset({
    "install-wsl2", "first-run", "first-run-interruption-retry", "default-user-sudo",
    "systemd", "network", "signed-full-update", "development-tools", "windows-interop",
    "kernel-modules", "shutdown-persistence", "reboot-persistence", "backup-import",
    "kernel-update", "failed-boot-rollback",
})


def classify(paths, uncertain=False):
    """Unknown and empty diffs require full validation; both rename paths count."""
    paths = set(paths)
    if uncertain or not paths:
        return "product"
    docs = {"README.md", "AGENTS.md", "CLAUDE.md", "assets/appsynergy.svg"}
    if paths <= docs:
        return "docs"
    if all(p.startswith("acceptance/") and p.endswith(".json") for p in paths):
        return "acceptance"
    return "product"


def lint_commit(message):
    if not message or not SUBJECT.fullmatch(message.splitlines()[0]):
        raise ValueError("commit subject must use Conventional Commits")
    if SKIP.search(message):
        raise ValueError("native CI-skip tokens are forbidden")


def proposed_version(tags, messages):
    versions = []
    for tag in tags:
        match = re.fullmatch(r"appsynergy-wsl-v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag)
        if match:
            versions.append(tuple(map(int, match.groups())))
    if not versions:
        return "0.1.0"
    level = 0
    for message in messages:
        lint_commit(message)
        subject = message.splitlines()[0]
        if re.search(r"!:", subject) or re.search(r"(?m)^BREAKING[ -]CHANGE: \S", message):
            level = 3
        elif subject.startswith("feat"):
            level = max(level, 2)
        elif subject.startswith(("fix", "perf")):
            level = max(level, 1)
    major, minor, patch = max(versions)
    return {0: None, 1: f"{major}.{minor}.{patch + 1}",
            2: f"{major}.{minor + 1}.0", 3: f"{major + 1}.0.0"}[level]


def aggregate(results, expected):
    """Missing results and unexpected skips must never count as success."""
    if set(results) != set(expected):
        raise ValueError("missing or unexpected job result")
    for job, should_run in expected.items():
        want = "success" if should_run else "skipped"
        if results[job] != want:
            raise ValueError(f"{job}: expected {want}, received {results[job]}")
