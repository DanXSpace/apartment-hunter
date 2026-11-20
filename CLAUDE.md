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

## Environment

### Dependencies

Change dependencies with `uvx uv@0.12.19 add …` / `remove …`, the uv the Dockerfile pins: it keeps `uv.lock` at revision 3, which the image's `uv sync --frozen` reads. Other uv versions rewrite the whole file.

### Cloud sessions

A cloud session reaches PyPI and GitHub, not Zillow, Craigslist or Discord: refreshing fixtures, running `scripts/feasibility_check.py` and running the bot happen on the owner's computer or in a GitHub Actions job. Reach GitHub through `gh api` (REST) or the built-in GitHub tools; the cloud's GitHub proxy refuses `gh issue` and `gh pr` (GraphQL), tag pushes and branch deletions.
