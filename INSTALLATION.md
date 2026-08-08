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

This copies every variable, comment and default from `.env.example` and fills in a freshly generated
secret for the values that need one: both database passwords, the supervisor password, the Django
secret key and the control API key the CLI authenticates with. Non-secret defaults like
`COMPOSE_PROFILES` are copied unchanged, so open `.env` afterwards to fit those to your system.

The task refuses to touch an existing `.env`. Pass `--force` to regenerate one, which replaces every
secret in it. On an install that already runs, that invalidates the current control API key and
leaves the passwords out of step with what the containers hold: PostgreSQL only reads
`INVOKE_POSTGRES_PASSWORD` when it initialises its data volume, so a regenerated password fails to
authenticate until that volume is recreated.

From here on use `activate.sh` instead of activating the venv directly, because it loads `.env` into
your shell the way docker compose does:

```bash
source activate.sh
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

`watch` syncs code changes into the container, where uvicorn picks them up with `--reload`, and
rebuilds the image when `management/requirements.txt` changes. Code edits are only live while
`docker compose watch` is running; a plain `up` serves the code baked into the image.

**On the host**, for debugging with a real debugger attached, by leaving management out of the
profile:

```bash
COMPOSE_PROFILES=services docker compose up -d
source activate.sh
cd management && python manage.py runserver
```

Both modes talk to the same database and reach the other containers by their service names, which
`invoke install.hosts-file` has already made resolvable from the host.
