# Installation

The local setup is made in such a way that you can run the project inside and outside of containers.
It can be convenient to run some code for inspection outside of containers.
To stay close to the production environment it works well to run the project in containers.
External services like the database run in containers, so it's always necessary to use Docker.
The project also heavily leans on its tests through pytest.


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
127.0.0.1 control
```

This way you can reach these containers outside of the container network through their names.
This is important for many setup commands.

To finish the container setup you can run these commands to build all containers:

```bash
docker compose up --build
```
