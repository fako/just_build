from invoke import Collection

from workspaces.cli.setup import setup


namespace = Collection("workspaces")
namespace.add_task(setup)
