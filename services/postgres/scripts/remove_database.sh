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

role_exists() {
  [[ -n "$(psqlc -v role_name="$DATABASE_USER" -tA <<'SQL'
SELECT 1 FROM pg_roles WHERE rolname = :'role_name';
SQL
)" ]]
}

drop_database_if_exists() {
  local db_name="$1"
  if [[ "$db_name" == "$POSTGRES_DB" ]]; then
    echo "Refusing to drop primary DB ($POSTGRES_DB)."
    exit 1
  fi
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

# The two databases this script names by convention.
drop_database_if_exists "$TEST_DATABASE_NAME"
drop_database_if_exists "$DATABASE_NAME"

if role_exists; then
  # Whatever else the role owns. Workspace roles have CREATEDB, so their databases are not limited
  # to the conventional two, and DROP ROLE refuses while the role still owns any database.
  while read -r owned_database; do
    [[ -n "$owned_database" ]] || continue
    drop_database_if_exists "$owned_database"
  done < <(psqlc -v role_name="$DATABASE_USER" -tA <<'SQL'
SELECT datname FROM pg_database
WHERE pg_get_userbyid(datdba) = :'role_name' AND datallowconn;
SQL
)

  # Objects and grants the role holds inside databases it does not own. DROP OWNED BY only reaches
  # the database it runs in, so it has to run in each of them for DROP ROLE to have nothing left to
  # complain about.
  while read -r database_name; do
    [[ -n "$database_name" ]] || continue
    psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$database_name" -v role_name="$DATABASE_USER" <<'SQL'
DROP OWNED BY :"role_name" CASCADE;
SQL
  done < <(psqlc -tA <<'SQL'
SELECT datname FROM pg_database WHERE datallowconn ORDER BY datname;
SQL
)

  psqlc -v role_name="$DATABASE_USER" <<'SQL'
DROP ROLE :"role_name";
SQL
fi

echo "Removed DBs '$DATABASE_NAME' and '$TEST_DATABASE_NAME' and role '$DATABASE_USER'."
