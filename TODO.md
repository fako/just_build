* When doing a workspaces.sync the SSH secrets under $HOME/.ssh/id_ed25519 get a permission reset somehow and that messes up SSH access.
  Git commands route around this by loading the key into an ssh-agent instead of letting ssh read the file, so the
  permissions only matter to anything that uses the key directly.
* Workspaces created before workspaces.create generated a git keypair have no key in $HOME/.ssh, so workspaces.clone-repo
  refuses them for a missing `git_key_created` step. They need a way to get one without being recreated.
* Add more runtime types: FastAPI, Node and Laravel. The registry and templates are the only places that need touching.
* Celery beat as its own runtime type instead of the embedded `--beat` flag


To be speced out further before implementation:
* Scaffold templates restate pyproject.toml in full rather than merging, so a later template silently
  drops an earlier one's dependencies. `--templates=default,celery` works only because celery repeats
  default's list, and every new template has to keep repeating it. Merge the dependency arrays instead.
* n8n workflow definitions reference credentials by id, and those ids only exist in the n8n instance
  they were made in. A workflow moved between installations points at credentials that are not there,
  with nothing in management to notice. Some way of reporting dangling references would help.
* Scaffold templates hardcode a literal `web/` directory, so `workspaces.scaffold --runtime-module=portal`
  creates portal/ with django-admin but writes the template's settings into web/. Either render path
  segments through Jinja too, or drop the argument and fix the package name at web.
* Generate passwords for toplevel .env.example instead of a simple cp in INSTALLATION.md
* Redis database index allocation per workspace. Queues and cache keys are namespaced by module for now,
  which is enough for isolation but shares one database.
