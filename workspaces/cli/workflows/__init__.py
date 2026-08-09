from invoke.collection import Collection

from workspaces.cli.workflows.tasks import list_workflows, pull, repush


namespace = Collection("workflows")
namespace.add_task(list_workflows)
namespace.add_task(pull)
namespace.add_task(repush)
