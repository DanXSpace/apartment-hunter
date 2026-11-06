## Agent skills

### Issue tracker

Issues and specs live in GitHub Issues on DanXSpace/apartment-hunter (via `gh`). See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `GLOSSARY.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Coding standards

Review rules (judgement calls) live in `CODING_STANDARDS.md`; lint and types run via `uv run ruff check` and `uv run mypy`.

### TDD and review

A spec's **Testing Decisions** section is the pre-agreed seam list for `/tdd`. To review uncommitted work, run `git add -N` on new files first so `git diff HEAD` includes them.
