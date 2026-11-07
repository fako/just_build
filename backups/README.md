# Backups

Every installation backs up its own PostgreSQL databases once a day, and installations can keep copies
of each other's backups over SSH. [INSTALLATION.md](../INSTALLATION.md#backups) has the two commands that
set up a single machine; this document covers the rest.


## How it fits together

Every machine is a named **system**, set with `INVOKE_BACKUPS_SYSTEM` in `.env`. A backup directory
belongs to the system that made it:

- a machine writes its own backups into `<root>/<own name>/`;
- copies of another system go into `<root>/<its name>/`, pulled from that machine;
- every machine prunes every directory it holds, locally, with the same rule.

No machine ever deletes anything on another machine. A remote that prunes, gets wiped or gets
compromised cannot remove the copies held elsewhere.

The example used throughout: three machines, where A keeps copies of B and C, B keeps copies of C, and
C is an edge that only backs itself up.

```
   ┌───────┐   pulls b, c
   │   A   │◄────────────────────────┐
   └───▲───┘                         │
       │ pull                        │ pull
   ┌───┴───┐   pulls c               │
   │   B   │◄──────────────┐         │
   └───────┘               │         │
                       ┌───┴───┐─────┘
                       │   C   │  edge: own backups only
                       └───────┘
```

A pull only fetches what the source made itself, never what it holds for others: A gets C's backups
from C, not through B. If A cannot reach C, A has no copy of C.

**Backups only ever move by pulling.** Pulling only needs read access, so it can run unattended, and no
machine can ever write to another. There is no push.


## What is backed up

Every database in the `postgres` container (management, n8n and one per workspace) as a
`pg_dump -Fc` file, plus the roles from `pg_dumpall --globals-only`. Test databases (`test_*`) are left
out; test runs recreate them.

Not backed up, so do not count on it:

- **n8n's encryption key**, in the `n8n` volume. A restored n8n database without it has its workflows
  but cannot read its credentials. See the n8n section of [INSTALLATION.md](../INSTALLATION.md#n8n).
- **Workspace homes and secrets** (the `workspaces_homes` and `workspaces_secrets` volumes). Code lives
  in git, but uncommitted work does not.
- **`.env`**. Keep it in `pass`. Restoring onto a rebuilt machine needs the original one, see
  [Disaster recovery](#disaster-recovery).
- **Redis**, which only holds queues and cache.

Backups are not encrypted at rest. The root directory is only readable by its owner.


## Retention

A backup is kept when any of these holds:

1. It is younger than 14 days. Every backup counts, so manual and pre-restore backups survive as well
   as the daily ones.
2. It is the earliest backup of its calendar month, in UTC. With a daily timer that is the one made on
   the 1st; if the machine was off on the 1st, it is the first one after. The own system keeps these
   until `backups.clean` removes them. Held systems keep 12 months of them.
3. It is the newest backup of the own system. If `backups.create` keeps failing for two weeks, the
   machine still has its last good backup. Held systems do not get this: a remote that goes silent
   ages out apart from its monthly backups, and `backups.status` is what tells you it went silent.

Everything else is deleted when prune runs, which happens after every create and pull and at the end of
every timer run. `invoke backups.prune --dry-run` shows what it would delete.

Old monthly backups of the own system are never pruned automatically. Remove them with
`backups.clean`, which by default deletes everything older than a year:

```bash
invoke backups.clean --dry-run              # what is older than a year
invoke backups.clean                        # delete it, after a confirmation
invoke backups.clean --before=2026-01-01    # everything made before 2026 instead
```

Clean never deletes the newest own backup. The timer never runs it.

The numbers are in the `backups` section of `invoke.yml`: `retention.days`, `retention.remote_months`
and `retention.clean_after_days`. Like other settings, they can be overridden from `.env`, for
instance with `INVOKE_BACKUPS_RETENTION_DAYS=30`.


## On disk

```
<repository>/backups/                  the backup root, mode 700, ignored by git
  README.md                            this document
  config                               SSH config with a block per registered system
  keys/                                mode 700
    job, job.pub                       the read-only job key, made by install.backups
  .lock                                held by every task that writes or deletes
  a/                                   own system
    20261101T030000Z/
      manifest.json
      globals.sql
      management.dump
      n8n.dump
      automate.dump
    20261102T030000Z.partial/          still being written; ignored everywhere
  b/
    20261101T020000Z/
    20261031T020000Z.corrupt/          failed verification on arrival
  c/
```

Backup ids are UTC timestamps. A backup is written under `<id>.partial` and renamed once complete, so
a directory with an id for a name and a `manifest.json` is always a complete backup. The manifest
records the system that made it, the PostgreSQL version, the repository commit, an optional label,
and the size, sha256 and owner of every file. Restores and pulls check it.

Everything the backups need lives here: the backups, the SSH config that says how to reach other
machines, and the keys to do it with. Nothing is kept in `~/.ssh`, apart from the `authorized_keys`
lines `backups.authorize` writes, since that is where sshd looks for them.

The root is the `backups` directory of the repository by default. To keep backups elsewhere, set
`backups.root` (or `INVOKE_BACKUPS_ROOT`) to another directory.

**`git clean -x` or `-X` deletes it**, keys included. Those flags remove ignored files, and everything
here is ignored apart from this README. Plain `git clean` leaves it alone.

Only one backup task runs at a time. A manual task that finds another one running fails with "Another
backup task is using …" instead of waiting; a timer check skips itself and tries again an hour later.
`backups.status` says when a task is running. The `.lock` file stays in the root on purpose: the lock is
held *on* it, and the kernel drops that the moment a task exits, even when it crashes.


## Setting up a machine

### Its own backups

```bash
invoke install.environment --missing     # then set INVOKE_BACKUPS_SYSTEM in .env
source activate.sh
invoke install.backups
```

`install.backups`:

1. checks for `docker`, `rsync`, `rrsync` (rsync 3.2.4 and later ship it as `/usr/bin/rrsync`),
   `systemctl` and `sudo`, and that you can reach the Docker socket;
2. creates the backup root, mode 700;
3. generates the **job key** `backups/keys/job` if it does not exist yet;
4. installs `just-build-backups.service` and `.timer` under `/etc/systemd/system`, which needs sudo;
5. runs the service once, exactly as the timer will, which makes the first backup and proves the
   setup, and shows its output;
6. only then enables the timer, and prints the next check and the command another machine needs to
   let this one pull from it.

To remove the timer again, leaving every backup where it is:

```bash
invoke install.backups --uninstall
```

The service runs `invoke backups.run` as you, from the repository, with `.env` loaded. Its output goes
to the journal:

```bash
journalctl -u just-build-backups.service
systemctl list-timers just-build-backups.timer
```

### When backups are made

Most machines are not on at 03:00, and the ones that are should still back up then. The timer handles
both the same way: it checks **every hour**, plus once **ten minutes after boot** when the stack has had
time to come up, and straight away after a suspend that swallowed a check. Each check runs
`backups.run`, which:

1. **makes a backup if one is due.** A backup day starts at `backups.daily_at`, 03:00 local time by
   default, and a backup is due when the own system has none since the most recent 03:00;
2. **pulls every registered system.** This is cheap when nothing is new, because backups already held
   are skipped, so a holder picks up a remote's new backup within an hour of both machines being on;
3. **prunes** every directory.

| machine | what happens |
| --- | --- |
| server, always on | backs up at 03:00; every other check that day only pulls and prunes |
| laptop, switched on at 09:00 | backs up ten minutes after boot, then not again until after the next 03:00 |
| on at 01:00 and still on at 03:00 | nothing at 01:00, since yesterday's backup counts for that day; backs up at 03:00 |
| off for a few days | one backup at the first check after it is back, not one per missed day |

A check never fails for things that will sort themselves out: if the stack is not up yet, or a remote is
off or out of reach, it says so and the next check tries again. Real problems do fail it, so systemd
records them: a dump that breaks, rrsync refusing a key, corrupt backups arriving.

To move the start of the backup day on one machine, set `INVOKE_BACKUPS_DAILY_AT=04:30` in its `.env`.
Nothing needs reinstalling, because `backups.run` reads it on every check.

### Keeping copies of another machine

On every machine in the example, run `invoke install.backups` first. Then, to let A pull B, start
**on A** with:

```bash
invoke backups.connect --name=b --address=b.example.com --user=fako
```

It prints A's job key and the two steps below with everything filled in, ready to copy. Every option
is optional; whatever is left out shows as a placeholder. `install.backups` prints the same at the end.

**On B**, authorize A's job key, with the command `backups.connect` printed:

```bash
invoke backups.authorize --name=a --key='ssh-ed25519 AAAA... just-build-backups:a@host-a'
```

**On A**, register B:

```bash
invoke backups.register --name=b --address=b.example.com --user=fako
```

`--user` is only needed when B runs just_build as a different user than A does, and `--port` when B's
SSH port is not 22. Register writes this block to `backups/config`:

```ssh-config
Host just-build-backup-b
    HostName b.example.com
    User fako
    IdentityFile /home/fako/Code/just_build/backups/keys/job
    IdentitiesOnly yes
```

Register checks the connection straight away. It reports how many backups of `b` it can see, or warns
if it cannot connect yet. Then `invoke backups.pull` fetches them now, without waiting for the timer.

For the full example, also authorize A on C, authorize B on C, and register C on both A and B.

#### The `backups/config` file

`backups/config` is an ordinary SSH config, and it is also the list of registered systems: every
`Host just-build-backup-<name>` block in it is one. All backup tasks connect with `ssh -F backups/config`,
so `~/.ssh/config` plays no part and nothing there can interfere. There is no need to connect by hand:
`invoke backups.check` tests every registered system the way the tasks connect to it.

Every block connects with the job key, which other machines only ever authorize to read.

You can edit the file. `HostName`, `User`, `Port` and any options you add to a block, such as
`ProxyJump`, are kept when `register` or `unregister` rewrites it; anything outside a block is not.
Running `register` again for a name replaces its address, user and port.

## SSH access

There is one key, the **job key** `backups/keys/job`, made by `install.backups`. It has no passphrase,
so the timer can use it, and that is only acceptable because every other machine authorizes it to read
one directory and nothing else.

`backups.authorize` writes a line like this into `~/.ssh/authorized_keys` on the machine being
connected to:

```
restrict,command="/usr/bin/rrsync -ro /home/fako/Code/just_build/backups/b" ssh-ed25519 AAAA... just-build-backups:a:read:b
```

- `command=` makes sshd run that command for every connection with this key, whatever the client
  asked to run.
- `rrsync` only accepts rsync's own server side, keeps every path inside the directory it was given,
  and with `-ro` refuses anything that would send files to it.
- `restrict` takes away the shell, the pty, and port, agent and X11 forwarding.

So a stolen job key can only do one thing: read the backups of the one system it was authorized for,
which the machine it was stolen from already held a copy of. It cannot delete, overwrite, add files,
look anywhere else on the disk, or open tunnels.

Pulls never use `--delete`, and use `--ignore-existing`, so a backup this machine already holds is
never rewritten by what a remote serves. `IdentitiesOnly yes` in every block stops ssh from offering
other keys from your agent.

More `authorize` options:

```bash
invoke backups.authorize --name=a --key=~/a.pub --source=203.0.113.7    # only from this address
invoke backups.authorize --name=a --key=~/a.pub                         # again: replaces the line
invoke backups.authorize --name=a --revoke                              # remove the access
```

The lines are tagged in their comment (`just-build-backups:<name>:read:<directory>`), so
`authorize` only ever replaces or removes its own lines. Do not also add an unrestricted line for the
same key by hand; sshd would accept either.

A stricter option is a separate unix user that only has group read access to the backup root. It is
not built in.


### Where the repository lives on each machine

Other machines never need to know. The repository, and with it the backup root, can be cloned to a
different place on every machine:

- **A puller asks for `<alias>:./`**, which means "whatever directory this key is allowed". It sends no
  path of its own.
- **The machine being connected to decides what that directory is.** `backups.authorize` runs there,
  knows its own repository, and writes the absolute path into the rrsync line, as in
  `command="rrsync -ro /srv/just_build/backups/b"` on one machine and
  `command="rrsync -ro /home/fako/Code/just_build/backups/b"` on another.
- **The block in `backups/config`** only says how to reach the machine: host name, user, port and
  key.

The consequence: **after moving the repository, or changing `backups.root`, on a machine, run its
`backups.authorize` commands again**, because the old lines still point at the old directory. Rerunning
replaces them. Also rerun `invoke install.backups`, because the systemd unit names the repository path
too. The machines connecting to it need no changes.


## Freshness across machines

Every machine gets the same timer, and nothing has to be staggered. Because every check pulls, copies
travel along the chain hour by hour: with A, B and C all on overnight, C backs up at 03:00, B pulls it at
its next check, and A at its next one. Machines that are only on at different times catch up whenever
they overlap. `backups.status` shows how old each held copy is, and flags one as stale after 36 hours.


## Commands

| command | does |
| --- | --- |
| `backups.create [--label=…]` | back up now, then prune the own system |
| `backups.restore [--backup=latest\|<id>] [--database=…] [--yes]` | restore an own backup, see below |
| `backups.status [--system=…]` | latest backup per system, from local files only |
| `backups.register --name=… --address=… [--user=…] [--port=…]` | add a system to pull from |
| `backups.unregister --name=…` | forget a system; held backups stay until pruned |
| `backups.connect [--name=…] [--address=…] [--user=…]` | print the job key and the commands to connect a machine |
| `backups.check [--remote=…]` | test the registered systems the way the tasks connect to them |
| `backups.authorize --name=… --key=… [--system=…] [--source=…] [--revoke]` | let another machine read |
| `backups.pull [--remote=…] [--system=…]` | fetch registered systems now |
| `backups.prune [--system=…] [--dry-run]` | apply retention |
| `backups.clean [--before=YYYY-MM-DD] [--system=…] [--dry-run] [--yes]` | delete very old backups |
| `backups.run` | what the hourly timer runs: back up if due, pull, prune |
| `backups.test` | run the tests of this tooling |
| `install.backups [--uninstall]` | install or remove the timer |

`backups.status` reads only local files, so it never connects to another machine or the database. It
lists the own system first, then the registered systems, then any other directory under the root (for
example a system that was unregistered but whose backups are still held):

```
SYSTEM  SOURCE        LATEST                AGE  BACKUPS  MONTHLY  SIZE      NOTE
a       own           2026-11-20 03:00 UTC  2h   18       4        1.2 GB
b       pull          2026-11-20 03:01 UTC  2h   18       4        800.0 MB
c       pull          2026-11-16 02:00 UTC  4d   14       4        40.1 MB   stale
x       unregistered  2026-11-12 01:00 UTC  8d   6        1        12.0 MB   stale
```

"Stale" means the newest backup is older than `stale_after_hours` (36). Other notes are "no backups
yet" and "N corrupt". `--system=<name>` lists every
backup of one system, with its label and why retention keeps it.


## Restoring

`backups.restore` restores one of this machine's **own** backups:

```bash
invoke backups.restore                                  # the latest
invoke backups.restore --backup=20261101T030000Z        # a specific one, ids from backups.status --system=<own>
invoke backups.restore --database=automate              # just one database
```

It:

1. checks every file against the manifest, and refuses a backup that does not match;
2. lists which databases it will replace or create, and which it leaves alone, then asks for
   confirmation (skip it with `--yes`);
3. makes a backup of the current state labelled `pre-restore`. To undo the restore, restore that
   backup;
4. stops `management`, `n8n`, `n8n-worker` and `workspaces`, if they are running;
5. applies the roles. Existing roles get their attributes and passwords from the backup. The
   `postgres` superuser is skipped, so it keeps the password `.env` gives it;
6. drops, recreates and restores each database, owned by the role that owned it;
7. starts the services it stopped again, even if a step failed.

Role passwords come back as they were when the backup was made. On the same machine they match `.env`.
On a rebuilt machine they only match if `.env` is the original one.


## Disaster recovery

**Bad migration or deleted data:** `invoke backups.restore`, or `--database=<name>` for one database.
If that was the wrong backup, restore the `pre-restore` one it made.

**The machine is gone.** Say B was rebuilt and A holds its backups:

1. Install B as usual, with the **original `.env`** from `pass` and the same `INVOKE_BACKUPS_SYSTEM=b`.
   Run `invoke install.backups`, which gives B a new job key.
2. On A, let B read A's copy of B's backups:

   ```bash
   invoke backups.authorize --name=b --system=b --key='<B's new job key>'
   ```

3. On B:

   ```bash
   invoke backups.register --name=a --address=a.example.com
   invoke backups.pull --remote=a --system=b
   invoke backups.unregister --name=a
   invoke backups.restore
   ```

4. On A, remove the recovery access: `invoke backups.authorize --name=b --system=b --revoke`.

`--system=b` tells pull to store what it gets as B's own backups and to check that the manifests say
`b`. Unregistering A afterwards stops B's timer from trying to pull from it every night.

**Moving to new hardware** is the same as a rebuild.

Every so often, restore a backup somewhere you can afford to, to check that restores actually work.


## Troubleshooting

**`INVOKE_BACKUPS_SYSTEM is not set to a usable name`.** Set it in `.env` (lowercase letters, digits and
dashes), then `source activate.sh`.

**`The postgres service is not running`.** Create does not start the stack. Start it, then run again.

**`Another backup task is using …`.** A timer check or another manual task holds the root. Wait for it;
`backups.status` says when one is running, and `systemctl status just-build-backups.service` shows the
timer's. An empty `.lock` file on its own means nothing: it always stays.

**`register` warns it could not list the host.** The other machine has not run `backups.authorize`
for this machine's job key yet, or the address is wrong. `invoke backups.connect --name=<other>` prints
the authorize command again, and `invoke backups.check --remote=<other>` tries again, connecting exactly
as the timer does.

**`rrsync error: … not allowed`.** Something tried more than reading with the job key, which its line
refuses. The backup tasks only read, so check for something else using `backups/keys/job`.

**Pull reports corrupt backups.** The files did not match their manifest on arrival. The backup was set
aside as `<id>.corrupt`, and the next pull fetches it again. If it stays corrupt, the source's copy is
damaged: check it there with `backups.status --system=<name>`. Remove the `.corrupt` directory by hand
once you know why.

**No backup today.** Check `systemctl list-timers just-build-backups.timer` and
`journalctl -u just-build-backups.service`. "Not running yet" on every check means the stack never came
up while the machine was on. The service user needs access to the Docker socket, and the job key must be
readable without a passphrase.

**A remote is always "not reachable".** Pulls give up after 20 seconds without a connection.
`invoke backups.check --remote=<other>` shows the error ssh gives. If the address changed, register the
system again with the new one.
