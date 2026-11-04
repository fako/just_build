#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd -- "$repository_root"

unit_name="just-build-backups"
unit_directory="/etc/systemd/system"
template_directory="$repository_root/services/backups/systemd"
job_key="$HOME/.ssh/just_build_backups"

if [[ "${1:-}" == "--uninstall" ]]; then
    echo "[backups] Requesting sudo to remove the backup timer. Backups themselves are left alone."
    sudo systemctl disable --now "$unit_name.timer" 2>/dev/null || true
    sudo rm -f "$unit_directory/$unit_name.service" "$unit_directory/$unit_name.timer"
    sudo systemctl daemon-reload
    echo "[backups] Timer removed."
    exit 0
fi

: "${BACKUPS_SYSTEM:?BACKUPS_SYSTEM is required, run this through invoke install.backups}"
: "${BACKUPS_ROOT:?BACKUPS_ROOT is required, run this through invoke install.backups}"

for dependency in docker rsync rrsync ssh-keygen systemctl sudo; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
        echo "[backups] Required command not found: $dependency" >&2
        if [[ "$dependency" == "rrsync" ]]; then
            echo "[backups] rrsync ships with rsync 3.2.4 and later as /usr/bin/rrsync." >&2
        fi
        exit 1
    fi
done

if [[ ! -f "$repository_root/.env" ]]; then
    echo "[backups] Missing .env. Complete INSTALLATION.md setup before installing backups." >&2
    exit 1
fi
if [[ ! -x "$repository_root/venv/bin/invoke" ]]; then
    echo "[backups] Missing $repository_root/venv/bin/invoke. Create the venv from INSTALLATION.md first." >&2
    exit 1
fi
if ! docker info >/dev/null 2>&1; then
    echo "[backups] The current user must have access to the Docker socket; the timer runs as this user." >&2
    exit 1
fi

mkdir -p "$BACKUPS_ROOT"
chmod 700 "$BACKUPS_ROOT"

# The key the timer pulls with. It has no passphrase so nothing has to be typed at 03:00, which is
# only acceptable because other machines authorize it read-only, for one directory.
if [[ ! -f "$job_key" ]]; then
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -q -t ed25519 -N "" -C "just-build-backups:$BACKUPS_SYSTEM@$(hostname)" -f "$job_key"
    echo "[backups] Generated the read-only job key $job_key"
fi

rendered="$(mktemp -d)"
trap 'rm -rf -- "$rendered"' EXIT
for unit in service timer; do
    sed -e "s|@REPOSITORY@|$repository_root|g" \
        -e "s|@USER@|$(id -un)|g" \
        "$template_directory/$unit_name.$unit" > "$rendered/$unit_name.$unit"
done

echo "[backups] Requesting sudo to install the $unit_name timer."
sudo -v
sudo install -m 644 "$rendered/$unit_name.service" "$unit_directory/$unit_name.service"
sudo install -m 644 "$rendered/$unit_name.timer" "$unit_directory/$unit_name.timer"
sudo systemctl daemon-reload
sudo systemctl enable "$unit_name.timer"
# Restart rather than start, so rerunning this picks up a changed timer on an installation that has one.
sudo systemctl restart "$unit_name.timer"

echo "[backups] Making a first backup to prove the setup."
"$repository_root/venv/bin/invoke" backups.create

systemctl list-timers "$unit_name.timer" --no-pager
echo ""
echo "[backups] Installed for system '$BACKUPS_SYSTEM', keeping backups in $BACKUPS_ROOT"
echo "[backups] To let this machine pull another one's backups, run this on the other machine:"
echo ""
echo "    invoke backups.authorize --name=$BACKUPS_SYSTEM --key='$(cat "$job_key.pub")'"
echo ""
echo "[backups] and then here: invoke backups.register --name=<other system> --host=<ssh alias>"
