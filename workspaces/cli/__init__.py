"""
The workspaces namespace: workspace lifecycle only, and rarely used.

Day-to-day work lives in the runtimes namespace. What stays here is the privileged setup a workspace
needs once: its Linux account, secrets, database and SSH access.
"""
from invoke.collection import Collection

from workspaces.cli.clean import clean
from workspaces.cli.create import create
from workspaces.cli.init import init
from workspaces.cli.remove import remove
from workspaces.cli.setup import setup
from workspaces.cli.test import workspaces_test
from workspaces.cli.update import update


namespace = Collection("workspaces")
namespace.add_task(setup)
namespace.add_task(create)
namespace.add_task(init)
namespace.add_task(clean)
namespace.add_task(update)
namespace.add_task(remove)
namespace.add_task(workspaces_test)
