# Compose

| Workflow | Inputs (defaults) | Caller grants |
|---|---|---|
| `compose-test.yml` | `compose-file` (compose.yml), `health-url`, `timeout` (180), `wait` (true), `commands` | `contents: read` |

## `compose-test`

`compose-test` builds and starts a Compose stack, waits for every service with a healthcheck to
report healthy (and for `health-url`, when given), then runs `commands` against it. Logs are printed
on failure and the stack is always removed, volumes included.

- **One-shot services:** compose counts a container that runs and exits, such as a migration
  or seed job, as a failure to start. For a stack with one, set `wait: false` and give
  `health-url` instead.
