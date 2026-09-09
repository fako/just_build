#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: bash services/daemon/firewall.sh [--enable --ssh-port PORT]"
    echo "Adds public TCP allowances for 2222, 7000, 5678, 8000 and 9998."
    echo "Preserves UFW's active state unless --enable is supplied."
}

enable=false
ssh_port=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --enable) enable=true; shift ;;
        --ssh-port)
            if [[ $# -lt 2 ]]; then
                usage >&2
                exit 1
            fi
            ssh_port="$2"
            shift 2
            ;;
        --help|-h) usage; exit 0 ;;
        *) usage >&2; exit 1 ;;
    esac
done

if [[ -n "$ssh_port" ]]; then
    if [[ ! "$ssh_port" =~ ^[0-9]{1,5}$ ]] || (( 10#$ssh_port < 1 || 10#$ssh_port > 65535 )); then
        echo "[firewall] SSH port must be an integer from 1 to 65535." >&2
        exit 1
    fi
    ssh_port="$((10#$ssh_port))"
elif $enable; then
    echo "[firewall] --enable requires --ssh-port with the host's SSH port (usually 22)." >&2
    echo "[firewall] Port 2222 belongs to the workspaces container, not the host SSH server." >&2
    exit 1
fi

if ! command -v ufw >/dev/null 2>&1; then
    echo "[firewall] Install UFW first: sudo apt install ufw" >&2
    exit 1
fi

# Request privileges only after validating options. Re-running with the same
# arguments is safe: UFW skips duplicate rules. Never reset existing rules.
if [[ $EUID -ne 0 ]]; then
    arguments=()
    if $enable; then arguments+=(--enable); fi
    if [[ -n "$ssh_port" ]]; then arguments+=(--ssh-port "$ssh_port"); fi
    exec sudo bash "${BASH_SOURCE[0]}" "${arguments[@]}"
fi

# Prepend ahead of existing deny rules so these explicit public allowances win.
# Unlike "insert 1", "prepend" also works when there are no existing rules.
# Add the host SSH allowance before optionally enabling the firewall.
if [[ -n "$ssh_port" ]]; then
    ufw prepend allow proto tcp to any port "$ssh_port" comment "just_build host SSH"
fi
for port in 2222 7000 5678 8000 9998; do
    ufw prepend allow proto tcp to any port "$port" comment "just_build public service"
done

if $enable; then
    ufw --force enable
fi

ufw status verbose
echo "[firewall] Rules saved. Without --enable, UFW's active state is unchanged."
echo "[firewall] Docker handles published-port forwarding separately and can bypass UFW."
