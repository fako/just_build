from pathlib import Path
import fcntl
import struct
import sys
import termios

from invoke import runners
from invoke.collection import Collection
from invoke.terminals import has_fileno, isatty

# Invoke's console script loads this package without putting the repo root on sys.path.
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))


def bytes_to_read(input_) -> int:
    """
    Invoke's own version, with a buffer the size of what FIONREAD writes into it.

    Invoke 3.0.3 hands the ioctl two bytes for a four byte int. Python 3.14 checks that, raises
    SystemError on the first keystroke, and the thread forwarding stdin to remote commands dies, so
    any prompt in a workspace hangs. Remove this once invoke ships a fix.
    """
    if isatty(input_) and has_fileno(input_):
        fionread = fcntl.ioctl(input_, termios.FIONREAD, b"\0" * 4)
        return struct.unpack("i", fionread)[0]
    return 1


runners.bytes_to_read = bytes_to_read

from management import cli as management_cli
from tasks import install
from workspaces import cli as workspaces_cli
from workspaces.cli import remote as remote_cli
from workspaces.cli import runtimes as runtimes_cli
from workspaces.cli import workflows as workflows_cli
from services.whisperx import tasks as whisperx_cli

namespace = Collection()
namespace.add_collection(install.namespace)
namespace.add_collection(workspaces_cli.namespace)
namespace.add_collection(runtimes_cli.namespace)
namespace.add_collection(workflows_cli.namespace)
namespace.add_collection(remote_cli.namespace)
namespace.add_collection(management_cli.namespace)
namespace.add_collection(whisperx_cli.collection)
