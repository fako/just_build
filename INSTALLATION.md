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
