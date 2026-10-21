# envkey - context for AI agents

## Project

Claude Code plugin: the `envkey` command, a skill that teaches the agent to use it, and a
PreToolUse hook that keeps the agent's tools away from `.env`. Scope is `.env` only: one tool,
one job. User-facing description - `README.md`.

## Principles

- No envkey command prints a secret value or takes it as an argument.
- Python stdlib only; the plugin works right after install, no pip.
- Zero configuration for the user; the only user touch is typing a secret into the dialog.

## Structure

| Path | What it is |
|---|---|
| `bin/envkey` | the command; Claude Code puts plugin `bin/` on the agent's PATH |
| `skills/envkey/SKILL.md` | when and how the agent uses envkey |
| `hooks/hooks.json`, `hooks/guard.py` | PreToolUse guard against direct `.env` access |
| `.claude-plugin/` | plugin manifest and single-plugin marketplace |
| `tests/` | tests for the command and the guard |
| `docs/demo.tape`, `assets/demo.gif` | README demo, recorded with `vhs docs/demo.tape` |

## Commands

| What | Command |
|---|---|
| tests | `python3 -m unittest discover -s tests -v` |
| lint | `uvx ruff@0.14.0 check .` |
| validate manifests | `claude plugin validate .` |
| local plugin run | `claude --plugin-dir .` |

## Workflow

- Branch `main`, GitHub (public), CI in `.github/workflows/ci.yml`: ruff and tests on Linux and macOS.
- Release: bump `version` in both manifests, add a `## [X.Y.Z] - date` section to `CHANGELOG.md`,
  tag `vX.Y.Z`. `release.yml` checks the three agree and publishes the section as the release notes.
- Commits: conventional commits in English. The project is public, so all text is English.

## Critical invariants

- A secret value never reaches stdout, stderr or argv of envkey.
- `envkey set` never writes to a file git does not ignore.
- `envkey run` returns the child's exit code.
