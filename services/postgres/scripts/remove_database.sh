#!/usr/bin/env bash
set -euo pipefail

: "${DATABASE_NAME:?DATABASE_NAME is required}"
: "${DATABASE_USER:?DATABASE_USER is required}"
: "${POSTGRES_USER:=postgres}"
: "${POSTGRES_DB:=postgres}"
: "${TEST_DATABASE_NAME:=test_${DATABASE_NAME}}"

if [[ "$DATABASE_NAME" == "$POSTGRES_DB" || "$TEST_DATABASE_NAME" == "$POSTGRES_DB" ]]; then
  echo "Refusing to drop primary DB ($POSTGRES_DB)."
  exit 1
fi

psqlc() {
  psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" "$@"
}

drop_database_if_exists() {
  local db_name="$1"
  if [[ -n "$(psqlc -v db_name="$db_name" -tA <<'SQL'
SELECT 1 FROM pg_database WHERE datname = :'db_name';
SQL
)" ]]; then
    psqlc -v db_name="$db_name" <<'SQL'
SELECT pg_terminate_backend(pid) FROM pg_stat_activity
WHERE datname = :'db_name' AND pid <> pg_backend_pid();
DROP DATABASE :"db_name";
SQL
  fi
}

drop_database_if_exists "$TEST_DATABASE_NAME"
drop_database_if_exists "$DATABASE_NAME"

if [[ -n "$(psqlc -v role_name="$DATABASE_USER" -tA <<'SQL'
SELECT 1 FROM pg_roles WHERE rolname = :'role_name';
SQL
)" ]]; then
  psqlc -v role_name="$DATABASE_USER" <<'SQL'
DROP OWNED BY :"role_name" CASCADE;
DROP ROLE :"role_name";
SQL
fi

echo "Removed DBs '$DATABASE_NAME' and '$TEST_DATABASE_NAME' and role '$DATABASE_USER'."
