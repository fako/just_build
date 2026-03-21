from invoke import Collection

from management import cli as management_cli
from workspaces import cli as workspaces_cli

namespace = Collection()
namespace.add_collection(workspaces_cli.namespace)
namespace.add_collection(management_cli.namespace)
