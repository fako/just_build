#!/bin/sh
set -eu

: "${OPENCODE_SERVER_PASSWORD:?OPENCODE_SERVER_PASSWORD must be set}"

OPENCODE_SERVER_USERNAME=${OPENCODE_SERVER_USERNAME:-opencode}
OPENCODE_BASIC_AUTH_HEADER=$(
    printf '%s' "${OPENCODE_SERVER_USERNAME}:${OPENCODE_SERVER_PASSWORD}" | base64 -w0
)
export OPENCODE_BASIC_AUTH_HEADER

envsubst '${OPENCODE_BASIC_AUTH_HEADER}' \
    < /etc/nginx/templates/opencode-auth.conf.template \
    > /etc/nginx/opencode-auth.conf
chmod 600 /etc/nginx/opencode-auth.conf
