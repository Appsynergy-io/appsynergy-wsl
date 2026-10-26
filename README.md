<p align="center"><img src="assets/appsynergy.svg" width="80" height="80" alt="AppSynergy logo"></p>

# AppSynergy WSL

An Arch-based development environment for WSL2, using AppSynergy's CachyOS server
kernel lineage, ThinLTO and x86-64-v3.

**Pre-release. No supported release has passed Windows maintainer acceptance.**
Linux builds and container tests cannot establish Windows compatibility. Candidates
remain testing artifacts until the complete acceptance gate passes.

[Compatibility](#compatibility) · [Installation](#installation) · [Updates](#updates) ·
[Backup and recovery](#backup-and-recovery) · [Development](#development) ·
[Maintainer acceptance](#maintainer-acceptance) · [Support](#support)

## Compatibility

| Component | Target and validation boundary |
| --- | --- |
| Host | Windows 11 Enterprise x64, x86-64-v3 CPU; Enterprise policies must allow WSL and custom kernels |
| Runtime | Store/MSI WSL 2.5.7+ with custom kernel/module VHD support; actual target version recorded during acceptance |
| Kernel | AppSynergy/CachyOS, ThinLTO; built-in boot storage, Hyper-V, vsock, 9P, VirtIO and OverlayFS |
| Modules | ABI-matched ext4 VHDX; filesystem, image and module metadata checked during build |
| Userspace | Signed Arch packages, systemd, bash, password-authenticated sudo, Git and development tools |
| Windows validation | Boot, interoperability, shutdown, reboot, backup/import and failed-boot rollback remain required |

The [kernel contract](kernel/wsl2-contract.json) records configuration requirements
and pinned Microsoft compatibility references. Microsoft source or binaries are
never substituted for the product kernel. The inherited 7.2.3 source and server
configuration retain their AppSynergy pins; a [reviewed stable backport](kernel/stable.lock.json)
updates that source to 7.2.13 while preserving CachyOS changes. Both upstream tags
are verified with the release maintainer's public key before build execution.
The module image uses WSL's supported
flat layout; announced removal of that layout requires a reviewed migration and
fresh acceptance. GPU/USB guarantees, desktop sessions, ARM64, Windows 10, WSL1
and Store registration are outside scope.

## Installation

There is no supported download yet. These commands describe the implemented release
interface and become usable after an accepted release is published. Run them in
**PowerShell 7.4+ on Windows**, from the reviewed repository checkout. Choose the
version reported by `Check`.
Use your organization's approved PowerShell policy; the tools do not change
execution policy, AppLocker or WDAC settings.

```powershell
.\windows\AppSynergy.ps1 -Operation Status
.\windows\AppSynergy.ps1 -Operation Check
.\windows\AppSynergy.ps1 -Operation Stage -Version 0.1.0
.\windows\AppSynergy.ps1 -Operation Install -Version 0.1.0 -Distribution AppSynergy -LinuxUser developer
.\windows\AppSynergy.ps1 -Operation Activate -Version 0.1.0 -Distribution AppSynergy -AllowShutdown
```

Staging verifies the signed manifest, artifact hashes and signed acceptance
promotion proof. Installation refuses existing distribution names and prompts for
a Linux password. Sudo requires that password. Resume interrupted setup with the
same username:

```powershell
wsl.exe -d AppSynergy -u root -- appsynergy-first-run developer
```

**Kernel selection affects all WSL2 distributions for your Windows account.**
Activation requires explicit `-AllowShutdown`, preserves the previous kernel/module
pair and unrelated `.wslconfig` settings, then checks health. Failure restores the
previous configuration. The tools do not modify partitions or bootloaders.
Administrative prerequisite changes remain explicit host setup.

## Updates

Update installed Linux userspace with a signed full-system update:

```bash
sudo pacman -Syu
```

Shared AppSynergy packages retain their existing signed origin. WSL integration
uses its own signed channel, enabled by the Windows tool for accepted releases.
For a Windows-side kernel update, use `Check`, `Stage` and `Activate` with the new
version. Staging never activates automatically. New rootfs images serve installation
and explicit recovery; they never overwrite installed user files. There is no
background updater.

## Backup and recovery

Use the exact distribution name shown by WSL and a backup directory you own:

```powershell
wsl.exe --list --verbose
wsl.exe --terminate AppSynergy
wsl.exe --export AppSynergy D:\Backups\AppSynergy.tar
.\windows\AppSynergy.ps1 -Operation Rollback -AllowShutdown
```

Rollback runs directly from Windows, including when the candidate cannot boot.
Keep versioned Windows-side files and the activation journal until the replacement
has passed your health checks.

<details>
<summary>Import a backup into a separate recovery instance</summary>

Choose a new name and an empty destination to preserve the original instance:

```powershell
wsl.exe --import AppSynergy-Recovery D:\WSL\AppSynergy-Recovery D:\Backups\AppSynergy.tar --version 2
wsl.exe -d AppSynergy-Recovery -u root -- appsynergy-first-run developer
```

The username must match the backup's first-run journal. Rollback refuses conflicting
external edits to managed kernel settings for manual review. Preserve
`%LOCALAPPDATA%\AppSynergy\WSL`, `.wslconfig` and your backups. Do not unregister
the original distribution to recover a kernel configuration.

</details>

## Development

Use Linux x64, an updated Python 3.12+ installation, Git, curl, GnuPG and Docker.
Python 3.11 with backported tarfile data filters is also supported. Kernel compilation needs
40 GiB free for the compiler tree and assembly reserve, plus space for downloaded
inputs and candidates. Builds use a pinned Arch image and signed, hash-locked
packages. Build/test containers receive no release signing credentials.

```bash
python3 scripts/bootstrap
python3 scripts/check fast
python3 scripts/check gate
python3 scripts/check gate --no-fail-fast
python3 scripts/fetch-sources
python3 scripts/check build --component kernel
```

`fast` classifies changes and names omitted checks; uncertain history selects full
cheap validation. `gate` runs applicable automated checks in cost order. Diagnostic
mode collects cheap failures but still blocks expensive work on cheap failure.
Logs and reusable artifacts stay in `.cache/`; failures print rerun commands.
Exit codes: 0 success, 1 failed check, 2 usage error or missing prerequisite.

Build the integration package without a signing key. A separate signing-only
operation uses an existing private keyring matching the pinned public key.
Substitute the printed output directories:

```bash
python3 -m wslbuild.rootfs integration
python3 scripts/sign-integration /path/to/integration-output --keyring /path/to/private-keyring
python3 scripts/check build --component rootfs --signed-package /path/to/integration-output
python3 scripts/check gate --rootfs /path/to/rootfs-output
```

Compilation produces testing artifacts; candidate assembly requires committed,
clean source. One [CI workflow](.github/workflows/ci.yml) handles pull requests,
main pushes and merge groups. Documentation changes skip expensive builds.
Cheap gates precede package checks and builds. `ci-ok` rejects failed, missing,
cancelled or unexpectedly skipped required jobs. Signing uses isolated main-only
environments. Dependencies, contributions and merges remain manual; there are no
bots or scheduled workflows.

## Maintainer acceptance

Use a signed candidate from a successful trusted-main build. The harness creates
uniquely named distributions and owned directories; it installs no runner.
Set `$candidate` to the extracted candidate directory. `Start` prints a state
directory; assign it to `$state` for subsequent phases. Use your GitHub handle:

```powershell
.\windows\Acceptance.ps1 -Phase Start -CandidateDirectory $candidate -Maintainer YourGitHubHandle
.\windows\Acceptance.ps1 -Phase Setup -StateDirectory $state -AllowShutdown
```

Interrupt the first password prompt as instructed, then rerun `Setup` with the
same state directory. Complete the retry and interactive sudo check. At the reboot
checkpoint, reboot Windows yourself, then resume:

```powershell
.\windows\Acceptance.ps1 -Phase Resume -StateDirectory $state
.\windows\Acceptance.ps1 -Phase Finish -StateDirectory $state -AllowShutdown -AllowFailedBootTest
.\windows\Acceptance.ps1 -Phase Cleanup -StateDirectory $state -AllowShutdown
```

`Finish` deliberately tests failed boot and rollback, affecting account-wide WSL
sessions. Copy the resulting `acceptance.json` out before cleanup. Review and
redact it, then submit an evidence-only PR under `acceptance/`, referencing the
existing trusted-main candidate and changing no product inputs. Incomplete, stale
or mismatched evidence is rejected. This is **maintainer acceptance**, not
independent automated Windows attestation. Publication promotes the exact accepted
bytes without rebuilding and records source and acceptance commits separately.

## Support

Report reproducible issues through [GitHub Issues](https://github.com/Appsynergy-io/appsynergy-wsl/issues).
Include the source revision, Windows build, WSL version, kernel identity and redacted
diagnostics. Omit passwords, tokens and private host information. The project remains
unsupported until Windows acceptance and release verification pass.

## Sources and licenses

[AppSynergy Linux](https://github.com/Appsynergy-io/appsynergy-linux) remains
authoritative for the kernel recipe, branding, keyring and shared package repository.
[Source locks](sources/inputs.lock.json) preserve its commit and CachyOS lineage;
[package locks](sources/packages.lock.json) record exact build inputs.
[NOTICE](NOTICE) retains attribution. Tooling uses the [MIT license](LICENSE);
the [kernel's licenses](LICENSES/kernel/COPYING) and package licenses remain applicable.
Releases must retain corresponding
source, configuration, patch provenance, toolchain inventory and build evidence.

The implementation adapts the Prompt Recipe Book at
`4e11740139a2fb586c2114ca53c6356b5663d373`. Contributor instructions are in
[AGENTS.md](AGENTS.md).
