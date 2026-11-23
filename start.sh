#!/bin/sh
set -eu

if [ -n "${DATABASE_URL:-}" ]; then
  alembic upgrade head
  alembic current
fi

exec python -m bot.main
