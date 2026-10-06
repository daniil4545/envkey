#!/usr/bin/env python3
"""PreToolUse guard: the agent works with .env through envkey, not by reading the file.

Blocks file tools on a path named .env and shell commands that mention .env outside
envkey. A command that encodes the path differently gets through: this keeps honest
mistakes out of the transcript, it is not a sandbox.
"""

import json
import os
import re
import sys

FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit", "Grep"}
# .env as a separate path element: not .env.example, not .envrc, not \.env in a regex
ENV_TOKEN = re.compile(r"(?:^|[\s/'\"=<>(:])\.env(?=$|[\s'\";|&)<>])")
REASON = ("This project keeps .env values away from the agent. Use envkey: "
          "`envkey ls` for names, `envkey run -- <cmd>` to use values, `envkey set|rm|mv` to edit. "
          "To ignore the file in git, edit .gitignore with the Edit tool.")


def blocked(tool, data):
    if tool in FILE_TOOLS:
        path = data.get("file_path") or data.get("notebook_path") or data.get("path") or ""
        return os.path.basename(path.rstrip("/")) == ".env"
    if tool == "Bash":
        command = data.get("command", "")
        return bool(ENV_TOKEN.search(command)) and not command.lstrip().startswith("envkey")
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
