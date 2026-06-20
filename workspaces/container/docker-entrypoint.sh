#!/bin/sh
set -eu

load_user_passwords.sh
load_opencode_secret.sh
unset OPENCODE_SERVER_PASSWORD OPENCODE_SERVER_USERNAME OPENCODE_BASIC_AUTH_HEADER

exec "$@"
