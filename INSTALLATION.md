# Installation

The local setup is made to allow agents to work inside a "mainframe" like container called workspaces.
Projects to build exists as users within this workspaces container.
Following this readme will setup the environment, but doesn't setup any agents.


## Prerequisites

This project uses `Python 3.12`, `Docker`, `Docker Compose V2`, `pass` and `psql`.
Make sure they are installed on your system before installing the project.


## Setup

First copy the `.env.example` file to `.env` and update the variable values to fit your system.
For a start the default values will do.

To install the basic environment and tooling you'll need to setup a local environment on a host machine with:

```bash
python3 -m venv venv --copies --upgrade-deps
source activate.sh
pip install -r requirements.txt
```

To let OpenSSH and Cursor Remote SSH see generated workspace entries, add this once to your user SSH config:

```ssh-config
Include /absolute/path/to/just_build/workspaces/ssh/config
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

This runs on the host against the published PostgreSQL port, so it works the same whether management
runs in its container or as a development server on your host.


## Running management

Management is a normal Django project and runs either way. Nothing in the CLI shells into its
container, so pick whichever suits what you are doing.

**In its container**, which is what the `control` and `workspaces` compose profiles do:

```bash
docker compose watch management
```

`watch` rebuilds the image when `management/requirements.txt` changes. Code changes are picked up
straight away, because the repository is mounted over the image and uvicorn runs with `--reload`.

**On the host**, for debugging with a real debugger attached, by leaving management out of the
profile:

```bash
COMPOSE_PROFILES=services docker compose up -d
source activate.sh
cd management && python manage.py runserver
```

Both modes talk to the same database and reach the other containers by their service names, which
`invoke install.hosts-file` has already made resolvable from the host.
