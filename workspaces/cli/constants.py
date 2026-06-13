from pathlib import Path


CLI_DIR = Path(__file__).resolve().parent
WORKSPACES_DIR = CLI_DIR.parent
TEMPLATES_DIR = WORKSPACES_DIR / "templates"
SRC_DIR = WORKSPACES_DIR / "src"
REPOS_DIR = SRC_DIR / "repos"
ACTIVE_SUPERVISOR_DIR = SRC_DIR / "supervisor"
ACTIVE_NGINX_DIR = SRC_DIR / "nginx"
STAGED_DIR = SRC_DIR / "staged"
STAGED_SUPERVISOR_DIR = STAGED_DIR / "supervisor"
STAGED_NGINX_DIR = STAGED_DIR / "nginx"
SSH_DIR = WORKSPACES_DIR / "ssh"
SSH_KEYS_DIR = SSH_DIR / "keys"
WORKSPACE_SSH_KEYS_DIR = SSH_KEYS_DIR / "src"
SSH_CONFIG_PATH = SSH_DIR / "config"
SUPERVISOR_TEMPLATE_PATH = WORKSPACES_DIR / "supervisor" / "example.conf.template"
NGINX_TEMPLATE_PATH = WORKSPACES_DIR / "nginx" / "example.conf.template"
