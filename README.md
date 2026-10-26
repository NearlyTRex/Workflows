# Workflows

[![CI](https://github.com/NearlyTRex/Workflows/actions/workflows/self-ci.yml/badge.svg?branch=main)](https://github.com/NearlyTRex/Workflows/actions/workflows/self-ci.yml)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen)](https://github.com/NearlyTRex/Workflows/actions/workflows/self-ci.yml)
[![Security](https://github.com/NearlyTRex/Workflows/actions/workflows/self-security.yml/badge.svg?branch=main)](https://github.com/NearlyTRex/Workflows/actions/workflows/self-security.yml)
[![Tool versions](https://github.com/NearlyTRex/Workflows/actions/workflows/self-tool-versions.yml/badge.svg)](https://github.com/NearlyTRex/Workflows/actions/workflows/self-tool-versions.yml)
[![Release](https://img.shields.io/github/v/release/NearlyTRex/Workflows)](https://github.com/NearlyTRex/Workflows/releases/latest)
[![License](https://img.shields.io/github/license/NearlyTRex/Workflows)](LICENSE)

Reusable template library for github workflows

My repos call these workflows instead of carrying their own copies. A fix or tool update made here
reaches every repo through one Dependabot bump.

## Setting up a repo

```bash
python3 scripts/init_repo.py ~/Repositories/SomeRepo            # writes .github/
python3 scripts/init_repo.py ~/Repositories/SomeRepo --dry-run  # shows it first
```

It detects the stack and writes CI, security, release and Dependabot workflows that call the ones
here, plus the community files GitHub looks for. See [Setting up a repo](docs/setup.md), which also
lists the [repo settings](docs/setup.md#repo-settings) the workflows need.

Every call is pinned to the commit of this library's latest release, with the tag as a comment, and
Dependabot moves that pin forward when this library releases:

```yaml
uses: NearlyTRex/Workflows/.github/workflows/lint.yml@<commit sha> # v0.1.0
```

## Reusable workflows

All of them run with the least permission they need. The caller must grant at least that much; each
page lists the inputs, their defaults and the permissions.

| Page | Workflows | What they do |
|---|---|---|
| [Python](docs/python.md) | `python-ci`, `python-build` | Tests under a coverage gate, shell coverage and a shell sandbox; hash-locked builds, and publishing to PyPI |
| [Lint](docs/lint.md) | `lint` | shellcheck, rumdl, yamllint, actionlint, PSScriptAnalyzer, clang-format, and JSON and TOML parsing |
| [Security](docs/security.md) | `security`, `codeql`, `dependency-review`, `container-scan` | zizmor, gitleaks, pip-audit, tracked credential files, CodeQL, and image scans |
| [C and C++](docs/cpp.md) | `cpp-build`, `windows-test`, `inno-setup` | Builds on any runner with MSVC, LLVM, ccache and clang-tidy; Windows tests under page heap; installers |
| [PS4 homebrew](docs/homebrew/ps4.md) | `cpp-build` with `openorbis-version` | The OpenOrbis SDK, a CMake toolchain, and `eboot.bin`, `.prx` and `.pkg` helpers |
| [Switch homebrew](docs/homebrew/switch.md) | `cpp-build` with `devkitpro: switch` | devkitPro's devkitA64 image, with libnx, the portlibs and the Switch tools |
| [Xbox homebrew](docs/homebrew/xbox.md) | `cpp-build` with `msvc-uwp` | MSVC for UWP apps in Dev Mode, and `ci-msix` to package and sign them |
| [Compose](docs/compose.md) | `compose-test` | Starts a Compose stack, waits for it to be healthy, and tests it |
| [Releases](docs/releases.md) | `prepare-release`, `release-check`, `release`, `attest` | Version bumps by pull request, tagging, assets, checksums and provenance |
| [Automation](docs/automation.md) | `scheduled-update`, `license-year` | Pull requests for regenerated data and the license year |

[Development](docs/development.md) covers how the workflows and actions fit together, and working
on this repo.
