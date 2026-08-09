"""
The workspace's own invoke namespace.

Empty to begin with. Templates that bring tasks with them replace this file and add their collection,
which is why it is here at all rather than appearing the first time something needs it.
"""
from invoke.collection import Collection


namespace = Collection()
