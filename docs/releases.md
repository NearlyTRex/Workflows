# Releases

| Workflow | Inputs (defaults) | Caller grants |
|---|---|---|
| `prepare-release.yml` | `bump`, `version-file`, `tag-prefix` (v) | `contents: write`, `pull-requests: write` |
| `release-check.yml` | `version-file`, `tag-prefix` (v). Outputs `version`, `tag`, `pending` | `contents: read` |
| `release.yml` | `version-file`, `tag-prefix` (v), `title`, `draft` (false), `assets`, `checksums` (false), `notes-file`, `dry-run` (false) | `contents: write` |
| `attest.yml` | `artifacts` | `id-token: write`, `attestations: write` |

The version file is the only place a version is written: `pyproject.toml`, `package.json`,
`CMakeLists.txt` (`project(... VERSION x.y.z)`), or a plain `VERSION` file.

1. **Actions → Prepare Release → Run workflow:** enter `patch`, `minor`, `major` or `x.y.z`. It
   bumps the version file on a `release/vX.Y.Z` branch and opens a PR.
2. **Merge the PR:** Release sees an untagged version, tags the merge commit and publishes a
   GitHub Release with generated notes.

Pushes that don't change the version do nothing.

`draft: true` publishes a draft to review before announcing. The tag is still created, so the next
push doesn't start a second draft.

Publishing is all or nothing. The tag is what marks a version released, so if creating the
release fails, for example on an asset upload, the partial release and the tag are removed again,
and re-running the workflow retries the whole release.

- **`notes-file`:** a Markdown file, such as install instructions, placed above the generated
  notes. `{version}`, `{tag}` and `{repository}` in it are filled in.
- **`checksums`:** also attaches `SHA256SUMS.txt` covering every asset.
- **Provenance:** run `attest.yml` over the same artifacts between the builds and the release. It
  signs a record, logged publicly with Sigstore, that each file was built by the repo's workflow
  from a given commit. Anyone can then check a downloaded asset with
  `gh attestation verify <file> --repo OWNER/REPO`. It needs a public repo, or GitHub
  Enterprise Cloud for a private one.
- **`dry-run`:** does everything except create the tag and the release, even for a version
  that is already tagged: it downloads the assets, renders the notes, writes the checksums and
  prints the `gh` commands it would run. For testing a release setup without releasing.

To attach build outputs, build them in an earlier job that uploads them as artifacts, and name them
with `assets`. `release-check` says whether a release is pending and at which version, so the build
only runs when there is something to publish, and is stamped with the version being released:

```yaml
jobs:
  check:
    uses: NearlyTRex/Workflows/.github/workflows/release-check.yml@<sha> # vX
    permissions:
      contents: read
    with:
      version-file: pyproject.toml

  installer:
    needs: check
    if: needs.check.outputs.pending == 'true'
    uses: NearlyTRex/Workflows/.github/workflows/inno-setup.yml@<sha> # vX
    permissions:
      contents: read
    with:
      script: packaging/app.iss
      version: ${{ needs.check.outputs.version }}
      artifact: installer

  attest:
    needs: installer
    uses: NearlyTRex/Workflows/.github/workflows/attest.yml@<sha> # vX
    permissions:
      id-token: write
      attestations: write
    with:
      artifacts: installer

  release:
    needs: attest
    uses: NearlyTRex/Workflows/.github/workflows/release.yml@<sha> # vX
    permissions:
      contents: write
    with:
      version-file: pyproject.toml
      assets: installer
      checksums: true
```

GitHub doesn't run workflows on a PR opened with the workflow token, so don't make checks required
on release PRs.
