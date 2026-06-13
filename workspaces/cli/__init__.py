from invoke.collection import Collection

from workspaces.cli.create import create
from workspaces.cli.enable import enable
from workspaces.cli.init import init
from workspaces.cli.setup import setup


namespace = Collection("workspaces")
namespace.add_task(setup)
namespace.add_task(create)
namespace.add_task(init)
namespace.add_task(enable)
