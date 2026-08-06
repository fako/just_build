#!/bin/sh
set -eu

load_user_passwords.sh
# After the accounts are in place, so log directories can be owned by their workspace group.
create_log_directories.sh
load_opencode_secret.sh
load_supervisor_secret.sh
unset OPENCODE_SERVER_PASSWORD OPENCODE_SERVER_USERNAME OPENCODE_BASIC_AUTH_HEADER
unset WORKSPACES_SUPERVISOR_PASSWORD

exec "$@"
