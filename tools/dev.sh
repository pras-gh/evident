#!/bin/sh
# Run a local service with settings from .env (git-ignored), so the launch
# configs stay portable and machine-specific connection strings stay out of git.
#
#   tools/dev.sh api     FastAPI on :8000
#   tools/dev.sh web     Next.js on :3000, pointed at the API
#   tools/dev.sh sync    the S&P 500 sync: backfill, then new filings every 10 min
set -e
cd "$(dirname "$0")/.."
# Parsed as KEY=value data, not sourced as shell: connection strings carry
# `&` and `?`, and sourcing would read `...host=/tmp&port=5433` as a
# background job and silently leave DATABASE_URL unset.
if [ -f .env ]; then
  while IFS='=' read -r key value || [ -n "$key" ]; do
    case "$key" in ''|\#*) continue ;; esac
    value=${value%\"}; value=${value#\"}
    export "$key=$value"
  done < .env
fi

case "$1" in
  api|sync)
    : "${DATABASE_URL:?set DATABASE_URL in .env — tools/db.sh up creates a database and prints it}"
    [ -x .venv/bin/python ] || { echo "dev: no .venv — run tools/setup.sh" >&2; exit 1; }
    export PYTHONPATH="packages/db:packages/parser:packages/memory:packages/retrieval:packages/graph:packages/ai:apps:.${PYTHONPATH:+:$PYTHONPATH}"
    # The private cluster in .pgdata isn't a server the app restarts: when the
    # app quits, it stops with everything else, and the API would then refuse
    # to start. Bring it back first, but only when DATABASE_URL is that
    # cluster; any other database is left alone.
    if [ "$DATABASE_URL" = "$(sh tools/db.sh url 2>/dev/null)" ] \
       && ! sh tools/db.sh status >/dev/null 2>&1; then
      echo "dev: the local database isn't running; starting it"
      sh tools/db.sh up >/dev/null || exit 1   # its errors are on stderr
    fi
    # Fail here, with the fix, rather than on the first request.
    .venv/bin/python tools/check_db.py || exit 1
    if [ "$1" = sync ]; then
      exec .venv/bin/python -m workers.sync_worker watch
    fi
    exec .venv/bin/uvicorn api.main:app --port "${API_PORT:-8000}" \
      --reload --reload-dir apps --reload-dir packages
    ;;
  web)
    export API_URL="${API_URL:-http://localhost:${API_PORT:-8000}/v1}"
    cd apps/web
    exec npx next dev --port "${WEB_PORT:-3000}"
    ;;
  *)
    echo "usage: tools/dev.sh api|web|sync" >&2
    exit 2
    ;;
esac
