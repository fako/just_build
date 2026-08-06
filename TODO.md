* Put management inside its own container
* Rename /var/log/projects to /var/log/workspaces
* When doing a workspaces.sync the SSH secrets under $HOME/.ssh/id_ed25519 get a permission reset somehow and that messes up SSH access
* Allow to add a Celery app to a workspace
* Write a fabfile to manage some stuff like copy, login and other utility


To be speced out further before implementation:
* Generate passwords for toplevel .env.example instead of a simple cp in INSTALLATION.md
