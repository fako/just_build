#!/bin/sh
set -eu

# Supervisord refuses to start at all when any program's log directory is missing, and will not
# create one itself. The log root lives in the image rather than a volume, so every rebuild loses
# the per-workspace directories while the bind-mounted configs that reference them survive.
#
# The configuration tree is the authority on which workspaces exist: conf.d/<module>/<runtime>.conf.
LOG_ROOT=${WORKSPACES_LOG_ROOT:-/var/log/workspaces}
CONFIG_ROOT=${WORKSPACES_CONFIG_ROOT:-/etc/supervisor/conf.d}

mkdir -p "$LOG_ROOT"

for config_dir in "$CONFIG_ROOT"/*/; do
    [ -d "$config_dir" ] || continue

    workspace=$(basename "$config_dir")
    log_dir="$LOG_ROOT/$workspace"
    mkdir -p "$log_dir"

    # Readable by the workspace group and nobody else, so a workspace tails its own logs only.
    # A workspace whose account has not been created yet falls back to root, and the CLI fixes the
    # ownership when it creates the account.
    if getent group "$workspace" >/dev/null 2>&1; then
        chown "root:$workspace" "$log_dir"
        chmod 750 "$log_dir"
    else
        chown root:root "$log_dir"
        chmod 755 "$log_dir"
    fi
done
