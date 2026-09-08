---
name: runtime addressing
overview: Give every runtime a stable name for container-to-container traffic that does not depend on what the outside world calls it. Container callers enter at `runtimes.workspaces:7000/r/<workspace>/<runtime>/`, which nginx rewrites onto the runtime's own internal server name; the host browser keeps `<subdomain>.localhost:7000` through a new explicit `is_primary` runtime; and fully-qualified external domains become a list a human or agent adds per runtime, ready for the home box and the VPS without any further routing work.
todos:
  - id: compose-alias
    content: Add the `runtimes.workspaces` network alias to the workspaces service. One-time, no top-level networks block needed.
    status: completed
  - id: nginx-router
    content: Add the /r/<workspace>/<runtime>/ location to the default server in workspaces/nginx/nginx.conf, rewriting Host onto the internal name.
    status: completed
  - id: runtime-names
    content: Rename HttpConfiguration.domain to subdomain, add a domains list, add Runtime.is_primary with a partial unique constraint, and derive server_names.
    status: completed
  - id: nginx-template
    content: Emit every name a runtime answers to from runtime.server_names instead of the single runtime.domain.
    status: completed
  - id: primary-surface
    content: Expose is_primary through the runtime API, plus runtimes.add --primary and runtimes.configure --primary/--no-primary, without which a new workspace could never claim its own name.
    status: completed
  - id: scaffold-allowed-hosts
    content: Change the scaffold settings template to ALLOWED_HOSTS = [".localhost"] so both the primary and internal names are covered by one entry.
    status: completed
  - id: backfill-existing
    content: Mark the existing web runtimes primary and apply. Workspace-owned files (ALLOWED_HOSTS, AGENT.md) are for their owners to change; this repository only ships the scaffold templates.
    status: completed
  - id: agent-docs
    content: Add a "How domains resolve" section to the scaffold AGENT.md so an agent in a new workspace knows which name to call from where.
    status: completed
  - id: dead-templates
    content: Delete workspaces/nginx/example.conf.template. The supervisor directory is left alone.
    status: completed
  - id: docs
    content: Document the two namespaces in INSTALLATION.md.
    status: completed
isProject: true
---

# Runtime Addressing Plan

## Problem And Shape Of The Solution

The automate workspace's n8n workflows carry 50 hardcoded `http://nginx:80/api/v1/...` URLs, naming a
service that exists in `just_automate` and not here. That is the symptom that surfaced the gap; fixing
those URLs is the workspace's own change, and this plan's job is only to give it an address worth
moving to. The obvious candidate, `automate.localhost:7000`, fails from inside a container — and the
reason is worth writing down, because it is the constraint the whole design bends around.

**`.localhost` is a rule; every other name is a record.** RFC 6761 makes anything ending in
`.localhost` resolve to loopback in the resolver itself — glibc, musl, systemd-resolved and every
browser answer it without asking a DNS server. So `automate.localhost` resolves *inside the n8n
container too*, to `[::1]`, where nothing listens. It is not that the name fails; it is that it
succeeds and means "me". Any other suffix — `.workspaces`, `.internal` — needs something to answer for
it, and Docker's embedded DNS answers only exact service names and aliases. A wildcard would need a
resolver service of our own.

The way out is to stop making the workspace part of a *hostname* that has to resolve, and make it part
of a *path* under one name that does. Exactly one name then needs DNS, so a single one-time alias
replaces a resolver:

```
 n8n / any container             ┌── workspaces container ───────────────────────┐
                                 │                                               │
  http://runtimes.workspaces     │  nginx :7000                                  │
       :7000/r/automate/web/…    │                                               │
            │                    │   default_server ──► /r/ router               │
            └───docker alias────►│      (Host matched no vhost)   │              │
                                 │                                │ rewrites Host│
                                 │                                ▼              │
 host browser                    │   server_name automate.web.localhost   ◄──────┘
                                 │   server_name automate.localhost    (is_primary)
  http://automate.localhost      │   server_name automate.example.com  (domains[])
       :7000/api/v1/  ──────────►│        │                                      │
   (RFC 6761 → 127.0.0.1,        │        ▼ 127.0.0.1:8003 uvicorn               │
    where :7000 is published)    └───────────────────────────────────────────────┘
```

The rewrite target, `automate.web.localhost`, is **never resolved by anything**. It is a Host header
that nginx compares to `server_name` as a string. That is what removes the resolver: no lookup happens,
so nobody needs to know the name exists, and the internal zone can stay `.localhost` forever no matter
what the outside world ends up calling things.

## Two Namespaces

Keeping these apart is the point of the plan. Conflating them is what produces a workflow that breaks
every time the deployment changes.

| | east–west | north–south |
| --- | --- | --- |
| who | n8n, management, a future container | a browser, on some network |
| addressed by | `runtimes.workspaces:7000/r/<ws>/<rt>/` | `<subdomain>.localhost:7000`, later real domains |
| resolved by | one Docker network alias | RFC 6761 today; real DNS later |
| changes when we move to the VPS | never | every time |
| owned by | this plan, generated | humans and agents, per runtime |

A workflow written against the east–west form is identical on a laptop, the home box and the VPS. That
is the property being bought, and it is worth more than the prettier URL a hostname scheme would give.

## The Internal Entrance

Two changes, both one-time and neither per-workspace.

`docker-compose.yml`, on the `workspaces` service — validated with `docker compose config`, and no
top-level `networks:` block is required because `default` is implicit:

```yaml
    networks:
      default:
        aliases:
          # The one name that has to resolve for container-to-container traffic. Everything past it is
          # a path, so this list never grows as workspaces are added.
          - runtimes.workspaces
```

`workspaces/nginx/nginx.conf`, inside the existing `listen 7000 default_server` block. A request from a
container arrives with `Host: runtimes.workspaces:7000`, matches no runtime's `server_name`, and lands
here:

```nginx
        # East-west entrance. The workspace and runtime travel in the path so that only one hostname
        # ever has to resolve; this rewrites them onto the name the runtime's own block answers to and
        # re-enters nginx on loopback, so static/, media/ and the upstream are the generated ones
        # rather than a second copy that can drift.
        location ~ ^/r/(?<ws>[a-z0-9-]+)/(?<rt>[a-z0-9-]+)(?<rest>/.*)$ {
            proxy_set_header Host ${ws}.${rt}.localhost;
            proxy_pass http://127.0.0.1:7000$rest$is_args$args;
            proxy_http_version 1.1;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
        }
```

No `map` and no generated router file: the Host is built from the regex captures, so this block is
static and correct for every workspace that will ever exist. Unknown names fail closed on their own —
a workspace with no such runtime produces a Host no `server_name` matches, which falls back to this
same default server's 404. Celery needs no special case for the same reason: it is not an
`HttpRuntime`, so it has no server block, so `/r/automate/worker/` is a 404.

Two consequences to accept knowingly. The second hop is a loopback connection, so `X-Real-IP` on the
inner block becomes `127.0.0.1`; the forwarded-for chain above preserves the real client. And absolute
URLs the app generates come back as `http://automate.web.localhost/...`, which a container cannot
follow — inert today (automate is django-ninja with no pagination links, and no workflow follows a
returned URL) and fixable per-workspace with `FORCE_SCRIPT_NAME` if it ever bites.

## Names A Runtime Answers To

`domain` is renamed to `subdomain`, which is what it has always been: one label, not a domain. Full
external names become a separate list, because they are a different type — a fully-qualified name that
will later carry certificate configuration, not a label to be composed with something.

```python
class HttpConfiguration(SupervisordConfiguration):
    # One label. Defaults to the workspace slug. Only the primary runtime uses it.
    subdomain: str | None = None
    # Fully-qualified names added by a human or agent: the home box, the VPS, a custom domain.
    # Management routes them; it does not own or verify them.
    domains: list[str] = Field(default_factory=list)
    workers: int = Field(default=2, ge=1, le=16)
```

`Runtime` gains `is_primary`, guarded the way `workflows` already guards `n8n_id` — a partial unique
constraint rather than application code, because "one primary per workspace" is a fact about the data:

```python
        constraints = [
            models.UniqueConstraint(
                fields=["workspace"], condition=Q(is_primary=True), name="runtime_unique_primary",
            ),
        ]
```

`clean()` rejects `is_primary` on anything that is not an `HttpRuntime`, so a Celery runtime cannot
claim the workspace's bare name.

On `HttpRuntime`, `domain` is replaced by two derivations:

```python
    @property
    def internal_domain(self) -> str:
        """
        The name the /r/ router rewrites to. Never resolved by anything — nginx matches it as a
        string — so it is fixed at .localhost regardless of what the installation is called outside.
        """
        return f"{self.workspace.slug}.{self.name}.localhost"

    @property
    def server_names(self) -> list[str]:
        names = [self.internal_domain]
        if self.is_primary:
            names.append(f"{self.settings.subdomain or self.workspace.slug}.localhost")
        return names + list(self.settings.domains)
```

Note what is *not* here: an installation-wide zone setting. External names are explicit and
fully-qualified, so there is no template to configure and nothing to migrate when the VPS arrives —
you add `automate.example.com` to `domains`. The `.localhost` names stay and are simply inert there.

## What The Templates Emit

`management/runtimes/templates/runtimes/nginx/http.conf` changes on one line:

```nginx
    server_name {{ runtime.server_names|join(" ") }};
```

Everything else in that template — `listen 7000`, the static/media aliases, the proxy body — is
untouched. This is deliberate: none of this changes *how* a runtime is served, only what it answers
to. When TLS and non-Django runtimes arrive they will bring new templates rather than conditionals in
this one.

`workspaces/nginx/example.conf.template` is deleted with it. It is a hand-maintained copy of a file
management now generates, superseded per [.cursor/plans/runtimes.plan.md](.cursor/plans/runtimes.plan.md),
and already drifted: it still documents `PROJECT_DOMAIN` as the single name a runtime answers to, which
is exactly the assumption this plan removes.

`workspaces/supervisor/` is left alone. Its `example.conf.template` is dead in the same way, but its
neighbour `supervisord.conf.template` is live — [workspaces/Dockerfile:59](workspaces/Dockerfile#L59)
copies it into the image and `load_supervisor_secret.sh` renders it into the running `supervisord.conf`
at boot — and one stale example is not worth the chance of a future reader taking both.

## Django Side

The scaffold template becomes one entry, because Django's leading dot matches a domain and all its
subdomains — `.localhost` covers `automate.localhost` and `automate.web.localhost` alike:

```python
ALLOWED_HOSTS = [
    "127.0.0.1",
    # Covers both the primary name a browser uses and the internal name the /r/ router rewrites to.
    # Real domains are added by whoever adds them to the runtime's `domains`.
    ".localhost",
]
```

`.workspaces` never appears here: the router rewrites Host before proxying, so Django never sees
`runtimes.workspaces`. `CSRF_TRUSTED_ORIGINS` needs nothing new either — n8n's HTTP node sends no
`Origin`, and the internal path is not browser traffic.

The three live workspaces were scaffolded before this and own their `settings.py` now. Those files
belong to their repositories, not to this one, so they are left alone: until their owners add the
entry, `/r/<workspace>/web/` answers 400 `DisallowedHost`. That is the expected symptom, and it is a
workspace-side change rather than a routing failure.

## What Agents Need To Know

An agent working inside a workspace is the main consumer of all this, and the failure it will hit is
the confusing one: `automate.localhost` *resolves* from inside a container and points at the container
itself, so the mistake reads as "connection refused" rather than "unknown host". That belongs in the
scaffold's [AGENT.md](workspaces/templates/default/AGENT.md), roughly:

> **How domains resolve.** Your runtime answers to more than one name, and which one to use depends on
> where the caller is.
>
> - **A browser on the host** reaches the workspace's primary runtime at
>   `http://<workspace>.localhost:7000/`. This works because every resolver treats `.localhost` as
>   loopback, and that is where the port is published.
> - **Another container** — n8n, or another workspace's runtime — uses
>   `http://runtimes.workspaces:7000/r/<workspace>/<runtime>/`. The workspace and runtime are in the
>   path, not the hostname.
> - **Never call `<workspace>.localhost` from inside a container.** It resolves, to the calling
>   container's own loopback, and fails as a refused connection rather than an unknown name.
> - **A real domain** (the home box, a VPS, a customer domain) is added to the runtime's `domains`
>   through management. Do not hand-write server blocks; they are generated and will be overwritten.
>
> Keep `.localhost` in `ALLOWED_HOSTS`: it covers both the browser name and the internal one.

This ships in the scaffold template only, so it reaches workspaces created from here on. The three
that already exist own their `AGENT.md`, and adding it there is their repositories' change to make.

## Sequenced Work

Nothing here changes how an existing name behaves, so the order is about when the new path starts
answering rather than about avoiding a broken window.

1. `compose-alias` and `nginx-router` — infrastructure, inert until something calls it.
2. `runtime-names` and `nginx-template` — the internal `server_name` starts being emitted.
3. `backfill-existing` — mark the existing web runtimes primary, `invoke runtimes.apply`.
4. **The gate:** `/r/automate/web/api/v1/` reaches the workspace. A 404 means routing is wrong and is
   this repository's problem; a 400 `DisallowedHost` means routing works and the workspace has yet to
   add `.localhost` to its `ALLOWED_HOSTS`, which is its own repository's change.
5. `agent-docs`, `dead-templates`, `scaffold-allowed-hosts` and `docs` — independent, any time.

Consumers migrate themselves after the gate. The automate workspace's own workflow URLs are that
repository's change, on its own schedule, and are not part of this plan.

## Verified

Run against the live stack during design; temporary configs removed afterwards.

| | |
| --- | --- |
| `automate.localhost` from the n8n container | resolves to `[::1]`, connection refused — the premise |
| Docker alias `runtimes.workspaces` covers `x.runtimes.workspaces` | **no** — exact match only, so no wildcard scheme without a resolver |
| `/r/automate/web/api/v1/` through the router | reaches Django (400 `DisallowedHost`, i.e. Host arrived correctly) |
| query strings across the rewrite | preserved |
| `/r/automate/worker/…` (celery) | 404, fails closed with no rule |
| `/r/nope/web/…` | 404 |
| `automate.localhost:7000/api/v1/` direct | 200, unaffected |
| the compose alias snippet | `docker compose config` resolves it, no top-level `networks:` needed |
| every live runtime's `configuration` JSON | `{}` for all five, so the `domain`→`subdomain` rename needs no data migration despite `extra="forbid"` |
| runtimes needing `is_primary` | three django rows; the two celery rows must stay false |

Not yet verified: the router placed inside the baked-in `default_server` on 7000 (tested as its own
server on 7001, so the logic is proven and the placement is not), and the alias applied to this
compose file rather than a throwaway network. Both are first-run checks, not design risks.

## Out Of Scope

Deliberately deferred, each because the design above lets it be deferred without rework:

- **TLS, 443, and 80→443 redirects.** Terminate at the edge when it arrives — one place for ACME, one
  wildcard covering every workspace — and leave the workspaces container plain HTTP on 7000. That
  keeps certificate material out of the workspaces volume and 443 out of the per-runtime template.
- **A DNS service.** dnsmasq with `--address=/runtimes.workspaces/<ip>` was verified to give working
  wildcard resolution with Docker passthrough intact, and CoreDNS with a suffix `rewrite` onto the
  service name would avoid pinning an IP. Neither is needed while the workspace lives in the path.
- **Real DNS for the home box and the VPS.** A subdomain of a domain you own, pointed at the private
  IP for the LAN box, gets DNS-01 wildcard certificates with no CA to install on phones; the catch is
  DNS rebinding protection dropping RFC1918 answers on some routers. North–south only, and it does not
  touch anything in this plan.
- **Per-workspace zones**, `FORCE_SCRIPT_NAME`, and non-Django runtime templates. All reachable from
  here; none of them blocking.
