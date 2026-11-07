---
name: database backups
overview: Give every installation a daily, self-pruning backup of its PostgreSQL databases (keep everything from the last 14 days, plus the first backup of every month), and let installations hold each other's backups over SSH. Each machine is a named "system" that writes its own backups into `<root>/<system>/`; machines that keep copies of others pull them (or receive pushes) into `<root>/<other>/`, and one retention rule prunes every directory locally. A `backups.*` invoke collection covers create, restore, register, pull, push, prune, a manual clean of very old backups and a file-based status; `install.backups` adds one systemd timer that runs create, pull and prune. Unattended transfers only ever use a passphrase-less key that the source restricts to reading one directory; writing to another machine is a manual push with a passphrase-protected key the user supplies. INSTALLATION.md gets the short version, BACKUPS.md the full one.
todos:
  - id: config-and-layout
    content: Add the backups section to invoke.yml and .env.example (system name, root, schedule, retention, staleness), and a storage module that owns the on-disk layout, the manifest, atomic `.partial` renames and the root lock.
    status: completed
  - id: retention
    content: Write the retention rule as a pure function (last 14 days, earliest backup of each calendar month, plus the newest own backup) and `backups.prune` with `--dry-run` on top of it. Held systems keep 12 months of monthlies, the own system keeps them until cleaned.
    status: completed
  - id: clean
    content: Add `backups.clean`, a manual task that deletes backups older than a year by default or before a fixed `--before` date, with `--system`, `--dry-run` and a confirmation prompt, never touching the newest own backup.
    status: completed
  - id: create
    content: Add `backups.create`, which dumps globals and every non-template, non-test database through `docker compose exec -T postgres`, writes a checksummed manifest and renames the directory into place.
    status: completed
  - id: restore
    content: Add `backups.restore` for the own system only, with checksum verification, a confirmation prompt, a pre-restore safety backup, stopping and restarting consumers, and `--database` for a single database.
    status: completed
  - id: registry
    content: Add `backups.register` and `backups.unregister`, which maintain `<root>/remotes.yml` with an SSH alias and a pull or push direction per system, checking reachability with an rsync listing because the remote key allows nothing else.
    status: completed
  - id: authorize
    content: Add `backups.authorize`, which writes a `restrict`ed, rrsync-forced line into `~/.ssh/authorized_keys` - read-only to the own system's directory by default, write-only into the sender's directory with `--push` - idempotent per name, with `--revoke`.
    status: completed
  - id: pull
    content: Add `backups.pull`, rsync over SSH with the read-only job key from whatever directory the source's rrsync exposes, `--ignore-existing` and without --delete, skipping partials, verifying manifests and their system on arrival, with `--system` for disaster recovery.
    status: completed
  - id: push
    content: Add `backups.push`, a manual-only rsync of the own directory to push targets through their own SSH alias and a user-supplied, passphrase-protected key, refusing the read-only job key, recording the last pushed backup per target under `<root>/.state/`.
    status: completed
  - id: status
    content: Add `backups.status`, which reads only local files and prints the latest backup per system, own first, then registered remotes, then unregistered held systems, flagging stale and corrupt ones. `--system` lists every backup of one system.
    status: completed
  - id: run
    content: Add `backups.run`, the composite the timer calls - create, pull, prune, never push - which carries on past a failing remote and exits non-zero if anything failed.
    status: completed
  - id: install
    content: Add `install.backups` with a services/backups/install.sh that checks its prerequisites (rrsync included), generates the passphrase-less read-only job key, writes and enables a systemd service and timer for `backups.run`, and supports `--schedule` and `--uninstall`.
    status: completed
  - id: tests
    content: Cover retention, manifest verification, status grouping, the registry and authorized_keys lines with plain pytest, and create/restore/pull/push command construction with a recording context.
    status: completed
  - id: docs-installation
    content: Add a short Backups section to INSTALLATION.md - set the system name, run `invoke install.backups`, link to BACKUPS.md.
    status: completed
  - id: docs-backups
    content: Write BACKUPS.md - concepts and topology, the retention rule, layout, per-machine setup for A/B/C, the read-only job key and passphrase-protected push keys with rrsync restrictions, command reference, scheduling, restore and disaster-recovery recipes, what is not backed up, troubleshooting.
    status: completed
isProject: true
---

# Database Backups Plan

## Problem And Shape Of The Solution

Everything that matters in an installation lives in the one `postgres` container: management, n8n,
and a database per workspace. Nothing copies it anywhere. Losing the `postgres` volume, or the
machine it is on, loses all of it.

There are going to be several installations — call them A, B and C — and they should look after
each other: every machine backs itself up, A also keeps copies of B and C, B keeps copies of C, and C
is an edge that only backs itself up.

```
   ┌───────┐   pulls b, c
   │   A   │◄────────────────────────┐
   └───▲───┘                         │
       │ pull                        │ pull
   ┌───┴───┐   pulls c               │
   │   B   │◄──────────────┐         │
   └───────┘   (or c       │         │
                pushes)    │         │
                       ┌───┴───┐─────┘
                       │   C   │  edge: own backups only
                       └───────┘
```

The design rests on one idea: **every machine is a named system, and a backup directory belongs to
the system that made it.** A machine writes only into `<root>/<own system>/`. Copies of other systems
land in `<root>/<their name>/`, either because this machine pulled them or because the other machine
pushed them. Retention is then a purely local, per-directory rule that every machine applies to every
directory it holds. No machine ever deletes anything on another machine, and nothing a remote does —
pruning, being wiped, being compromised — can remove copies held elsewhere.

That gives three roles a machine can play towards another, all expressed with the same files:

| | writes | transfers | prunes |
| --- | --- | --- | --- |
| own system | `<root>/<own>/` via `backups.create` | serves it read-only to pullers; manual push | yes |
| pulled system | `<root>/<remote>/` via `backups.pull` | — | yes |
| pushed-in system | `<root>/<sender>/`, written by the sender | — | yes |

Pull is the default because the machine that keeps the copies is the one that cares whether they
arrive, and because it only needs read access, which is what lets it run unattended with a
passphrase-less key (see SSH Access). Push exists for the case where the holder cannot reach the
source, such as C behind NAT; it writes to another machine, so it is manual and takes a
passphrase-protected key.

## Configuration

`invoke.yml` gets a `backups` section; the per-machine values come from `.env` as usual:

```yaml
backups:
  # Name of this machine among the systems that exchange backups. Lowercase letters, digits and
  # dashes; unique across every machine that holds or sends backups. Required.
  system: null
  # Where this machine keeps its own backups and the ones it holds for others. Empty means
  # ~/backups/just_build. Kept outside the repository so a git clean can never reach it.
  root: null
  # systemd OnCalendar expression for backups.run. Stagger it per machine, see Scheduling.
  schedule: "*-*-* 03:00:00"
  retention:
    days: 14
    # Months of monthly keepers kept for held systems. The own system keeps them until backups.clean.
    remote_months: 12
    # Default age cutoff for backups.clean, overridden per run with --before.
    clean_after_days: 365
  # status flags a system whose newest backup is older than this.
  stale_after_hours: 36
```

`.env.example` gains `INVOKE_BACKUPS_SYSTEM=` and `INVOKE_BACKUPS_ROOT=` under a `# Backups
environment` heading, so `invoke install.environment --missing` picks them up on existing
installations. Neither is a secret, so `install.environment` needs no generator for them.

Remotes are per machine and structured, which fits neither `.env` nor the committed `invoke.yml`.
They live in `<root>/remotes.yml`, next to the data they describe, maintained by `backups.register`
rather than edited by hand:

```yaml
b:
  host: just-build-b          # an alias from ~/.ssh/config, which carries user, port and key
  direction: pull
c:
  host: just-build-c
  direction: pull
```

There is no remote path. The source decides which directory a key may see (see SSH Access), so the
puller always asks for "the directory this key is allowed", and checks the manifests it gets back.
On C, a push target would look like `b: {host: just-build-b-push, direction: push}`, with an alias of
its own.

## Storage Layout

```
~/backups/just_build/                  INVOKE_BACKUPS_ROOT, mode 700
  remotes.yml
  .lock                                flock'ed by every task that writes or deletes
  .state/pushed-b.json                 last backup pushed to b, and when
  a/                                   own system
    20261101T030000Z/
      manifest.json
      globals.sql                      pg_dumpall --globals-only (roles)
      management.dump                  pg_dump -Fc, one file per database
      n8n.dump
      automate.dump
    20261031T030000Z/
    20261102T030000Z.partial/          being written; ignored by everything else
  b/
  c/
```

Backup ids are UTC timestamps, so lexical order is chronological order and directory names alone are
enough to answer "what is the latest". A backup is written into `<id>.partial/` and renamed into
place once the manifest is written, so a complete directory always has a manifest and a partial one
is never pulled, pushed, restored or counted. Partials older than a day are removed by prune.

`manifest.json` records what restore and verification need:

```json
{
  "format": 1,
  "system": "a",
  "id": "20261101T030000Z",
  "created_at": "2026-11-01T03:00:00Z",
  "label": null,
  "postgres_version": "16.4",
  "commit": "c07e11a",
  "files": {
    "globals.sql": {"bytes": 2048, "sha256": "..."},
    "management.dump": {"database": "management", "owner": "management", "bytes": 1048576, "sha256": "..."}
  }
}
```

The `system` field is checked on every pull and push, so two machines accidentally configured with
the same name fail loudly instead of interleaving their backups.

## Retention

A backup is kept when any of these hold:

1. It is younger than `retention.days` (14): **every** backup from the last 14 days, so manual and
   pre-restore backups survive alongside the daily ones.
2. It is the earliest backup in its calendar month (UTC). With a daily timer that is the backup of
   the 1st; if the machine was off on the 1st it is the first one made after, so a month is never
   left without a keeper. For the own system monthly keepers stay until `backups.clean` removes
   them; for held systems only the last `retention.remote_months` (12) months are kept, so A holds a
   calendar year of B's and C's monthlies.
3. It is the newest backup of the **own** system. If `backups.create` keeps failing for two weeks
   while the timer keeps pruning, the machine still has its last good backup.

Everything else is deleted. The rule is a pure function from a list of ids, "now" and whether the
directory is the own system to a set to keep, which is where the tests concentrate. A holds the same
last 14 days of B's and C's backups as B and C hold of themselves, plus a year of their monthlies.
Rule 3 deliberately does not extend to held systems; a remote that goes silent ages out like its own
copies would, and `status` is what tells you it went silent. `backups.prune --dry-run` prints the
decision per backup without deleting anything.

Prune never removes the own system's old monthlies, because how far back a machine wants to reach
into its own history is a judgement, not a schedule. That is what `backups.clean` is for.

## Commands

All tasks live in a `backups` collection at `services/backups/tasks.py`, registered in
`tasks/__init__.py` like whisperx, with logic split into `storage.py` (layout, manifest, lock),
`retention.py`, `postgres.py` and `transport.py` beside it.

### `backups.create [--label=<text>]`

1. Take the root lock, create `<root>/<own>/<id>.partial/`.
2. List databases with `psql` inside the container:
   `SELECT datname, pg_get_userbyid(datdba) FROM pg_database WHERE NOT datistemplate AND datname
   <> 'postgres' AND datname NOT LIKE 'test\_%'`. Test databases are recreated by test runs and are
   not worth keeping.
3. `pg_dumpall --globals-only` into `globals.sql`, then `pg_dump -Fc <db>` per database, each through
   `docker compose exec -T postgres` and streamed to the host file. Running the tools inside the
   container guarantees they match the server's major version; a host `pg_dump` older than 16
   refuses to dump.
4. Hash every file, write `manifest.json`, rename the directory into place, run prune for the own
   system.

The compose invocation is built the way `services/daemon/install.sh` builds it (explicit project
directory, env file and compose file) so it behaves the same from a shell and from systemd. If the
`postgres` service is not running, create fails with a message saying so rather than starting it.

### `backups.restore [--backup=latest|<id>] [--database=<name>] [--yes]`

Restores a backup of **the own system only**; it refuses ids that are not in `<root>/<own>/`. Getting
another machine's backup onto this one is a pull with `--system` (see Disaster Recovery), which keeps
"whose data is this" explicit.

1. Resolve the backup, verify every checksum in the manifest.
2. List what will be replaced — and databases that exist now but are not in the backup, which are
   left alone — and ask for confirmation unless `--yes`.
3. Make a backup of the current state labelled `pre-restore`, so a restore can itself be undone.
4. Stop whichever of `management`, `n8n`, `n8n-worker` and `workspaces` are running, remembering
   which.
5. Apply `globals.sql` without `ON_ERROR_STOP`, so existing roles survive their `CREATE ROLE` and
   still get the `ALTER ROLE` that follows. Statements for the `postgres` superuser are filtered out,
   because restoring its old password would desynchronise it from `INVOKE_POSTGRES_PASSWORD`.
6. Per database (or just `--database`): terminate connections, `DROP DATABASE IF EXISTS`,
   `CREATE DATABASE ... OWNER <owner from manifest>`, `pg_restore --exit-on-error` streamed into the
   container.
7. Start the services that were running before, in the finally block so a failed restore does not
   leave the stack down.

Role passwords come back as they were when the backup was made. On the same machine those match
`.env`; on a rebuilt machine they only match if `.env` was restored too, which BACKUPS.md spells out.

### `backups.register --name=<system> --host=<ssh alias> [--push]` and `backups.unregister --name=<system>`

Write and remove entries in `remotes.yml`. `register` refuses the own system name. For a pull remote
it checks reachability with `rsync --list-only -e 'ssh -o BatchMode=yes' <host>:./`, since the
restricted key allows nothing but rsync, and reports whether the listing contains backups whose
manifest names `<system>`. It warns rather than fails, so a remote can be registered before the other
side has run `backups.authorize`. For a push target it skips the check, because that needs the
passphrase.

### `backups.authorize --name=<system> --key=<public key or file> [--system=<name>] [--push] [--from=<address>] [--revoke]`

Run on the machine being connected to. It writes one line into `~/.ssh/authorized_keys`, tagged with a
comment `just-build-backups:<name>` so rerunning replaces it and `--revoke` removes it:

```
# read-only, the default: <name> may read this machine's own backups
restrict,command="rrsync -ro <root>/<own>" ssh-ed25519 AAAA... just-build-backups:a:read:<own>

# --push: <name> may write into its own directory here, and read nothing
restrict,command="rrsync -wo -no-del -munge <root>/<name>" ssh-ed25519 AAAA... just-build-backups:c:push:c
```

`--system` exposes a different directory read-only, which is only for disaster recovery: A runs
`backups.authorize --name=b --system=b --key=...` so a rebuilt B can fetch its own backups back.
`--from` adds a `from="<address>"` restriction for machines with a fixed address. With `--push`,
authorize creates `<root>/<name>/` so the sender has somewhere to write.

### `backups.pull [--remote=<name>] [--system=<name>]`

Without arguments, pulls every `direction: pull` remote. For each:

```
rsync -a --ignore-existing --delay-updates --exclude '*.partial' --exclude '*.corrupt' \
    -e 'ssh -o BatchMode=yes' <host>:./ <root>/<system>/
```

`<host>:./` is whatever directory the source's rrsync line exposes to this key, which by default is
the source's own system directory: **a pull fetches what the remote made itself, not what it holds for
others.** A pulls C from C, not via B. That keeps every copy one hop from its source and avoids two
routes writing the same directory. `<system>` is the remote's name, and because the client cannot
choose what it is served, the manifest check below is what guarantees it got that system.

There is no `--delete`: the remote pruning its old backups does not prune ours, local retention does.
`--ignore-existing` means a backup already held is never rewritten, only completed if a previous
transfer was cut short. After transfer, every backup that arrived is verified against its manifest,
including that `manifest.system` is `<system>`. A backup that fails is renamed to `<id>.corrupt`,
which status reports and prune and restore ignore, and which the next pull fetches afresh. Prune runs
for the pulled systems afterwards.

`--system` stores and checks the pull as a different system, which is the disaster-recovery path: once
A has authorized it with `--system=b`, a rebuilt B runs `backups.pull --remote=a --system=b` to get
its own backups back.

### `backups.push [--remote=<name>]`

Manual only. Without arguments, pushes to every `direction: push` remote:

```
rsync -a --ignore-existing --delay-updates --exclude '*.partial' --exclude '*.corrupt' <root>/<own>/ <host>:./
```

There is no `BatchMode` here on purpose: the push alias points at a key the user made, with a
passphrase, so ssh prompts for it or takes it from a running ssh-agent. Before connecting, push reads
the alias's identity files with `ssh -G <host>` and refuses if one of them is the passphrase-less job
key, so the unattended key can never be used to write, even by mistake. A push key without a
passphrase gets a warning. The receiver's rrsync line only lets it write into its own directory there,
and `--ignore-existing` stops it overwriting backups the receiver already has.

Only the own system is ever pushed, for the same reason pull only fetches the source's own. On success
it writes `<root>/.state/pushed-<remote>.json` with the newest pushed id and the time, which is what
lets status say something about push targets without connecting to them. The receiver prunes the
directory with its own `backups.run`, so a machine that receives pushes needs the timer installed too.

### `backups.status [--system=<name>]`

Reads only local files: no SSH, no database. One row per system, ordered own system first, then
registered remotes by name, then directories under the root that are not registered (typically
systems that push to this one):

```
SYSTEM  SOURCE        LATEST                AGE   BACKUPS  MONTHLY  SIZE     NOTE
a       own           2026-11-01 03:00 UTC  2h    15       3        1.2 GB
b       pull          2026-11-01 02:30 UTC  3h    15       3        800 MB
c       pull          2026-10-28 02:00 UTC  4d    12       2        40 MB    stale
x       unregistered  2026-11-01 01:00 UTC  4h    14       1        12 MB

PUSH TARGET  LAST PUSHED          AT
b            20261101T030000Z     2026-11-01 03:04 UTC
```

Stale means older than `stale_after_hours`. Notes also flag a registered remote with no directory yet,
`.corrupt` backups, and a push target whose last push is behind the newest own backup. With
`--system`, it lists every backup of that system with its label, size and whether retention keeps it
because it is recent, a monthly keeper, or the newest own backup.

### `backups.prune [--system=<name>] [--dry-run]`

Applies retention to every system directory, or one. Normally run implicitly by create, pull and run.

### `backups.clean [--before=YYYY-MM-DD] [--system=<name>] [--dry-run] [--yes]`

Deletes very old backups on this machine. Without `--before` the cutoff is `clean_after_days` (365)
before today; `--before` replaces that with a fixed date, so `--before=2026-01-01` removes everything
made before 2026. It applies to every system directory unless `--system` narrows it, although in
practice it only finds the own system's old monthlies, because prune already keeps held systems to a
year.

It lists what it is about to delete per system, with sizes, and asks for confirmation unless `--yes`;
`--dry-run` stops after the list. It never deletes the newest own backup, whatever the cutoff. Clean
is manual only: `backups.run` does not call it, so the timer never throws away history nobody asked
to lose.

### `backups.run`

What the timer calls: create, pull from all pull remotes, prune everything. It never pushes, because
the timer only holds the read-only key. A failing remote is reported and skipped so it cannot hold up the others, and the task exits non-zero
at the end if any step failed, so systemd records the run as failed and `journalctl` shows why.

## Installation On A Machine

```bash
invoke install.environment --missing     # then fill in INVOKE_BACKUPS_SYSTEM in .env
source activate.sh
invoke install.backups
```

`install.backups` follows the `install.daemon` pattern: a thin task calling
`services/backups/install.sh`, which

1. checks `docker`, `rsync`, `rrsync`, `systemctl` and `sudo` exist, `.env` exists, `INVOKE_BACKUPS_SYSTEM` is
   set and a valid name, and the user can reach the Docker socket;
2. creates the root with mode 700;
3. generates the job key `~/.ssh/just_build_backups` (ed25519, no passphrase) if it does not exist,
   and prints its public key with the `backups.authorize` command to run on each source;
4. renders `services/backups/systemd/just-build-backups.service` and `.timer` with the repository
   path, the current user and the schedule, installs them under `/etc/systemd/system` with sudo, and
   enables the timer;
5. runs `invoke backups.create` once so the machine has a backup and the setup is proven, then prints
   `systemctl list-timers just-build-backups.timer`.

The service is `Type=oneshot`, runs as the installing user with `WorkingDirectory=<repo>`,
`EnvironmentFile=<repo>/.env` (the generated secrets are alphanumeric, so systemd parses it the way
`activate.sh` does) and `ExecStart=<repo>/venv/bin/invoke backups.run`, with `Nice=10` and
`IOSchedulingClass=idle`. The timer uses `Persistent=true`, so a machine that was off at 03:00 backs
up when it comes back — which is what makes the monthly keeper reliable on machines that are not
always on. Running as the user means SSH uses the user's `~/.ssh/config` aliases.

`--schedule` overrides `backups.schedule`, rerunning is idempotent, and `--uninstall` disables and
removes the units without touching any backups.

## Scheduling Across Machines

Each machine runs one cycle a day, so the order across machines decides how fresh the held copies
are. Staggering the schedule edge-first makes the chain settle in one night:

| machine | schedule | does |
| --- | --- | --- |
| C | 02:00 | create |
| B | 03:00 | create, pull C |
| A | 04:00 | create, pull B, pull C |

With the default schedule everywhere nothing breaks; held copies are just a day behind. Pushes are not
part of any schedule; a holder that cannot reach its source only gets what is pushed by hand.

## SSH Access

Two kinds of key, with a hard line between them:

| | job key | push key |
| --- | --- | --- |
| made by | `install.backups`, as `~/.ssh/just_build_backups` | the user, wherever they like |
| passphrase | none, so the timer can use it | expected; push warns without one |
| used by | `pull`, `register`'s check, the timer | `push`, run by hand |
| allowed on the other side | `rrsync -ro <root>/<own>`: read one directory | `rrsync -wo -no-del -munge <root>/<sender>`: write one directory |
| SSH alias | `just-build-<name>` | `just-build-<name>-push` |

**Yes, SSH can scope a key to one directory, read-only.** OpenSSH's `command="..."` in
`authorized_keys` forces every connection with that key to run that one command, whatever the client
asked for. `rrsync`, which ships with rsync (`/usr/bin/rrsync` on current Debian and Ubuntu), is the
command made for this: it accepts only rsync's own server invocation, rewrites every path to sit
inside the directory it was given, and with `-ro` refuses anything that sends files to it. With
`restrict` in front, the key also gets no shell, no pty, and no port, agent or X11 forwarding. A
stolen job key can therefore do exactly one thing: read the backups of the one system it was
authorized for, which the machine the key was taken from already held a copy of. It cannot delete, overwrite, add,
look elsewhere on the disk, or reach anything over the network.

The push key gets the mirror image, `rrsync -wo -no-del -munge`: it can write into the sender's directory on the
receiver and cannot read anything, including the receiver's own backups or what it holds for others.
Since nothing ever runs with `--delete` and both directions use `--ignore-existing`, neither key can
destroy or alter a backup the other side already has.

Remaining points:

- Backups contain all data and role password hashes. The root is mode 700 and stays outside the
  repository.
- The restriction lives in the source's `authorized_keys`, under the normal user's account. That is
  the right boundary as long as nobody adds an unrestricted line for the same key, which is why
  `backups.authorize` writes these lines rather than BACKUPS.md asking for them to be typed. A
  dedicated backup user with only group read on the root is a stricter option BACKUPS.md mentions but
  the plan does not build.
- Backups are not encrypted at rest, by decision.

## Disaster Recovery

The recipes BACKUPS.md needs to carry, because they are the reason the rest exists:

- **Bad migration or deleted data on a live machine:** `invoke backups.restore` (whole system) or
  `--database=<name>`; the pre-restore backup undoes it.
- **Machine rebuilt:** install as usual with the **original `.env`** (keep it in `pass`) and the same
  `INVOKE_BACKUPS_SYSTEM`. On the holder, `invoke backups.authorize --name=b --system=b --key=<new job
  key>`. On the rebuilt machine, register the holder, `invoke backups.pull --remote=a --system=b`,
  `invoke backups.restore`. Revoke the recovery line on the holder afterwards.
- **Moving an installation to new hardware:** the same as a rebuild.

## Not Covered

Stated in BACKUPS.md so nobody assumes otherwise:

- **n8n's encryption key**, in the `n8n` volume. A restored n8n database without it has workflows but
  unreadable credentials (see the n8n section of INSTALLATION.md). Noted, and out of scope.
- **Workspace home directories and secrets** (`workspaces_homes`, `workspaces_secrets`); their code
  lives in git, but uncommitted work does not.
- **`.env`**, which belongs in `pass`, not next to the data it unlocks.
- **Redis**, which only holds queues and cache.

## Tests

Plain pytest, no Django, in `services/backups/tests/`, run the same way as `workspaces/cli/tests/`:

- retention: table tests over id lists — the 14-day window boundary, month keepers when the 1st is
  missing, month boundaries in UTC, the newest own backup surviving a failing create, a silent held
  system aging out completely apart from its monthly keepers, held monthlies past 12 months dropped
  while own ones stay, labelled backups;
- clean: the default cutoff, `--before` replacing it, the newest own backup surviving any cutoff;
- storage: manifest round trip, checksum failure, `.partial` and `.corrupt` ignored, system mismatch;
- status: ordering own → registered → unregistered, stale and push-behind notes, built from a fixture
  directory tree;
- registry: register/unregister round trip, refusing the own name;
- authorize: the read-only and push lines, `--system` and `--from`, replacing an existing line for the
  same name, `--revoke`, leaving unrelated lines untouched;
- push: refusing an alias whose identity files include the job key;
- create, restore, pull and push: the commands they build and the order they run them in, with a
  recording context like the one in `workspaces/cli/tests/test_clone.py`.

A manual end-to-end check before calling it done: create, restore into the same machine, and a pull
between two root directories on one host through an SSH alias to localhost.

## Decisions

Settled before implementation:

1. **Same daily retention everywhere, shorter monthly history for held systems.** Every system
   directory keeps the last 14 days plus the earliest backup of each month. Held systems keep 12
   months of those monthlies; the own system keeps them until `backups.clean` removes them (default:
   older than a year, or before a date given with `--before`). Only the own system additionally keeps
   its newest backup.
2. **"Keep 1st of month" means the earliest backup of each month**, which is the 1st's backup whenever
   the machine was on that day.
3. **PostgreSQL only.** The n8n encryption key is noted under Not Covered and stays out of scope.
4. **No encryption at rest.**
5. **No relaying.** C never connects to A. A pulls C directly, and if A cannot reach C, A has no copy
   of C. Push exists for holders that cannot reach their source, such as B for C, never as a route
   from C to A.
6. **Unattended means read-only.** The passphrase-less job key is only ever authorized with
   `rrsync -ro` on one directory, and only pulls run on the timer. Writing to another machine is a
   manual push with a separate, user-supplied key that is expected to have a passphrase.

## Implementation Notes

Where the build ended up differing from, or adding to, the plan above:

- Push lines carry `-no-del -munge` on top of `-wo`. Verified against rrsync 3.2.7: without `-no-del` a
  pusher's own client could still send `--delete` into its directory on the receiver.
- Transfers use `--delay-updates`, so a pull or push that is cut short leaves directories without a
  manifest, which nothing counts, rather than complete looking backups with files missing. Pull reads
  `--out-format=%n` to know which backups arrived and verifies exactly those.
- Restore creates databases from `template0`, so they are exactly what the dump describes, rather than
  inheriting the vector extension `setup_database.sh` installs into `template1`.
- Code lives in `services/backups/`: `storage.py`, `retention.py`, `remotes.py`, `access.py`,
  `transport.py`, `postgres.py`, `status.py` and `tasks.py`, with tests under `tests/` run by
  `invoke backups.test`.
- Verified end to end: create and restore (full and `--database`) against a throwaway pgvector
  project, including role passwords and vector columns; pull, push and the register check through real
  `rrsync` with a stand-in for ssh, including the refusals (writing or deleting with a read key, reading
  or deleting with a push key, escaping the directory); `backups.create` against the live stack through
  the CLI with a bare environment like systemd's; rendered units with `systemd-analyze verify`.
  `install.backups` itself was not run, because it installs system units with sudo.
- The default root became `<repository>/backups/`, ignored by git, instead of `~/backups/just_build`,
  and `.env.example` does not propose `INVOKE_BACKUPS_ROOT`; `backups.root` remains overridable. Other
  machines never need the repository's location, because the rrsync line `backups.authorize` writes on
  each machine carries its own absolute path. Moving the repository means rerunning that machine's
  `backups.authorize` commands and `install.backups`.
- Most machines are not on at 03:00, so the timer became hourly plus ten minutes after boot, and
  `backups.schedule` became `backups.daily_at` (03:00 local). A check backs up only when the own system
  has no backup since the most recent 03:00, so always-on machines still back up at 03:00 and others at
  their first check after; every check pulls. A stack that is not up or a remote that is off (ssh exit
  255, with a 20 second connect timeout) is skipped until the next check rather than failing the run.
  The staggered per-machine schedules are gone, and `install.backups --schedule` with them.
- First real install: the timer fired the moment `install.sh` started it (its boot delay had passed)
  and lost the lock to the install's own first backup, leaving the unit failed. Install now runs the
  service once through systemd before enabling the timer, and a timer check that finds the root locked
  skips itself instead of failing. `backups.status` reports a running task.
- `backups.connect [--name] [--address] [--user]` prints the job key with the authorize command for the
  other machine, the SSH alias and the register command, filled in as far as the options allow.
  `install.backups` ends by calling it, rather than echoing its own version.
- The registry moved from `remotes.yml` into `<root>/config`, an SSH config with one
  `just-build-backup-<name>` block per system, written by `backups.register --name --address [--user]
  [--port] [--push --identity]`. Every ssh and rsync call passes it with `-F`, so `~/.ssh/config` needs
  no aliases. A block using the job key is a pull remote, any other key a push target, which is the
  same line the SSH access rules draw. Options added to a block by hand survive rewrites.
- Keys moved under the root as well, next to `config`: the job key is `<root>/keys/job`, made by
  `install.backups`, and `register --push`
  makes `<root>/keys/push-<name>`, with ssh-keygen asking for its passphrase, then prints the
  `authorize --push` command for the receiver. Nothing is used from `~/.ssh` apart from
  `authorized_keys`. `backups.check` replaces every hand-typed ssh or rsync command the docs suggested,
  and BACKUPS.md became `backups/README.md`.
- Push was removed entirely: backups only ever move by pulling, with the read-only job key, and no
  machine can write to another. That took `backups.push`, `register --push`, `authorize --push`, push
  keys and the push status table with it. The job key lives in `<root>/keys/job`.
