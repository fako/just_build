#!/bin/sh
set -eu

: "${WORKSPACES_SUPERVISOR_PASSWORD:?WORKSPACES_SUPERVISOR_PASSWORD must be set}"

# Substituted in rather than read through %(ENV_...)s, so the entrypoint can unset the variable
# before starting supervisord. Supervisord passes its own environment to every program it starts,
# and a workspace that inherited this password could drive the processes of every other workspace.
envsubst '${WORKSPACES_SUPERVISOR_PASSWORD}' \
    < /etc/supervisor/templates/supervisord.conf.template \
    > /etc/supervisor/supervisord.conf
chmod 600 /etc/supervisor/supervisord.conf
