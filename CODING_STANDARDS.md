# Coding standards

Read during review. Every rule here is a judgement call. Ruff and mypy enforce the mechanical rules (`uv run ruff check`, `uv run mypy`, both in CI), so a review leaves those to the tooling.

## Tests

- **Test at the seam.** Each test drives a module through its public interface and asserts on results a user or caller can observe: return values, messages a fake Notifier recorded, state read back through the same interface. Later, the web app is tested through its HTTP interface (FastAPI `TestClient` on the application factory). Ruff `SLF001` catches reaching into `_private` attributes. The judgement call is a test that stays on public names but still pins internal structure (call order between collaborators, template markup beyond the visible text and controls).
- **Hand-written fakes.** Fakes are small classes in the test file, in the style of `FakeScraper` / `FakeNotifier` in `tests/test_runner.py`. A fake scripts results and errors, and records what it was sent. Ruff `TID251` bans mocking libraries and Playwright in `tests/`.
- **Deterministic and offline.** Time arrives as an injected clock. Background work runs through a synchronous hook (for example `Scheduler.run_pending`). Tests contain no real threads or sleeps, and never touch apartments.com, Discord or GitHub. Pytest allows connections to loopback only and has a 10 s timeout (pyproject), so a network call or a hang fails the test.
- **Independent expected values.** Assert against literals or worked examples, not values recomputed the way the code computes them.

## Behaviour the CLI promises

- The CLI, `config.toml` format and Docker image keep working as in the previous release unless a ticket says otherwise. A diff that changes CLI output, exit codes or config handling should name the change in its commit message.
