#!/bin/sh
set -eu

STATE_DIR=${WORKSPACES_STATE_DIR:-/workspaces/state}
STATE_ETC="$STATE_DIR/etc"

mkdir -p "$STATE_ETC"

seed_account_file() {
    name="$1"
    if [ ! -f "$STATE_ETC/$name" ]; then
        cp -a "/etc/$name" "$STATE_ETC/$name"
    fi
}

seed_account_file passwd
seed_account_file group
seed_account_file shadow
seed_account_file gshadow

chmod 644 "$STATE_ETC/passwd" "$STATE_ETC/group"
chmod 640 "$STATE_ETC/shadow" "$STATE_ETC/gshadow"

cp -a "$STATE_ETC/passwd" "$STATE_ETC/group" "$STATE_ETC/shadow" /etc/
cp -a "$STATE_ETC/gshadow" /etc/
