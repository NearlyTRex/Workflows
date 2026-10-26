# Security

| Workflow | Inputs (defaults) | Caller grants |
|---|---|---|
| `security.yml` | `zizmor` (true), `gitleaks` (true), `pip-audit-files`, `tracked-files` (true), `tracked-files-patterns` | `contents: read` |
| `codeql.yml` | `languages` (["actions"]), `build-mode` (none), `build-command` | `actions: read`, `contents: read`, `security-events: write` |
| `dependency-review.yml` | `fail-on-severity` (moderate) | `contents: read` |
| `container-scan.yml` | `dockerfile` (Dockerfile), `context` (.), `severity` (HIGH,CRITICAL), `config-scan` (false) | `contents: read` |

## `security`

- **zizmor:** audits the workflows and requires every third-party action to be pinned to a
  commit hash.
- **gitleaks:** on pull requests and pushes, scans only the new commits, since everything before
  them was scanned when it landed. Scheduled and manual runs scan the whole history, so a
  secret that was committed and later deleted is still caught, as is anything a newer rule
  recognises. A push it can't place, such as a new branch or a force push, also gets the full
  scan. It reads `.gitleaks.toml` for allowlists.
- **pip-audit:** checks the listed hash-locked files.
- **tracked-files:** fails if git tracks a file that could hold a credential: `.env` files,
  private keys and certificates. It complements gitleaks, which reads contents and so can miss a
  binary file such as a database an app keeps a token in. `tracked-files-patterns` adds patterns
  (with a `/` they match the path, otherwise the file name); `!pattern` makes an exception, and
  `*.example`, `*.sample` and `*.template` files are always allowed.

## `container-scan`

`container-scan` builds the image and fails on HIGH or CRITICAL vulnerabilities that have a fix
available.

- **`config-scan`:** also fails on misconfigurations in the Dockerfile's directory, such as a
  container that runs as root. A deliberate exception goes in the repo's `.trivyignore`, with a
  comment saying why. Off by default so existing callers don't go red on a library bump;
  `init_repo.py` turns it on for new repos.
