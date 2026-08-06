from pathlib import Path
import sys

from invoke.collection import Collection

# Invoke's console script loads this package without putting the repo root on sys.path.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from management import cli as management_cli
from tasks import install
from workspaces import cli as workspaces_cli
from workspaces.cli import runtimes as runtimes_cli

namespace = Collection()
namespace.add_collection(install.namespace)
namespace.add_collection(workspaces_cli.namespace)
namespace.add_collection(runtimes_cli.namespace)
namespace.add_collection(management_cli.namespace)
