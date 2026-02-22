from invoke import Collection

from workspaces import cli as workspaces_cli

namespace = Collection()
namespace.add_collection(workspaces_cli.namespace)
