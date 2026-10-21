# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-21

First public release.

### Added

- `envkey` command: `ls`, `run`, `set`, `rm` and `mv` for a project's `.env`, with `-C DIR` to
  start the search elsewhere and `-f FILE` for an env file with any name.
- Output of `envkey run` hides every secret value as `[hidden NAME]`, longest values first, and
  returns the exit code of the child. A variable is public only when the line above it is
  `# public`.
- `envkey set` takes a secret from stdin, a hidden terminal prompt or a macOS dialog. It refuses
  a file that git does not ignore and a value that `make` and `docker compose` would read
  differently, and writes the file atomically with mode 0600.
- Parsing like docker compose: quoted values, multi-line values, CRLF line endings. A line that
  does not parse is reported by its number.
- Search from the current directory up to the git root, continuing in the main tree for a linked
  git worktree.
- PreToolUse hook that refuses Read, Edit, Write, Grep and shell commands on `.env` and
  `<name>.env`, including chained, quoted and substituted commands, and points the agent to
  envkey.
- Skill that teaches the agent to use envkey for anything about environment variables.
- Plugin manifest and single-plugin marketplace, installable with
  `claude plugin marketplace add daniil4545/envkey`.
- README with a terminal demo, tests, ruff lint and CI on Linux and macOS.

[Unreleased]: https://github.com/daniil4545/envkey/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/daniil4545/envkey/releases/tag/v0.2.0
