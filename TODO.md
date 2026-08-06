* When doing a workspaces.sync the SSH secrets under $HOME/.ssh/id_ed25519 get a permission reset somehow and that messes up SSH access
* Add more runtime types: FastAPI, Node and Laravel. The registry and templates are the only places that need touching.
* Celery beat as its own runtime type instead of the embedded `--beat` flag


To be speced out further before implementation:
* Generate passwords for toplevel .env.example instead of a simple cp in INSTALLATION.md
* Redis database index allocation per workspace. Queues and cache keys are namespaced by module for now,
  which is enough for isolation but shares one database.


Done:
* Put management inside its own container
* Rename /var/log/projects to /var/log/workspaces, now /var/log/workspaces/${workspace}/${runtime}.log
* Allow to add a Celery app to a workspace
* Write utility commands to manage some stuff like copy, login and other utility. These are the
  `remote.*` invoke tasks rather than a fabfile: fabric's task runner requires invoke<3.0 and this
  repository pins invoke 3.0.3, so `fab` cannot run here. Fabric's Connection is what they use.
