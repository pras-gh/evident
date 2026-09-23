#!/bin/sh
# One-time setup from a clean checkout: the Python venv, the web app's
# packages, and a local database with the demo loaded.
#
#   tools/setup.sh            everything
#   tools/setup.sh --no-seed  skip loading the demo filings
set -e
cd "$(dirname "$0")/.."

PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || { echo "setup: Python 3.11+ required ($PYTHON_BIN is $("$PYTHON_BIN" --version 2>&1))" >&2; exit 1; }

if [ ! -x .venv/bin/python ]; then
  echo "setup: creating .venv"
  "$PYTHON_BIN" -m venv .venv
fi
echo "setup: installing Python dependencies"
.venv/bin/pip install -q --disable-pip-version-check -r requirements-dev.txt

echo "setup: installing web dependencies"
(cd apps/web && npm install --silent --no-audit --no-fund)

tools/db.sh up
[ "$1" = "--no-seed" ] || tools/db.sh seed

cat <<'DONE'

setup: done. Run the two servers, each in its own terminal:
  tools/dev.sh api      http://localhost:8000/docs
  tools/dev.sh web      http://localhost:3000
DONE
