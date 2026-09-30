# CLAUDE.md

@AGENTS.md

## Claude Code specifics

- Path-scoped rules live in `.claude/rules/` and load automatically for matching files. Keep them short;
  the full detail is in `docs/`.
- Slash commands: `/spec <feature>` (write/update a spec before coding), `/next-task` (pick up the next plan task),
  `/eval [retrieval|router|full]` (run eval and compare with the baseline).
- Subagents: `code-reviewer` (run before committing), `eval-analyst` (diagnose eval failures).
- Permissions and the format-on-edit hook are in `.claude/settings.json`. Don't read `.env` files.
