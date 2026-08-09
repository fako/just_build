"""
The workspaces namespace: workspace lifecycle only, and rarely used.

Day-to-day work lives in the runtimes namespace. What stays here is the privileged setup a workspace
needs once: its Linux account, secrets, database, SSH access and the project it starts from, which
is either scaffolded or cloned.
"""
from invoke.collection import Collection

from workspaces.cli.clone import clone_repo
from workspaces.cli.create import create
from workspaces.cli.remove import remove
from workspaces.cli.scaffold import scaffold
from workspaces.cli.test import workspaces_test


namespace = Collection("workspaces")
namespace.add_task(create)
namespace.add_task(scaffold)
namespace.add_task(clone_repo)
namespace.add_task(remove)
namespace.add_task(workspaces_test)
