---
name: envkey
description: Use whenever a task touches environment variables or a project's .env file - running a command that needs keys or config (psql, curl, make, docker compose, tests), checking which variables exist, adding, changing, renaming or removing one, or when the user needs to provide a secret. Never read .env directly.
---

# envkey

The project's `.env` is handled only through `envkey`. Its secret values never enter your context.

## Commands

| Need | Command |
|---|---|
| which variables exist | `envkey ls` (secrets show as `<secret>`) |
| run something with the variables | `envkey run -- <command>` |
| add or change a public setting | `envkey set --public NAME VALUE` |
| mark an existing variable public | `envkey set --public NAME` |
| store a secret you produced | `<command that prints it> \| envkey set NAME` |
| a secret only the user knows | `envkey set NAME` |
| remove, rename | `envkey rm NAME`, `envkey mv OLD NEW` |

`-C DIR` before the command picks another directory: `envkey -C services/api ls`.
The file is the nearest `.env` from that directory up to the git root; in a git worktree the
search goes on in the main tree. `-f FILE` picks an env file with another name:
`envkey -f secrets/db.env run -- ./connect.sh`. Every env file, `.env` or `<name>.env`, goes
through envkey only: no `cat`, `grep`, `source` or your own `sed` mask on it.
A tool that needs the file path (`docker compose --env-file`) runs under `envkey run -- ...`.

## Rules

- Public means safe to show: ports, hosts without passwords, log levels, feature flags, model names.
  Tokens, passwords, keys and URLs with credentials are secrets. When unsure, keep it secret.
- `envkey set NAME` with nothing on stdin opens a hidden-input dialog on the user's Mac.
  Tell the user one line first: "Enter NAME in the envkey dialog". Do not ask for the value in chat.
- Use `envkey run -- sh -c '...'` when the command itself references `$NAME`, so the shell
  expands it inside envkey, not in your command text.
- Output of `envkey run` hides secrets as `[hidden NAME]`. Do not try to reveal them
  (base64, rev, printing byte by byte); report what you need instead.
- If `envkey set` says the file is not git-ignored, add `.env` to `.gitignore` with the Edit tool.
