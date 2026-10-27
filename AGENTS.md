# Commands

- `python3 scripts/bootstrap` installs hash-pinned lint binaries in `.cache/bin`.
- `python3 scripts/check fast` runs cheap checks and names omitted validation.
- `python3 scripts/check gate` runs the complete implemented gate; Docker is required.
- `python3 scripts/check gate --no-fail-fast` collects failures for diagnosis.
- `python3 scripts/check build --component kernel` gates, then compiles the pinned kernel and module VHDX.
- `python3 scripts/check build --component kernel --locked-artifact` gates, then verifies the exact controlled-builder input used by CI.
- `python3 -m wslbuild.rootfs integration` builds the unsigned integration package.
- `python3 scripts/check build --component rootfs --signed-package DIRECTORY` gates, then builds with a separately signed package.
- `python3 scripts/check gate --rootfs DIRECTORY` also exercises the candidate archive and real interrupted first-run setup.
- `python3 scripts/fetch-sources` authenticates the pinned AppSynergy/CachyOS source.
- `python3 -m unittest discover -s tests -v` runs behavioral regression tests.
- Windows: `windows/Acceptance.ps1` uses Start, Setup, Resume, Finish and Cleanup phases; see README.md for explicit shutdown/reboot handling.
- Report Linux checks and Windows host tests separately.

# Conventions

- Keep README.md and this file as the only authored guides. Keep audit reports outside the repository.
- Keep CLAUDE.md exactly `@AGENTS.md` followed by a newline; this file must stay under 200 lines.
- Update full commit pins and hashes together in `sources/inputs.lock.json`; never substitute Microsoft's kernel.
- Retain ThinLTO, x86-64-v3 and inherited AppSynergy/CachyOS source provenance.
- Preserve `kernel/stable.lock.json` signed ancestry and the reviewed adaptation patch; verify both upstream tags before compilation. Never mark an old-version advisory match closed from a renamed kernel release alone.
- Keep versioned WSL adaptation in `kernel/wsl.config`; do not modify appsynergy-linux product or CI contracts.
- Update `sources/kernel-artifact.lock.json` only after exporting a verified controlled-builder kernel. CI never compiles a replacement or falls back to different inputs.
- Use Conventional Commits. Reject native CI-skip tokens in both subjects and bodies.

# Workflow

- Use completion hooks when waiting for builds or other work; do not poll.
- Keep inspection scoped to this project.
- Commit, push, open a PR, merge or release only when scoped work meets its specification and every required check passes. If a check fails or did not run, do not ship; report the unmet work.
- Keep all CI in `.github/workflows/ci.yml`; only pull_request, push to main and merge_group are allowed.
- Add real job dependencies before expensive work. Keep `ci-ok` strict about missing and unexpected skips.
- Use the same stages in `scripts/check` and CI; do not add CI-only substitutes for local checks.
- Keep dependency updates and merges manual. Do not add bots, schedules or background update services.
- Keep release signing credentials out of builds and tests. Kbuild's ephemeral module key stays in the private build cache; ship only its public certificate. Publish signed bytes without rebuilding.
- Windows reports are optional QA, not release prerequisites. Release only exact signed main-run artifacts after all required build checks pass.
- Preserve failing assertions and genuine gates when fixing failures.

# Gotchas

- Account-wide kernel selection affects every WSL2 distribution; activation requires explicit session-shutdown consent.
- Rollback must execute from Windows even when the candidate kernel cannot boot.
- A rootfs update must never replace an installed user's filesystem.
- `wslbuild/evidence.py` validates structure and bytes; callers must independently authenticate the manifest and trusted build metadata. It is not a signature verifier or attestation service.
- Never manufacture trusted-main metadata for local candidates. Acceptance must authenticate the GitHub run and exact signed artifacts.
- WSL uses OverlayFS before module mounting: keep CONFIG_OVERLAY_FS=y. A module cannot bootstrap its own VHD.
- The flat module VHD layout is supported but deprecated upstream; review runtime changes and reaccept before supporting its removal.
- `.cache` contains reusable tools, downloads and logs; never include it in rootfs or release artifacts.
- Keep the public bootstrap compatible with stock Windows PowerShell 5.1 and flag-free; authenticate downloaded tools before execution and prompt before account-wide kernel activation.
- First-run passwords belong only to interactive `passwd`; never put them in manifests, logs, CLI arguments or source.
