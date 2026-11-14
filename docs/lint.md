# Lint

| Workflow | Inputs (defaults) | Caller grants |
|---|---|---|
| `lint.yml` | `runs-on` (ubuntu-latest), `shellcheck` (true), `markdown` (true), `json` (true), `toml` (true), `yaml` (true), `actionlint` (true), `powershell` (true), `clang-format` (true), `pinned-runners` (false) | `contents: read` |

## `lint`

`lint` shellchecks every tracked shell script, found by its `*.sh` or `*.bash` extension or by a
`sh`, `bash`, `dash` or `ksh` shebang, checks that every tracked `*.json` and `*.toml` file parses,
and lints Markdown with [rumdl](https://github.com/rvben/rumdl), which implements the markdownlint
rules. Each check is skipped when there's nothing to check.

- **Markdown fences:** `markdown` also fails on a code fence with text after it, or a code
  block that never closes. Under CommonMark neither ends the block, so the prose after it is
  read as code and the markdownlint rules, which skip code, never see it.
- **Markdown config:** rumdl reads the repo's `.markdownlint.json`/`.yaml`, `.rumdl.toml` or
  `[tool.rumdl]` in `pyproject.toml`, so a repo sets line length and exclusions there. Without
  one it uses the markdownlint defaults, including 80-column lines. Gitignored files are skipped.
- **YAML:** lints every tracked `*.yml` and `*.yaml` file with
  [yamllint](https://github.com/adrienverge/yamllint). With the repo's `.yamllint` config it
  reports whatever that asks for; without one, only real errors such as invalid syntax,
  duplicate keys and trailing spaces.
- **actionlint:** checks the workflows for expression typos, bad inputs, unknown runner labels
  and YAML mistakes, and shellchecks their `run:` scripts.
- **Pinned runners:** with `pinned-runners`, fails on `ubuntu-latest`, `windows-latest` or
  `macos-latest` anywhere in the repo's workflows outside comments, matrix lists and expressions
  included. GitHub moves those labels to a new image without a commit, and a build moves with
  them. Every reusable workflow here takes `runs-on`, so a caller can name a version for each.
- **PowerShell:** runs
  [PSScriptAnalyzer](https://github.com/PowerShell/PSScriptAnalyzer) over every tracked `*.ps1`,
  `*.psm1` and `*.psd1` file, such as an installer smoke test. With the repo's
  `PSScriptAnalyzerSettings.psd1` everything it reports fails; without one, only errors do, such
  as a script that doesn't parse or a plaintext `ConvertTo-SecureString`. It uses the
  PowerShell and PSScriptAnalyzer that GitHub's runner image ships.
- **clang-format:** in a repo with a `.clang-format`, fails on any tracked C or C++ source it
  would reformat, annotating each spot and printing the `clang-format -i` command that fixes
  them. `.clang-format-ignore` leaves out vendored code. Repos without a `.clang-format` are
  skipped, since clang-format's default style would flag code that never chose one.
