# Agent instructions

This repo is meant to become a drop-in personal assistant: one AI API key (or later auth), then tools, research, MCP, automations, and computer use.

## Merge policy (required)

All agent and Cursor automation work that changes this repository **must land on `main`**.

Do not stop at a draft pull request. After committing:

1. Push the branch.
2. Open a PR **targeting `main`**.
3. Mark the PR **ready for review** (`gh pr ready`). Cursor's `open_git_pr` tool often creates drafts; a draft cannot be merged.
4. Merge into `main` (`gh pr merge --merge`, or fast-forward `main` and `git push origin main`).
5. Confirm `origin/main` contains the new commits before finishing.

If a previous automation run left a draft PR that already contains the work, mark that PR ready and merge it instead of opening another.

Daily improvement runs should keep using PRs so CI runs, then always complete the merge to `main` in the same run.
