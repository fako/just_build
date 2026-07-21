#!/usr/bin/env bash
set -euo pipefail

# Required inputs
: "${DATABASE_NAME:?DATABASE_NAME is required}"
: "${DATABASE_USER:?DATABASE_USER is required}"
: "${DATABASE_PASSWORD:?DATABASE_PASSWORD is required}"

# Defaults (from the official image)
: "${POSTGRES_USER:=postgres}"
: "${POSTGRES_DB:=postgres}"
: "${TEST_DATABASE_NAME:=test_${DATABASE_NAME}}"

# Helper to run a single SQL against the control DB (usually 'postgres')
psqlc() {
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "$1"
}

ensure_safe_database_name() {
  local db_name="$1"
  if [[ "$db_name" == "$POSTGRES_DB" && "${FORCE:-0}" != "1" ]]; then
    echo "Refusing to drop primary DB ($POSTGRES_DB). Set FORCE=1 to override."
    exit 1
  fi
}

drop_database_if_exists() {
  local db_name="$1"
  if [[ -n "$(psqlc "SELECT 1 FROM pg_database WHERE datname = '$db_name'")" ]]; then
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
      "SELECT pg_terminate_backend(pid)
       FROM pg_stat_activity
       WHERE datname = '$db_name' AND pid <> pg_backend_pid();"

    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
      "DROP DATABASE \"$db_name\";"
  fi
}

configure_database_access() {
  local db_name="$1"
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db_name" -c \
    "GRANT USAGE, CREATE ON SCHEMA public TO \"$DATABASE_USER\";"

  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db_name" -c \
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO \"$DATABASE_USER\";"

  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db_name" -c \
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO \"$DATABASE_USER\";"
}

install_vector_extension() {
  local db_name="$1"
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$db_name" -c \
    "CREATE EXTENSION IF NOT EXISTS vector;"
}

ensure_safe_database_name "$DATABASE_NAME"
ensure_safe_database_name "$TEST_DATABASE_NAME"

# 1) If DBs exist: terminate connections and drop them (no transaction)
drop_database_if_exists "$TEST_DATABASE_NAME"
drop_database_if_exists "$DATABASE_NAME"

# 2) If role exists: drop its objects in the control DB and try to drop the role.
#    The role may own objects outside this workspace (e.g. a database created by
#    another process), in which case DROP ROLE fails. That's non-fatal: we keep
#    the existing role and just reset its password/attributes below.
if [[ -n "$(psqlc "SELECT 1 FROM pg_roles WHERE rolname = '$DATABASE_USER'")" ]]; then
  if ! psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "DROP OWNED BY \"$DATABASE_USER\" CASCADE;"; then
    echo "Warning: could not drop objects owned by role '$DATABASE_USER' in '$POSTGRES_DB'. Continuing." >&2
  fi

  if ! psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "DROP ROLE \"$DATABASE_USER\";"; then
    echo "Warning: could not drop role '$DATABASE_USER' (it likely still owns a database outside this workspace). Reusing the existing role instead." >&2
  fi
fi

# 3) Recreate the role and databases (each statement autocommits). The role may
# still exist from step 2 above, so fall back to ALTER ROLE to reset its
# password/attributes rather than failing on a duplicate CREATE ROLE.
if [[ -n "$(psqlc "SELECT 1 FROM pg_roles WHERE rolname = '$DATABASE_USER'")" ]]; then
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "ALTER ROLE \"$DATABASE_USER\" LOGIN CREATEDB PASSWORD '$DATABASE_PASSWORD';"
else
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
    "CREATE ROLE \"$DATABASE_USER\" LOGIN CREATEDB PASSWORD '$DATABASE_PASSWORD';"
fi

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "CREATE DATABASE \"$DATABASE_NAME\" OWNER \"$DATABASE_USER\";"

psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "CREATE DATABASE \"$TEST_DATABASE_NAME\" OWNER \"$DATABASE_USER\";"

# 4) Inside the new DBs: extension, schema & default privileges
install_vector_extension "$DATABASE_NAME"
install_vector_extension "$TEST_DATABASE_NAME"

configure_database_access "$DATABASE_NAME"
configure_database_access "$TEST_DATABASE_NAME"

echo "(Re)created DBs '$DATABASE_NAME' and '$TEST_DATABASE_NAME' with role '$DATABASE_USER'."
