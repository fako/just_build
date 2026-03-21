# Installation

The local setup is made to allow agents to work inside a "mainframe" like container called workspaces.
Projects to build exists as users within this workspaces container.
Following this readme will setup the environment, but doesn't setup any agents.


## Prerequisites

This project uses `Python 3.12`, `Docker`, `Docker Compose V2` and `psql`.
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

If you want to run the project outside of a container you'll need to add the following to your hosts file.
It's strongly recommended to update your ``/etc/hosts`` immediately,
to prevent weird error messages if you ever run the project outside of its containers.

```
127.0.0.1 postgres
127.0.0.1 redis
127.0.0.1 tika
127.0.0.1 management
```

This way you can reach these containers outside of the container network through their names.
This is important for many setup commands.

To finish the container setup you can run these commands to build all containers:

```bash
docker compose up --build
```
