from pathlib import Path


CLI_DIR = Path(__file__).resolve().parent
WORKSPACES_DIR = CLI_DIR.parent
REPOSITORY_DIR = WORKSPACES_DIR.parent
TEMPLATES_DIR = WORKSPACES_DIR / "templates"
SRC_DIR = WORKSPACES_DIR / "src"
REPOS_DIR = SRC_DIR / "repos"
SECRETS_DIR = SRC_DIR / "secrets"
# Written only by the manifest reconciler in workspaces.cli.configs, from what management renders.
SUPERVISOR_DIR = SRC_DIR / "supervisor"
NGINX_DIR = SRC_DIR / "nginx"
SSH_DIR = WORKSPACES_DIR / "ssh"
SSH_KEYS_DIR = SSH_DIR / "keys"
WORKSPACE_SSH_KEYS_DIR = SRC_DIR / "ssh"
SSH_CONFIG_PATH = SSH_DIR / "config"
