# envkey

A Claude Code plugin that gives the agent hands for a project's `.env`: it lists, uses, adds,
renames and removes variables, and never reads secret values.

## Install

```sh
claude plugin marketplace add daniil4545/envkey
claude plugin install envkey@envkey
```

Restart Claude Code. Nothing to configure: the agent learns the tool from the plugin's skill.

Requirements: macOS, Python 3.9+, git.

## What the agent gets

| Command | What it does |
|---|---|
| `envkey ls` | variable names; values only for public ones, `<secret>` for the rest |
| `envkey run -- <cmd>` | runs `<cmd>` with `.env` in its environment; secrets in the output become `[hidden NAME]` |
| `envkey set --public NAME VALUE` | adds or changes a public variable |
| `envkey set NAME` | stores a secret: from stdin, or the user types it into a hidden-input macOS dialog |
| `envkey rm NAME`, `envkey mv OLD NEW` | removes, renames |

`-C DIR` before the command starts the search in another directory. The file is the nearest
`.env` from there up to the git root. It stays a plain dotenv file that `make` and
`docker compose` read as before.

A variable is public only when the line above it is `# public`. Everything else is a secret.

## What the user does

Only one thing, and only when the agent needs a secret it cannot get itself: type the value into
the envkey dialog. The value goes into `.env` and never into the chat.

## Limits

- The plugin's hook stops the agent's file tools and shell commands from touching `.env` directly.
  It catches mistakes, not a determined command: `.env` is a plain file readable by your user.
- Masking matches exact values. A transformed value (`base64`) passes through.
- Values shorter than 6 characters are not masked.
- `envkey set` writes only to a file that git ignores, with mode 0600.

## Development

Tests: `python3 -m unittest discover -s tests -v`. Rules for agents: `AGENTS.md`.
