from pathlib import Path

from invoke.tasks import task

from workspaces.cli.constants import SSH_CONFIG_PATH, SSH_KEYS_DIR


USER_SSH_CONFIG = Path.home() / ".ssh" / "config"


def check_ssh_config_include() -> bool:
    """
    Report whether the user's SSH config includes the generated workspace aliases.

    The fabfile addresses workspaces by alias and fails to resolve them without this, which looks
    like a connection problem rather than a missing line.
    """
    include_line = f"Include {SSH_CONFIG_PATH}"
    if USER_SSH_CONFIG.exists() and str(SSH_CONFIG_PATH) in USER_SSH_CONFIG.read_text():
        return True

    print("")
    print(f"Generated workspace SSH aliases are not included from {USER_SSH_CONFIG}.")
    print("Add this line at the top of that file to use 'fab -H <alias>' and Cursor Remote SSH:")
    print("")
    print(f"    {include_line}")
    return False


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
    """Generate SSH host keys for the workspaces container and check the SSH config include."""
    generated = ensure_ssh_host_keys(ctx)
    if not generated:
        print("SSH host keys already exist. Delete them first to regenerate.")
    else:
        print("\nSSH host keys generated. Rebuild the container to use them:")
        print("  docker compose --profile workspaces up --build")

    check_ssh_config_include()
