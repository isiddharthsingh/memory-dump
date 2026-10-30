"""The unprivileged user that the children reading untrusted files run as
(app/thumbnails.py, app/extractors/reel.py), the environment and memory they
get, and reading what they send back within bounds.

Those children decode what anyone who can sign up uploads or links to, and
each gets none of the worker's environment. Run as the worker's own user
(root in the image), one taken over through a decoder bug could still read
that environment from /proc, read the mounted secrets, and lift the limits it
was given. As USER, which the Dockerfile creates, it can do none of these. A
worker not running as root (on a laptop) starts them as itself: there is no
one to drop to.
"""

from __future__ import annotations

import asyncio
import os
import pwd
import resource
from collections.abc import Iterable
from pathlib import Path
from typing import Any

USER = "sandbox"
# What is kept of a child's stderr, for the log.
MAX_ERRORS = 64 << 10


def credentials() -> dict[str, Any]:
    """create_subprocess_exec's keyword arguments that start a child as USER.
    Raises PermissionError when the worker runs as root and USER is missing,
    rather than start the child as root."""
    if os.geteuid() != 0:
        return {}
    try:
        entry = pwd.getpwnam(USER)
    except KeyError:
        raise PermissionError(f"there is no {USER!r} user to start the child as") from None
    return {"user": entry.pw_uid, "group": entry.pw_gid, "extra_groups": []}


def env(names: Iterable[str]) -> dict[str, str]:
    """The worker's environment cut down to `names`: a child gets nothing
    else, so none of the worker's keys and passwords."""
    return {name: os.environ[name] for name in names if name in os.environ}


def limit_memory(cap: int) -> None:
    """Caps the calling child's address space at `cap`, and never above a
    hard limit the worker already runs under. Run as USER, the child can't
    lift it again."""
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    cap = cap if hard == resource.RLIM_INFINITY else min(cap, hard)
    resource.setrlimit(resource.RLIMIT_AS, (cap, cap))


def give(path: Path) -> None:
    """Makes `path` USER's, for a child to write in."""
    if owner := credentials():
        os.chown(path, owner["user"], owner["group"])


async def read_at_most(stream: asyncio.StreamReader, limit: int) -> bytes | None:
    """What `stream` gives until it ends, or None when that is over `limit`
    bytes. What is past the limit is read and dropped, never kept: the child
    can't fill the worker's memory, nor stall on a full pipe."""
    kept = bytearray()
    over = False
    while chunk := await stream.read(1 << 16):
        if not over and len(kept) + len(chunk) <= limit:
            kept += chunk
        else:
            over = True
            kept.clear()
    return None if over else bytes(kept)
