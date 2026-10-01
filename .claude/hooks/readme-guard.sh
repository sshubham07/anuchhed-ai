#!/usr/bin/env bash
# README guard: block commits whose changes the README describes but README.md
# (or its rendered diagrams) wasn't updated. Bypass with "[skip readme]" in the
# commit message (git mode strips the token from the final message).
#
# Two modes, one rule set:
#   readme-guard.sh               Claude Code PreToolUse(Bash) hook; JSON on stdin.
#                                 Checks the working tree, since the command often
#                                 chains `git add … && git commit`.
#   readme-guard.sh --git MSGFILE git commit-msg hook (`make hooks`); checks staged files.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

if [ "${1:-}" = "--git" ]; then
  msgfile=$2
  if grep -qF '[skip readme]' "$msgfile"; then
    sed -i.bak 's/[[:space:]]*\[skip readme\]//' "$msgfile" && rm -f "$msgfile.bak"
    exit 0
  fi
  changed=$(git diff --cached --name-only)
  deny() { printf '\n✋ README guard: %s\n   (git commit --no-verify also skips it)\n\n' "$1" >&2; exit 1; }
else
  cmd=$(jq -r '.tool_input.command // ""')
  grep -qE '(^|[;&|[:space:]])git[[:space:]]+commit' <<<"$cmd" || exit 0
  grep -qF '[skip readme]' <<<"$cmd" && exit 0
  changed=$(git status --porcelain --untracked-files=all | awk '{print $NF}')
  deny() {
    jq -n --arg r "$1" '{hookSpecificOutput: {hookEventName: "PreToolUse",
      permissionDecision: "deny", permissionDecisionReason: $r}}'
    exit 0
  }
fi
[ -z "$changed" ] && exit 0

# Diagram sources edited but SVGs not re-rendered.
stale=""
for f in $(grep -E '^docs/diagrams/.*\.mmd$' <<<"$changed" || true); do
  svg="${f%.mmd}.svg"
  { [ -f "$svg" ] && [ "$svg" -nt "$f" ]; } || stale="$stale $f"
done
[ -n "$stale" ] && deny "Diagram sources changed but SVGs are stale:$stale. Run \`make diagrams\`, check the images, then commit again."

# Project changes that the README describes, but README.md untouched.
relevant=$(grep -E '^(src/|migrations/|eval/|ui/|tests/|prompts/|docs/(plans|specs|adr|design|diagrams)/|Makefile$|pyproject\.toml$|docker-compose\.yml$)' <<<"$changed" || true)
if [ -n "$relevant" ] && ! grep -qx 'README.md' <<<"$changed"; then
  deny "README.md not updated for this commit. Changed: $(tr '\n' ' ' <<<"$relevant"). Update README.md first: status badges (phase, test count, chunk/article numbers), the 'Where we are' phase list (tick done steps, move the ⏳ next marker), and any diagrams under docs/diagrams (then \`make diagrams\`). Then stage README.md and commit again. If the README truly needs no change, put \"[skip readme]\" in the commit message."
fi
exit 0
