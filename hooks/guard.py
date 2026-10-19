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
# .env, <name>.env, .env.local as a separate path element: not .env.example, not .envrc, not \.env in a regex
NAME = r"[\w.-]*\.env(?:\.(?!(?:example|sample|template|dist)\b)[\w-]+)?"
ENV_TOKEN = re.compile(r"(?:^|[\s/'\"=<>(:])" + NAME + r"(?=$|[\s'\";|&)<>])")
ENV_NAME = re.compile(NAME)
CODE_IDIOMS = re.compile(r"\b(?:process|import\.meta)\.env\b")
# Every part of a chain is checked: "envkey ls; cat .env" must not pass as an envkey call
SEGMENT_SPLIT = re.compile(r"[;&|\n`]|\$\(")
SAFE_STARTS = ("envkey", "git check-ignore")
REASON = ("This project keeps env file values away from the agent. Use envkey: "
          "`envkey ls` for names, `envkey run -- <cmd>` to use values, `envkey set|rm|mv` to edit, "
          "`envkey -f <file>` for a file not named .env. "
          "To ignore the file in git, edit .gitignore with the Edit tool.")


def closing_paren(command, start):
    depth = 1
    for index in range(start, len(command)):
        depth += {"(": 1, ")": -1}.get(command[index], 0)
        if depth == 0:
            return index
    return len(command)


def flatten(command):
    """Command with quoted prose blanked, plus the code hidden in quotes to check on its own.

    Quoted text with a space is prose (commit message, issue title), not a path. Code inside
    quotes still runs: $(...) and `...` in double quotes, the argument of sh -c.
    """
    out, nested, index = [], [], 0
    while index < len(command):
        char = command[index]
        if char == "\\":
            out.append(command[index:index + 2])
            index += 2
            continue
        if char not in "'\"":
            out.append(char)
            index += 1
            continue
        text, index = [], index + 1
        while index < len(command) and command[index] != char:
            if char == '"' and command[index] == "\\":
                text.append(command[index:index + 2])
                index += 2
            elif char == '"' and command.startswith("$(", index):
                end = closing_paren(command, index + 2)
                nested.append(command[index + 2:end])
                text.append(" ")
                index = end + 1
            elif char == '"' and command[index] == "`":
                end = command.find("`", index + 1)
                end = len(command) if end < 0 else end
                nested.append(command[index + 1:end])
                text.append(" ")
                index = end + 1
            else:
                text.append(command[index])
                index += 1
        index += 1
        text = "".join(text)
        before = "".join(out).split()
        if before and before[-1] == "-c":
            nested.append(text)
            out.append("''")
        elif re.search(r"\s", text):
            out.append("''")
        else:
            out.append(char + text + char)
    return "".join(out), nested


def bash_blocked(command):
    flat, nested = flatten(command)
    flat = CODE_IDIOMS.sub("", flat)
    return (any(ENV_TOKEN.search(part) and not part.strip().startswith(SAFE_STARTS)
                for part in SEGMENT_SPLIT.split(flat))
            or any(bash_blocked(code) for code in nested))


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
