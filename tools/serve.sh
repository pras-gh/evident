#!/bin/sh
# The backend image's entrypoint: one image, a role per service.
#
#   tools/serve.sh api       the API on $PORT, behind the host's proxy
#   tools/serve.sh sync      the S&P 500 sync: backfill, then new filings every 10 min
#   tools/serve.sh migrate   alembic upgrade head: the API service's pre-deploy command
#   tools/serve.sh check     can DATABASE_URL serve the API: version, pgvector, migration
#
# The role is the first argument, else PARALLX_ROLE, else api. Settings come
# from the environment (the host's variables); nothing is read from .env.
set -e
cd "$(dirname "$0")/.."
role="${1:-${PARALLX_ROLE:-api}}"

case "$role" in
  api)
    # The host's proxy terminates TLS and says so in X-Forwarded-*; trust it,
    # or every request looks like plain http from the proxy's address.
    # WEB_CONCURRENCY, when set, is how many processes uvicorn runs.
    exec python -m uvicorn api.main:app --host "${HOST:-0.0.0.0}" --port "${PORT:-8080}" \
      --proxy-headers --forwarded-allow-ips "${FORWARDED_ALLOW_IPS:-*}" --no-server-header
    ;;
  sync)
    exec python -m workers.sync_worker watch
    ;;
  migrate)
    # MIGRATION_DATABASE_URL, when set, is a connection suited to DDL (a
    # pooler's session mode, or the direct host) for when DATABASE_URL isn't.
    cd db
    DATABASE_URL="${MIGRATION_DATABASE_URL:-$DATABASE_URL}" exec python -m alembic upgrade head
    ;;
  check)
    exec python tools/check_db.py
    ;;
  *)
    echo "serve: unknown role '$role' (api, sync, migrate or check)" >&2
    exit 2
    ;;
esac
