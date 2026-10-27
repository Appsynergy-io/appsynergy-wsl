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

On Windows 11, open **Windows PowerShell** and paste this single command. No checkout, manual download,
extraction or command-line options are needed:

```powershell
irm https://raw.githubusercontent.com/Appsynergy-io/appsynergy-wsl/main/windows/bootstrap.ps1 | iex
```

Windows PowerShell 5.1 is supported as the entry shell. When needed, the bootstrap
downloads a pinned private PowerShell 7 runtime, verifies its hash and Microsoft
signature, continues automatically, and removes that runtime afterward. It does
not replace Windows PowerShell or change permanent execution policy. If Windows'
default policy blocks script files, it asks before allowing scripts with
`RemoteSigned` in the temporary child process only. It respects organizational
policy and explicitly configured `AllSigned` restrictions.

The bootstrap downloads the latest accepted release, verifies the signed manifest,
acceptance promotion proof and installer hash, then downloads and verifies the
remaining artifacts. It asks for your Linux username and password. After installation,
it asks whether to activate the AppSynergy kernel and shut down **all** your running
WSL sessions. Answering no leaves activation pending; nothing activates in the background.
Existing distributions are never overwritten.

**Availability:** no accepted release is published yet. The command can install
a supported distribution only after Windows acceptance and release publication.
It never substitutes an unaccepted testing candidate. Use the maintainer acceptance
harness below for the first Windows test.

<details>
<summary>First-time Windows setup: WSL prerequisites</summary>

On a fresh Windows 11 host, WSL must be enabled before importing AppSynergy.
If it is missing, run this explicit host setup command in an
administrator terminal approved by your organization:

```powershell
wsl.exe --install --no-distribution
```

Microsoft documents [PowerShell installation](https://learn.microsoft.com/en-us/powershell/scripting/install/installing-powershell-on-windows)
and [WSL's no-distribution installation](https://learn.microsoft.com/en-us/windows/wsl/basic-commands).
Restart Windows if WSL requests it. Then open **Windows PowerShell as your normal Windows
account**. Existing WSL installations can proceed directly to the checks below;
the tools require WSL 2.5.7+, an x86-64-v3 CPU and 20 GiB free. Enterprise policy
must permit the reviewed scripts, WSL and custom kernels. The installer does not
change organizational policy or install another distribution as a prerequisite.

For the first pre-release test, use [Maintainer acceptance](#maintainer-acceptance)
below. That path imports a clean, uniquely named test distribution and preserves
existing distributions and their files.

</details>

The initial script is trusted through this repository's HTTPS URL; subsequently
executed tools are hash-verified against signed release metadata. For review before
execution, inspect [bootstrap.ps1](windows/bootstrap.ps1). Organizational PowerShell,
AppLocker and WDAC policy remains unchanged. Missing Windows prerequisites still
require the explicit setup in the expandable section above and any requested reboot.

Advanced users can run `windows/Install.ps1` from reviewed source or release tools;
it prompts interactively without required flags. `-Version` selects an accepted
release, and `-CandidateDirectory` explicitly selects extracted testing artifacts.
The maintainer acceptance harness remains separate because it records interruption,
reboot and recovery tests in its own distribution.

Staging verifies the signed manifest, artifact hashes and signed acceptance
promotion proof. Installation refuses existing distribution names and prompts for
a Linux password. Sudo requires that password. Resume interrupted setup with the
same username:

```powershell
wsl.exe -d AppSynergy -u root -- appsynergy-first-run developer
```

**Kernel selection affects all WSL2 distributions for your Windows account.**
The installer asks for activation consent; advanced commands require explicit
`-AllowShutdown`. Activation preserves the previous kernel/module pair and unrelated `.wslconfig` settings, then checks health. Failure restores the
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
python3 scripts/check build --component kernel --locked-artifact
```

Kernel compilation runs on the maintainer's controlled Linux builder. GitHub CI
downloads the reviewed input in [kernel-artifact.lock.json](sources/kernel-artifact.lock.json),
verifies its archive and every file, checks the module certificate and WSL
configuration, and proves its kernel inputs match the recorded source commit.
It never starts another hosted compilation or substitutes a different kernel.
These unsigned build inputs are testing components; they are not accepted product
releases or independent build attestations. Release signing stays in separate jobs.

After a source build, export its output against a full commit containing identical
kernel inputs. Substitute the actual directories and reviewed recipe commit:

```bash
python3 -m wslbuild.kernel_artifact export --directory /path/to/kernel-output --output /path/to/empty-export-directory --recipe-commit FULL_COMMIT_SHA
```

Review the generated lock and upload the exact archive to its pinned testing-input
URL before submitting the change. A kernel input change requires a new controlled
build and reviewed lock. Build caches and private module keys remain on the builder.

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

Download the `candidate-SOURCE_SHA-ATTEMPT` artifact from the successful main run in
[GitHub Actions](https://github.com/Appsynergy-io/appsynergy-wsl/actions/workflows/ci.yml).
The maintainer handoff identifies the exact run, artifact and source commit. Extract
the candidate ZIP into a new folder. Separately download and extract the repository
source ZIP for that same commit; run the following commands from its root. Git and
the GitHub CLI are not required on Windows. GitHub may require your signed-in
maintainer account to download the Actions artifact.

Use the reviewed source's verification tools and public key before importing:

```powershell
$candidate = (Resolve-Path 'C:\path\to\extracted-candidate').Path
.\windows\AppSynergy.ps1 -Operation Status
Import-Module .\windows\AppSynergy.psm1 -Force
Get-VerifiedManifest $candidate | Select-Object source, version, build
```

This verifies the signed manifest and every artifact before installation. No
accepted-release `Version` is needed for this first test. The harness creates
uniquely named distributions and owned directories; it installs no runner.
`Start` imports the clean rootfs and prints its state directory. Assign that printed
path to `$state` for subsequent phases. Use your GitHub handle:

```powershell
.\windows\Acceptance.ps1 -Phase Start -CandidateDirectory $candidate -Maintainer YourGitHubHandle
.\windows\Acceptance.ps1 -Phase Setup -StateDirectory $state -AllowShutdown
```

Save the printed state directory before restarting Windows; set `$state` to that
same path in the new PowerShell session. The progress journal persists there.
`Setup` creates the normal Linux user `acceptance` and prompts for its password.
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
