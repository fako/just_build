---
name: credential store models
overview: Add a new Django `credentials` app implementation around `CredentialStore` and `Credential`, backed by the `pass` CLI for decryption and an explicit allowlist from `invoke.yml` for sync/history metadata. Include migrations, admin UX, a slug-based sync management command, and Django-style test coverage executed through `pytest`.
todos:
  - id: wire-app-and-models
    content: Implement `CredentialStore` and `Credential` models, store parsing helpers, caching, and app wiring in Django settings.
    status: completed
  - id: sync-and-command
    content: Add allowlisted sync/history logic driven by `invoke.yml`, plus a slug-based management command.
    status: completed
  - id: admin-grouping
    content: Build the admin registrations and grouped credential path display for nested folders.
    status: completed
  - id: fixtures-and-tests
    content: Add committed test pass repository data, Django fixture files, and Django-style tests run through pytest for parsing, caching, sync, command, and admin grouping.
    status: completed
isProject: false
---

# Credential Store Plan

## Scope

Implement the scaffolded app in [management/credentials/apps.py](/home/fako/Code/just_build/management/credentials/apps.py), [management/credentials/models.py](/home/fako/Code/just_build/management/credentials/models.py), and [management/credentials/admin.py](/home/fako/Code/just_build/management/credentials/admin.py), then wire it into [management/web/settings.py](/home/fako/Code/just_build/management/web/settings.py).

Reuse the existing UUID model style from [management/access_control/models.py](/home/fako/Code/just_build/management/access_control/models.py):

```6:12:/home/fako/Code/just_build/management/access_control/models.py
class Project(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    slug = models.SlugField(max_length=255, unique=True)
```

## Data Model And Store Behavior

Create two models:

- `CredentialStore`: UUID primary key, `name`, `slug`, and a filesystem path pointing at the root of a pass repository.
- `Credential`: UUID primary key, foreign key to `CredentialStore`, and a relative credential path such as `ai/openai`.

Add timestamps:

- `created_at` and `modified_at` managed by Django.
- `last_updated_at` on `Credential`, populated from repository history when available.

Implement store methods on `CredentialStore` for:

- Resolving the pass entry file from a relative key.
- Reading decrypted content via `pass show <key>` against the configured store location.
- Parsing the credential text into `value`, `headers`, and `notes` using the requested loose format: first line is value, then header-like lines until the first blank line, then free-form notes.
- Reading the set of allowed credential keys from a config entry in [invoke.yml](/home/fako/Code/just_build/invoke.yml), with a file-based indirection option only if that keeps the configuration cleaner.
- Deriving `last_updated_at` from git history when the store is versioned, using the entry file path; if unavailable, preserve the DB value.

Implement lazy loading and object-level caching on `Credential` so repeated access to `value`, `headers`, or `notes` does not re-read from disk during the lifetime of the model instance.

## Sync And Command Layer

Add `CredentialStore.sync()` to reconcile DB rows with the repository:

- Create missing `Credential` rows for newly allowlisted keys.
- Delete rows that are no longer allowlisted.
- Refresh `last_updated_at` for retained rows when git metadata is available.
- Optionally verify whether allowlisted entries still resolve in the store and surface failures cleanly, but do not discover additional credentials by scanning.
- Leave credential content itself uncached in the DB; runtime access should continue to resolve through the store.

Add a management command under [management/credentials/management/commands](/home/fako/Code/just_build/management/credentials/management/commands) that accepts a store slug, fetches the matching `CredentialStore`, runs `sync()`, and reports what changed.

## Admin UX

Extend [management/credentials/admin.py](/home/fako/Code/just_build/management/credentials/admin.py) with:

- Standard registration for `CredentialStore` and `Credential`.
- Readonly UUID/timestamp fields.
- A searchable credential list.
- A custom grouped rendering for credential paths so `ai/openai` appears under `ai`, `ai/new/openai` appears under `ai` with a `new` subsection, and deeper paths collapse everything after depth 2 into a joined subsection like `new/latest`.

The likely implementation is a custom changelist or computed display grouping in admin rather than introducing a tree library, since the repo has no existing hierarchical-admin dependency or pattern.

## Tests And Fixtures

Create a test package under [management/credentials/tests](/home/fako/Code/just_build/management/credentials/tests) and add Django fixture files under a `fixtures` directory there for baseline `CredentialStore` and `Credential` rows.

Create a committed test password-store tree under [management/credentials/tests/pass](/home/fako/Code/just_build/management/credentials/tests/pass) that is tracked by this repository only and includes:

- Valid entries covering flat, two-level, and deeper nested paths.
- Failure cases such as missing entries, malformed header sections, empty files, and entries without git history metadata.

Test coverage should include:

- Store lookup and parse behavior for `value`, `headers`, and `notes`.
- Lazy-load caching on a `Credential` instance.
- `last_updated_at` sourcing from git when available and DB fallback when not.
- `sync()` adding, deleting, and updating credential rows based on the configured allowlist rather than filesystem discovery.
- Management command lookup by slug and sync execution.
- Admin grouping logic at the path-depth boundaries you described.

Given your updated preference, tests should be written in Django’s style (`TestCase` / admin client / fixture loading) while still being executed by the existing `pytest` runner.

## Likely Supporting Files

Expect changes in:

- [invoke.yml](/home/fako/Code/just_build/invoke.yml)
- [management/credentials/models.py](/home/fako/Code/just_build/management/credentials/models.py)
- [management/credentials/admin.py](/home/fako/Code/just_build/management/credentials/admin.py)
- [management/credentials/apps.py](/home/fako/Code/just_build/management/credentials/apps.py)
- [management/credentials/migrations](/home/fako/Code/just_build/management/credentials/migrations)
- [management/credentials/tests](/home/fako/Code/just_build/management/credentials/tests)
- [management/credentials/management/commands](/home/fako/Code/just_build/management/credentials/management/commands)
- [management/web/settings.py](/home/fako/Code/just_build/management/web/settings.py)
- Possibly [management/requirements.txt](/home/fako/Code/just_build/management/requirements.txt) only if a small helper dependency becomes necessary, though the current plan assumes standard library plus shelling out to installed `pass`/`git`.

## Notes

Assumption for implementation: use the `pass` CLI for decryption, use an explicit allowlist in `invoke.yml` as the source of truth for which credential paths belong to each store, and use `git log -1 --format=%cI -- <entry-file>` to populate `last_updated_at` when possible. If the store path is not a git repo or the entry has no history, keep the existing DB value instead of failing.
