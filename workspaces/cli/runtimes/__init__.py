from invoke.collection import Collection

from workspaces.cli.runtimes.adopt import adopt
from workspaces.cli.runtimes.tasks import (
    add,
    configure,
    apply,
    disable,
    enable,
    list_runtimes,
    logs,
    remove,
    restart,
    start,
    status,
    stop,
    sync,
)


namespace = Collection("runtimes")
namespace.add_task(list_runtimes)
namespace.add_task(add)
namespace.add_task(adopt)
namespace.add_task(configure)
namespace.add_task(enable)
namespace.add_task(disable)
namespace.add_task(apply)
namespace.add_task(sync)
namespace.add_task(restart)
namespace.add_task(start)
namespace.add_task(stop)
namespace.add_task(status)
namespace.add_task(logs)
namespace.add_task(remove)
