#!/bin/bash
# SessionStart hook (.claude/settings.json). In Claude Code cloud sessions only,
# install the project's dependencies so `uv run pytest`, `ruff` and `mypy` work
# from the first command. On your own computer it does nothing.
# --frozen installs exactly what uv.lock says and never rewrites it (the cloud's
# uv may be newer than the uv 0.12.19 the Dockerfile pins).
# It never fails the session: a failed install is reported, and `uv run`
# tries again on first use.

[ "$CLAUDE_CODE_REMOTE" = "true" ] || exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

log=/tmp/uv-sync.log
if uv sync --frozen >"$log" 2>&1; then
  echo "Dependencies installed with uv sync."
elif UV_NATIVE_TLS=true uv sync --frozen >>"$log" 2>&1; then
  # The cloud's network proxy can need the system certificate store; keep
  # using it for the rest of the session.
  [ -n "$CLAUDE_ENV_FILE" ] && echo "export UV_NATIVE_TLS=true" >>"$CLAUDE_ENV_FILE"
  echo "Dependencies installed with uv sync (using system certificates)."
else
  echo "uv sync failed (details in $log). Run uv sync again before testing."
fi
exit 0
