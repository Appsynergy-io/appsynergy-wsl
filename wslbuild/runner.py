"""Sequential, logged checks with explicit prerequisite and failure statuses."""

import shlex
import subprocess
import time
from pathlib import Path


def run_stages(stages, logs, diagnostic=False, cwd=None, env=None):
    logs = Path(logs)
    logs.mkdir(parents=True, exist_ok=True)
    failures = []
    for name, command in stages:
        started = time.monotonic()
        print(f"[run] {name}", flush=True)
        log = logs / f"{name}.log"
        with log.open("w") as stream:
            try:
                result = subprocess.run(command, cwd=cwd, env=env, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=600)
                code = 0 if result.returncode == 0 else 1
            except FileNotFoundError as error:
                stream.write(str(error))
                code = 2
            except subprocess.TimeoutExpired:
                stream.write("Check exceeded its 10-minute limit\n")
                code = 1
        if code:
            print(log.read_text())
            failures.append((name, command, code))
            print(f"[FAIL] {name}; log: {log}", flush=True)
            if not diagnostic:
                break
        else:
            print(f"[ok] {name} {time.monotonic() - started:.1f}s", flush=True)
    for name, command, _ in failures:
        print(f"FAIL: {name}. Rerun: {shlex.join(command)}")
    return max((code for _, _, code in failures), default=0)
