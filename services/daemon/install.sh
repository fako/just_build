#!/usr/bin/env bash
set -euo pipefail

repository_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd -- "$repository_root"

for dependency in docker systemctl sudo; do
    if ! command -v "$dependency" >/dev/null 2>&1; then
        echo "[daemon] Required command not found: $dependency" >&2
        exit 1
    fi
done

if ! command -v ufw >/dev/null 2>&1; then
    echo "[daemon] Install UFW first: sudo apt install ufw" >&2
    exit 1
fi

if [[ ! -f "$repository_root/.env" ]]; then
    echo "[daemon] Missing .env. Complete INSTALLATION.md setup before installing autostart." >&2
    exit 1
fi

# Use the system Docker Engine, regardless of the caller's Docker context. Load
# Compose settings from .env, without stale variables exported by activate.sh.
# Keep HOME so Docker can find the caller's CLI plugins and registry credentials.
docker_command=(env -i "HOME=$HOME" "PATH=$PATH" docker --host unix:///var/run/docker.sock)
compose_command=("${docker_command[@]}" compose
    --project-directory "$repository_root"
    --env-file "$repository_root/.env"
    --file "$repository_root/docker-compose.yml"
    --file "$repository_root/services/daemon/docker-compose.daemon.yml")

"${compose_command[@]}" version
"${compose_command[@]}" config --quiet

echo "[daemon] Requesting sudo to configure UFW allowances and enable Docker at boot."
sudo -v
sudo bash "$repository_root/services/daemon/firewall.sh"
sudo systemctl enable --now docker.service containerd.service

if ! "${docker_command[@]}" info >/dev/null; then
    echo "[daemon] The current user must have access to the system Docker socket." >&2
    echo "[daemon] Configure Docker access, then rerun invoke install.daemon." >&2
    exit 1
fi

# Builds happen during installation, never during boot. Existing project volumes
# are reused; do not override the project's normal name or select extra profiles.
"${compose_command[@]}" up --build --detach --wait --wait-timeout 180
echo "[daemon] Autostart installed. Docker will restart these containers without a user login."
"${compose_command[@]}" ps
