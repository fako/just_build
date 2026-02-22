#!/usr/bin/env bash
set -euo pipefail

# Required inputs
: "${DATABASE_NAME:?DATABASE_NAME is required}"
: "${DATABASE_USER:?DATABASE_USER is required}"
: "${DATABASE_PASSWORD:?DATABASE_PASSWORD is required}"

# Defaults (from the official image)
: "${POSTGRES_USER:=postgres}"
: "${POSTGRES_DB:=postgres}"

# Optional safety: don't drop the primary DB unless FORCE=1
if [[ "$DATABASE_NAME" == "$POSTGRES_DB" && "${FORCE:-0}" != "1" ]]; then
  echo "Refusing to drop primary DB ($POSTGRES_DB). Set FORCE=1 to override."
  exit 1
fi

# Helper to run a single SQL against the control DB (usually 'postgres')
psqlc() {
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"
}

# 1) If DB exists: terminate connections and drop it (no transaction)
if [[ -n "$(psqlc "SELECT 1 FROM pg_database WHERE datname = '$DATABASE_NAME'")" ]]; then
  # terminate other sessions
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "SELECT pg_terminate_backend(pid)
     FROM pg_stat_activity
     WHERE datname = '$DATABASE_NAME' AND pid <> pg_backend_pid();"

  # drop the database
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "DROP DATABASE \"$DATABASE_NAME\";"
fi

# 2) If role exists: drop its objects in the control DB and drop the role
if [[ -n "$(psqlc "SELECT 1 FROM pg_roles WHERE rolname = '$DATABASE_USER'")" ]]; then
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "DROP OWNED BY \"$DATABASE_USER\" CASCADE;"
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "DROP ROLE \"$DATABASE_USER\";"
fi

# 3) Recreate role and database (each statement autocommits)
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "CREATE ROLE \"$DATABASE_USER\" LOGIN PASSWORD '$DATABASE_PASSWORD';"

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "CREATE DATABASE \"$DATABASE_NAME\" OWNER \"$DATABASE_USER\";"

# 4) Inside the new DB: schema & default privileges
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$DATABASE_NAME" -c \
  "GRANT USAGE, CREATE ON SCHEMA public TO \"$DATABASE_USER\";"

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$DATABASE_NAME" -c \
  "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES    TO \"$DATABASE_USER\";"

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$DATABASE_NAME" -c \
  "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO \"$DATABASE_USER\";"

echo "(Re)created DB '$DATABASE_NAME' and role '$DATABASE_USER'."
