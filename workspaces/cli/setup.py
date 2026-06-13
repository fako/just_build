from invoke.tasks import task

from workspaces.cli.constants import SSH_KEYS_DIR


def ensure_ssh_host_keys(ctx) -> bool:
    SSH_KEYS_DIR.mkdir(parents=True, exist_ok=True)

    ed25519_key = SSH_KEYS_DIR / "ssh_host_ed25519_key"
    rsa_key = SSH_KEYS_DIR / "ssh_host_rsa_key"

    if ed25519_key.exists() and rsa_key.exists():
        return False

    print(f"Generating SSH host keys in {SSH_KEYS_DIR}")

    if not ed25519_key.exists():
        ctx.run(f'ssh-keygen -t ed25519 -f {ed25519_key} -N ""', hide=False)
        print(f"  Created {ed25519_key.name}")

    if not rsa_key.exists():
        ctx.run(f'ssh-keygen -t rsa -b 4096 -f {rsa_key} -N ""', hide=False)
        print(f"  Created {rsa_key.name}")

    return True


@task
def setup(ctx):
    """Generate SSH host keys for the workspaces container."""
    generated = ensure_ssh_host_keys(ctx)
    if not generated:
        print("SSH host keys already exist. Delete them first to regenerate.")
        return

    print("\nSSH host keys generated. Rebuild the container to use them:")
    print("  docker compose --profile workspaces up --build")
