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

### Updating existing installations

If a variable is missing from your `.env` because it was added to `.env.example` after you generated
it, fill it in with:

```bash
invoke install.environment --missing
```

That leaves every value you already have alone. Do not reach for `--force` on a running installation:
it regenerates *every* secret in the file, including the ones postgres, its roles and the workspaces
were built with, and they will stop authenticating.


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

## n8n

Workspaces manage their automation workflows through management, which reaches n8n over its public
REST API. That API needs a key and a project, and n8n creates neither from configuration, so this
last part is partly manual. Run:

```bash
invoke install.n8n
```

It creates n8n's database if it is not there yet, then prints what is still missing and stops. Work
through what it asks for, running it again after each step:

1. Open http://n8n.localhost/ and complete the owner setup.
2. Under Settings > n8n API, create an API key with the workflow and workflowTags scopes, and put it
   in `.env` as `INVOKE_N8N_API_KEY`.
3. Take the project id out of the editor URL, `/projects/<id>/workflows`, and put it in `.env` as
   `INVOKE_N8N_API_PROJECT_ID`. Every workspace writes into this one project; they are kept apart by
   tags, because n8n only offers project management under a licence.
4. `source activate.sh`, then `docker compose up -d management` so it picks the values up.

A last `invoke install.n8n` confirms management can reach n8n.

n8n encrypts the credentials it stores, with a key it generates on first boot and keeps in its own
volume. Nothing in this repository holds that key, because nothing here handles credentials: they are
entered by hand in the n8n UI. The cost of that is worth knowing up front, because it is not
recoverable — **destroy the `n8n` volume and every credential in it becomes unreadable**, and each
one has to be created again, OAuth flows included. Workflows survive, since management mirrors them
and `invoke workflows.repush` puts them back.

Workspaces get the workflow tooling from a template, so scaffold them with it:

```bash
invoke workspaces.scaffold --workspace-module=<module> --templates=default,n8n
```

A workspace that already holds a project takes the same templates with `--over-existing`, which
leaves them as uncommitted changes to review rather than as a commit:

```bash
invoke workspaces.scaffold --workspace-module=<module> --templates=default,n8n --over-existing
```

From inside the workspace, `invoke n8n.sync` is then the command that matters. On the host,
`invoke workflows.list`, `workflows.pull` and `workflows.repush` are the housekeeping.


## Addressing runtimes

A runtime answers to two kinds of name, and which one to use depends on where the caller is. Keeping
them apart is what lets a workflow keep working when the installation moves to a real machine.

**From a browser on the host**, a workspace's primary runtime is at `http://<workspace>.localhost:7000/`.
No configuration makes this work: every resolver treats anything under `.localhost` as loopback, and
that is where the workspaces container publishes port 7000.

**From another container** — n8n, or another workspace's runtime — the address is:

```
http://runtimes.workspaces:7000/r/<workspace>/<runtime>/
```

The workspace and runtime travel in the path rather than in the hostname, so exactly one name has to
resolve, and it does so through a single compose alias on the workspaces service. nginx rewrites the
path onto the runtime's own internal name and serves it through the same block a browser gets.

The trap worth knowing: **`<workspace>.localhost` does not work from inside a container**, and does
not fail cleanly either. It resolves, to the calling container's own loopback, so it surfaces as a
refused connection rather than an unknown host. Anything not backed by an HTTP runtime — a celery
worker, a workspace that does not exist — returns 404 from the workspaces container's default server.

Real domains for a home network machine, a VPS or a customer are added to a runtime's `domains`
configuration, and management generates the server block:

```bash
invoke runtimes.configure --workspace-module=<module> --name=<runtime> \
    --configuration='{"domains": ["example.com"]}'
```

Nothing here makes those names resolve; that is DNS, and it is yours to point at the host. Whichever
names a runtime gains, `ALLOWED_HOSTS` in the workspace has to accept them.
