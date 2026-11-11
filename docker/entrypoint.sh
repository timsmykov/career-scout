#!/bin/sh
set -eu
umask 077
exec /app/.venv/bin/python -m career_scout \
  --db /data/ledger.sqlite3 \
  --env /run/secrets/career_env \
  --token-file /data/auth/hh-token.json "$@"
