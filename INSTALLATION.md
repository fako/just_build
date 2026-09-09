# Installation

The local setup is made to allow agents to work inside a "mainframe" like container called workspaces.
Projects to build exists as users within this workspaces container.
Following this readme will setup the environment, but doesn't setup any agents.


## Prerequisites

This project uses `Python 3.12`, `Docker`, `Docker Compose V2`, `pass` and `psql`.
Make sure they are installed on your system before installing the project.


## Setup

To install the basic environment and tooling you'll need to setup a local environment on a host machine with:

```bash
python3 -m venv venv --copies --upgrade-deps
source venv/bin/activate
pip install -r requirements.txt
```

Now generate your `.env` from `.env.example`:

```bash
invoke install.environment
```

From here on use `activate.sh` instead of activating the venv directly, because it loads `.env` into
your shell the way docker compose does. Run it now to load new variable values:

```bash
source activate.sh
```

To let OpenSSH and VSCode Remote SSH see generated workspace entries, add this once to your user SSH config:

```ssh-config
Include /absolute/path/to/just_build/workspaces/ssh/config
```

Now you can run the following command to generate the SSH keys that the host will use for container access:

```bash
invoke install.ssh
```

To allow easy CLI communication with the Docker containers of this project you now need to update your `/etc/hosts` to include all container names. This requires your sudo password.

```bash
invoke install.hosts-file
```

To finish the container setup you can run these commands to build all containers:

```bash
docker compose up --build -d
```

And run the following to setup the management database that stores workspace details.

```bash
invoke install.management-database
```

## Start automatically at boot

On a Linux host running systemd and system-wide Docker Engine, finish the initial setup
(including the n8n database via `invoke install.n8n`) and then run:

```bash
invoke install.daemon
```

Install UFW first if needed: `sudo apt install ufw`.
The command requests sudo to add the public-port UFW allowances and enable and start
`docker.service` and `containerd.service` under Docker's `restart: always`.
Compose runs as your current user, who must already have access to the system Docker socket.

Other users logged into the same host can access the published ports on `127.0.0.1` without
Docker permissions, subject to each application's authentication. Hostnames installed through
`invoke install.hosts-file` are also system-wide. This does not enable remote access to loopback ports.

### Public ports and UFW

`install.daemon` calls `services/daemon/firewall.sh` to allow incoming TCP on host ports
2222 (workspace SSH), 7000 (workspace HTTP), 5678 (n8n), 8000 (management), and 9998 (Tika) from any source.
Rules are inserted before existing deny rules and repeated runs skip duplicates. Existing rules,
default policies, and UFW's enabled/disabled state are preserved.

Docker's published bridge ports normally bypass UFW's host input rules. These allowances do not
make UFW an access-control boundary for Docker, and this script leaves Docker's forwarding rules
intact. See [Docker's firewall documentation](https://docs.docker.com/engine/network/packet-filtering-firewalls/#docker-and-ufw).

To configure these rules independently, without starting or rebuilding containers:

```bash
bash services/daemon/firewall.sh
```

To also enable UFW, supply the actual **host SSH port** so its allowance is added first.
For a host using the usual port 22:

```bash
bash services/daemon/firewall.sh --enable --ssh-port 22
```
