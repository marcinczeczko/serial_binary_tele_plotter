#!/bin/bash
# SessionStart hook for Claude Code on the web: makes `uv run pytest`, ruff and mypy work
# in a fresh cloud container (the same setup as .github/workflows/ci.yml).
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}"

# Qt runtime libraries, so the `qt` tests run instead of skipping.
QT_LIBS="libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3 libglib2.0-0t64"
missing=""
for pkg in $QT_LIBS; do
  dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"
done
if [ -n "$missing" ]; then
  SUDO=""
  [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  if $SUDO apt-get update -qq && $SUDO apt-get install -y -qq --no-install-recommends $missing; then
    echo "installed:$missing"
  else
    echo "could not install$missing; run the tests with: uv run pytest -p no:pytest-qt" >&2
  fi
fi

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  [ -n "${CLAUDE_ENV_FILE:-}" ] && echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$CLAUDE_ENV_FILE"
fi

# Python 3.14, runtime + dev deps + pyarrow into .venv (uv fetches Python if needed).
uv sync --all-extras

[ -n "${CLAUDE_ENV_FILE:-}" ] && echo 'export QT_QPA_PLATFORM=offscreen' >> "$CLAUDE_ENV_FILE"
exit 0
