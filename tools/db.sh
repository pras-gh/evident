#!/bin/sh
# A local PostgreSQL for Evident, kept in .pgdata/ (git-ignored). Nothing
# system-wide is installed, started or changed: it is a private cluster on its
# own port, started and stopped by this script.
#
#   tools/db.sh up       create if needed, start, create the database, migrate
#   tools/db.sh seed     load three NVIDIA 10-Ks (keyword extraction, no API key)
#   tools/db.sh status   is it running, and at what URL
#   tools/db.sh url      print DATABASE_URL
#   tools/db.sh stop
#
# Needs PostgreSQL 15 or newer — migration 0009 relies on NULLS NOT DISTINCT —
# with pgvector. On macOS: brew install postgresql@17 pgvector. The newest
# Homebrew postgresql@N is used unless PG_BIN names a bin directory.
#
# Settings: EVIDENT_DB_PORT (5434), EVIDENT_DB_NAME (evident), PG_BIN.
set -e
cd "$(dirname "$0")/.."
ROOT=$(pwd)
DATA="$ROOT/.pgdata"
PORT="${EVIDENT_DB_PORT:-5434}"
NAME="${EVIDENT_DB_NAME:-evident}"
USER_NAME="${USER:-$(id -un)}"
URL="postgresql+psycopg://$USER_NAME@localhost:$PORT/$NAME"
PY="${PYTHON:-$ROOT/.venv/bin/python}"

die() { echo "db: $*" >&2; exit 1; }

find_bin() {
  if [ -n "$PG_BIN" ]; then echo "$PG_BIN"; return; fi
  if command -v brew >/dev/null 2>&1; then
    for v in 18 17 16 15; do
      d="$(brew --prefix "postgresql@$v" 2>/dev/null)/bin"
      [ -x "$d/postgres" ] && { echo "$d"; return; }
    done
  fi
  command -v pg_config >/dev/null 2>&1 && pg_config --bindir
}

BIN=$(find_bin)
[ -n "$BIN" ] && [ -x "$BIN/postgres" ] || die "no PostgreSQL found. Install 15+ (brew install postgresql@17 pgvector) or set PG_BIN."

check_version() {
  major=$("$BIN/postgres" --version | sed -E 's/[^0-9]*([0-9]+).*/\1/')
  [ "$major" -ge 15 ] || die "$BIN is PostgreSQL $major; Evident needs 15+ (migration 0009 uses NULLS NOT DISTINCT). Install a newer one or set PG_BIN."
  [ -f "$("$BIN/pg_config" --sharedir)/extension/vector.control" ] \
    || die "pgvector is not installed for PostgreSQL $major ($BIN). brew install pgvector, or build it against this server."
}

# The server gets a fixed locale. On macOS, starting it without a valid one in
# the environment aborts with "postmaster became multithreaded during startup";
# the cluster is created with --no-locale, so C is also what it expects.
pg() { LC_ALL=C LANG=C "$@"; }

running() { pg "$BIN/pg_ctl" -D "$DATA" status >/dev/null 2>&1; }

psql_() { PGOPTIONS="-c client_min_messages=warning" "$BIN/psql" -h localhost -p "$PORT" -U "$USER_NAME" -X -q -v ON_ERROR_STOP=1 "$@"; }

up() {
  check_version
  if [ ! -f "$DATA/PG_VERSION" ]; then
    echo "db: creating a cluster in .pgdata"
    # trust is safe only because the server listens on localhost alone
    pg "$BIN/initdb" -D "$DATA" -U "$USER_NAME" -A trust -E UTF8 --no-locale >/dev/null
    {
      echo "listen_addresses = 'localhost'"
      echo "port = $PORT"
      echo "unix_socket_directories = ''"
    } >> "$DATA/postgresql.conf"
  fi
  if ! running; then
    pg "$BIN/pg_ctl" -D "$DATA" -l "$DATA/server.log" -w start >/dev/null \
      || die "server did not start; see .pgdata/server.log"
  fi
  if [ -z "$(psql_ -d postgres -Atc "select 1 from pg_database where datname = '$NAME'")" ]; then
    psql_ -d postgres -c "create database \"$NAME\""
  fi
  psql_ -d "$NAME" -c "create extension if not exists vector"
  [ -x "$PY" ] || die "no Python at $PY. Run tools/setup.sh first, or set PYTHON."
  out=$(cd db && DATABASE_URL="$URL" "$PY" -m alembic upgrade head 2>&1) \
    || { echo "$out" >&2; die "migration failed"; }
  echo "$out" | sed -n 's/.*Running upgrade /db: migrated /p'
  echo "db: ready — DATABASE_URL=$URL"
  write_env
}

# Point .env at this database if there is no .env yet. An existing .env is
# the user's and is never rewritten; if it points elsewhere, say so.
write_env() {
  if [ ! -f .env ]; then
    sed "s|^DATABASE_URL=.*|DATABASE_URL=$URL|" .env.example > .env
    echo "db: wrote .env from .env.example"
  elif ! grep -q "^DATABASE_URL=$URL\$" .env; then
    echo "db: note — .env sets a different DATABASE_URL; to use this database, set"
    echo "    DATABASE_URL=$URL"
  fi
}

case "$1" in
  up) up ;;
  seed)
    running || die "not running — tools/db.sh up"
    DATABASE_URL="$URL" "$PY" tools/seed_demo.py --corpus timeline
    ;;
  stop)
    if running; then pg "$BIN/pg_ctl" -D "$DATA" -w stop >/dev/null; echo "db: stopped"; else echo "db: not running"; fi
    ;;
  status)
    if running; then echo "db: running on port $PORT — $URL"; else echo "db: not running"; exit 1; fi
    ;;
  url) echo "$URL" ;;
  *) echo "usage: tools/db.sh up|seed|status|url|stop" >&2; exit 2 ;;
esac
