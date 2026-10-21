# Demo for the README. Record from the repository root: vhs docs/demo.tape
# Needs vhs and jq. Everything runs in a throwaway directory with a made-up token, never in a real project.
Output assets/demo.gif

Require jq

Set Shell "bash"
Set FontSize 16
Set Width 1200
Set Height 640
Set Padding 16
Set TypingSpeed 35ms

Hide
Type "ROOT=$PWD && export PATH=$ROOT/bin:$PATH && export PS1='$ ' && mkdir -p $(mktemp -d)/shop && cd $_ && git init -q && echo .env > .gitignore && clear" Enter
Type `guard() { printf '{"tool_name":"Bash","tool_input":{"command":"%s"}}' "$1" | python3 $ROOT/hooks/guard.py | jq -r '.hookSpecificOutput | .permissionDecision + ": " + .permissionDecisionReason' | fold -s -w 100; }` Enter
Type "clear" Enter
Show

Type "# the agent stores a secret it produced itself" Enter
Type "echo 'fake-token-8f3a91c2d4' | envkey set API_TOKEN" Enter
Sleep 800ms
Type "envkey set --public PORT 8080" Enter
Sleep 800ms
Type "envkey ls" Enter
Sleep 2s

Type "clear" Enter
Type "# a command that needs the key; its output reaches the agent through a pipe" Enter
Type `envkey run -- sh -c 'echo "port=$PORT token=$API_TOKEN"' | cat` Enter
Sleep 2500ms

Type "clear" Enter
Type "# the hook refuses a direct read of .env and points to envkey" Enter
Type "guard 'cat .env'" Enter
Sleep 4s
