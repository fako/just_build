#!/bin/sh
set -eu

load_user_passwords.sh
# After the accounts are in place, so log directories can be owned by their workspace group.
create_log_directories.sh
load_supervisor_secret.sh
unset WORKSPACES_SUPERVISOR_PASSWORD

exec "$@"
