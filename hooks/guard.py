#!/usr/bin/env python3
"""PreToolUse guard: the agent works with env files through envkey, not by reading them.

Blocks file tools on an env file (.env or <name>.env) and shell commands that mention one
outside envkey. A command that encodes the path differently gets through: this keeps honest
mistakes out of the transcript, it is not a sandbox.
"""

import json
import os
import re
import sys

FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep"}
# .env or <name>.env as a separate path element: not .env.example, not .envrc, not \.env in a regex
ENV_TOKEN = re.compile(r"(?:^|[\s/'\"=<>(:])[\w.-]*\.env(?=$|[\s'\";|&)<>])")
ENV_NAME = re.compile(r"[\w.-]*\.env")
# Quoted text with a space is prose (commit message, issue title), not a path
PROSE = re.compile(r"'[^']*\s[^']*'|\"[^\"]*\s[^\"]*\"")
CODE_IDIOMS = re.compile(r"\b(?:process|import\.meta)\.env\b")
# Every part of a chain is checked: "envkey ls; cat .env" must not pass as an envkey call
SEGMENT_SPLIT = re.compile(r"[;&|\n`]|\$\(")
SAFE_STARTS = ("envkey", "git check-ignore")
REASON = ("This project keeps env file values away from the agent. Use envkey: "
          "`envkey ls` for names, `envkey run -- <cmd>` to use values, `envkey set|rm|mv` to edit, "
          "`envkey -f <file>` for a file not named .env. "
          "To ignore the file in git, edit .gitignore with the Edit tool.")


def bash_blocked(command):
    command = CODE_IDIOMS.sub("", PROSE.sub("''", command))
    return any(ENV_TOKEN.search(part) and not part.strip().startswith(SAFE_STARTS)
               for part in SEGMENT_SPLIT.split(command))


def blocked(tool, data):
    if tool in FILE_TOOLS:
        path = data.get("file_path") or data.get("notebook_path") or data.get("path") or ""
        glob = data.get("glob") or ""
        return (bool(ENV_NAME.fullmatch(os.path.basename(path.rstrip("/"))))
                or glob.rstrip("}").endswith(".env"))
    if tool == "Bash":
        return bash_blocked(data.get("command", ""))
    return False


def main():
    try:
        event = json.load(sys.stdin)
    except json.JSONDecodeError:
        return
    if blocked(event.get("tool_name", ""), event.get("tool_input") or {}):
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": REASON,
        }}))


if __name__ == "__main__":
    main()
