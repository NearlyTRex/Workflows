# Automation

| Workflow | Inputs (defaults) | Caller grants |
|---|---|---|
| `scheduled-update.yml` | `command`, `paths`, `commit-message` (Update generated files: {date}), `pr-branch` (scheduled-update/BASE), `assignees`, `node-version`, `timeout-minutes` (60). Outputs `changed`, `pr-url` | `contents: write`, `pull-requests: write` |
| `license-year.yml` | `path` (LICENSE), `assignees`. Outputs `pr-url` | `contents: write`, `pull-requests: write` |

## `scheduled-update`

`scheduled-update` is for repos that publish generated data, such as an export refreshed daily. It
runs `command` and opens a pull request, as `github-actions[bot]`, with whatever it changed under
`paths`, into the branch the run started from. So it works on a branch that requires pull requests.
A run that changes nothing does nothing.

- **One pull request:** each run commits onto the base branch's tip and force-pushes
  `pr-branch`, so a single pull request always holds the latest changes; an open one gets the
  new title. Merge it whenever. `assignees` get it, so it shows up in their review queue.
- **Two jobs:** `command` runs with read-only access, and `diff-paths` writes what it changed
  as a patch. A second job, which runs none of the repo's code, applies it with
  `commit-patch` and opens the pull request with `open-pr`, so a compromised dependency of
  the command never holds a token that can write.
- **Moved branch:** the changes are applied to the base branch's tip. If a push since the
  command started touched the same lines, the run fails and the next one starts from the new
  tip.
- **No CI on the pull request:** GitHub doesn't start runs for a pull request opened with the
  workflow token, so don't make checks required on the base branch.
- **Repo setting:** "Allow GitHub Actions to create and approve pull requests", as for
  prepare-release (see [Repo settings](setup.md#repo-settings)).
- **`node-version`:** sets up Node.js with npm's download cache, which needs a
  `package-lock.json`.

```yaml
on:
  schedule:
    - cron: "0 0 * * *"
  workflow_dispatch:

jobs:
  update:
    uses: NearlyTRex/Workflows/.github/workflows/scheduled-update.yml@<sha> # vX
    permissions:
      contents: write
      pull-requests: write
    with:
      assignees: NearlyTRex
      node-version: "24"
      command: npm ci && node generate.js
      paths: data/
```

## `license-year`

`license-year` opens a pull request that brings the copyright year(s) in the license file up to the
current year, using
[action-update-license-year](https://github.com/FantasticFiasco/action-update-license-year).
`assignees` get it to review and merge; it is never merged for you. A file that is already current
gets no pull request. It needs the same "create pull requests" setting as prepare-release (see [Repo
settings](setup.md#repo-settings)). Call it yearly:

```yaml
on:
  schedule:
    - cron: "0 3 1 1 *" # 03:00 on January 1
  workflow_dispatch:

jobs:
  license-year:
    uses: NearlyTRex/Workflows/.github/workflows/license-year.yml@<sha> # vX
    permissions:
      contents: write
      pull-requests: write
    with:
      path: LICENSE.md
      assignees: NearlyTRex
```
