#!/bin/sh
# Run a local service with settings from .env (git-ignored), so the launch
# configs stay portable and machine-specific connection strings stay out of git.
#
#   tools/dev.sh api     FastAPI on :8000
#   tools/dev.sh web     Next.js on :3000, pointed at the API
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
  api)
    : "${DATABASE_URL:?set DATABASE_URL in .env}"
    export PYTHONPATH="packages/db:packages/parser:packages/memory:packages/retrieval:packages/graph:packages/ai:apps:.${PYTHONPATH:+:$PYTHONPATH}"
    exec .venv/bin/uvicorn api.main:app --port "${API_PORT:-8000}" \
      --reload --reload-dir apps --reload-dir packages
    ;;
  web)
    export API_URL="${API_URL:-http://localhost:${API_PORT:-8000}/v1}"
    cd apps/web
    exec npx next dev --port "${WEB_PORT:-3000}"
    ;;
  *)
    echo "usage: tools/dev.sh api|web" >&2
    exit 2
    ;;
esac
