"""
The workspace's own invoke namespace.

Replaces the empty one the default template writes, so that `invoke n8n.sync` works from the
workspace home. Keep this file to the assembly and put the work in the modules it imports.
"""
from invoke.collection import Collection

from n8n.tasks import namespace as n8n_namespace


namespace = Collection()
namespace.add_collection(n8n_namespace)
