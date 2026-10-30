"""The unprivileged users that the children reading untrusted files run as
(app/thumbnails.py, app/extractors/reel.py), one per child, the environment
and limits they get, and reading what they send back within bounds.

Those children decode what anyone who can sign up uploads or links to, and
each gets none of the worker's environment. Run as the worker's own user
(root in the image), one taken over through a decoder bug could still read
that environment from /proc, read the mounted secrets, and lift the limits it
was given. Run as one user shared by all, it could still trace, signal or
read the files of a child working on another person's save, or leave a
process behind for the next. So each child gets a user of its own from
USERS, which the Dockerfile creates, held through a lock that any process in
the container (the worker, a script run beside it) takes before using one.
Everything running as a user is killed before it is handed out and when its
child is done. A worker not running as root (on a laptop) starts them as
itself: there is no one to drop to.
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import pwd
import resource
import signal
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# One per child that can run at once (the worker runs at most max_jobs jobs,
# each with one child), and room to spare.
USERS = tuple(f"sandbox{i}" for i in range(8))
# Processes and threads one child may have: Whisper, onnxruntime and ffmpeg
# use a few dozen, and a fork bomb stops here.
MAX_PROCESSES = 512
# What is kept of a child's stderr, for the log.
MAX_ERRORS = 64 << 10
# Rounds of killing a user's processes, KILL_WAIT_S apart (a killed process
# takes a moment to go), before giving up on the user.
KILL_TRIES = 50
KILL_WAIT_S = 0.02

# A user's lock file, root's alone, is held while a child runs as it, and let
# go by the kernel if the process holding it dies.
_LOCKS = Path("/run/memory-dump-sandbox")
# Users whose processes outlived every kill, by uid, with their lock, kept
# held until the worker restarts rather than handed to another child.
_quarantined: dict[int, int] = {}


@contextlib.asynccontextmanager
async def user() -> AsyncIterator[dict[str, Any]]:
    """A user from USERS that no other child has meanwhile, with nothing
    running as it, as create_subprocess_exec's keyword arguments; once the
    block is done, whatever still runs as it is killed. Raises PermissionError
    when the worker runs as root and USERS are missing, rather than start a
    child as root, and BlockingIOError when every one is taken."""
    if os.geteuid() != 0:
        yield {}
        return
    entry, lock = await _take()
    try:
        yield {"user": entry.pw_uid, "group": entry.pw_gid, "extra_groups": []}
    finally:
        await _let_go(entry.pw_uid, lock)


async def _take() -> tuple[pwd.struct_passwd, int]:
    try:
        entries = [pwd.getpwnam(name) for name in USERS]
    except KeyError as exc:
        raise PermissionError(f"there is no {exc.args[0]!r} user to start a child as") from None
    for entry in entries:
        lock = _lock(entry.pw_uid)
        if lock is None:
            continue
        # A process that held it may have died with something left running.
        if await _kill_all(entry.pw_uid):
            return entry, lock
        _quarantine(entry.pw_uid, lock)
    raise BlockingIOError("every sandbox user is running a child")


async def _let_go(uid: int, lock: int) -> None:
    if await _kill_all(uid):
        os.close(lock)
    else:
        _quarantine(uid, lock)


def _lock(uid: int) -> int | None:
    """`uid`'s lock, held, or None when another child has it."""
    _LOCKS.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(_LOCKS / str(uid), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _quarantine(uid: int, lock: int) -> None:
    log.error("processes still run as sandbox user %s: it isn't handed out again", uid)
    _quarantined[uid] = lock


def give(path: Path, owner: dict[str, Any]) -> None:
    """Makes `path` belong to `owner`, what `user` gave, for the child to
    write in."""
    if owner:
        os.chown(path, owner["user"], owner["group"])


def env(names: Iterable[str]) -> dict[str, str]:
    """The worker's environment cut down to `names`: a child gets nothing
    else, so none of the worker's keys and passwords."""
    return {name: os.environ[name] for name in names if name in os.environ}


def limit(memory: int) -> None:
    """Caps the calling child's address space at `memory` (never above a hard
    limit the worker already runs under) and its processes at MAX_PROCESSES.
    Run as a sandbox user, it can't lift them again."""
    _, hard = resource.getrlimit(resource.RLIMIT_AS)
    memory = memory if hard == resource.RLIM_INFINITY else min(memory, hard)
    resource.setrlimit(resource.RLIMIT_AS, (memory, memory))
    resource.setrlimit(resource.RLIMIT_NPROC, (MAX_PROCESSES, MAX_PROCESSES))


async def read_at_most(stream: asyncio.StreamReader, most: int) -> bytes | None:
    """What `stream` gives until it ends, or None when that is over `most`
    bytes. What is past it is read and dropped, never kept: the child can't
    fill the worker's memory, nor stall on a full pipe."""
    kept = bytearray()
    over = False
    while chunk := await stream.read(1 << 16):
        if not over and len(kept) + len(chunk) <= most:
            kept += chunk
        else:
            over = True
            kept.clear()
    return None if over else bytes(kept)


async def _kill_all(uid: int) -> bool:
    """Kills every process running as `uid`, the ones that left the child's
    process group too; True once none is left. MAX_PROCESSES bounds how many
    can appear meanwhile."""
    for _ in range(KILL_TRIES):
        pids = list(_running_as(uid))
        if not pids:
            return True
        for pid in pids:
            _kill(pid, uid)
        await asyncio.sleep(KILL_WAIT_S)
    return not any(_running_as(uid))


def _kill(pid: int, uid: int) -> None:
    """SIGKILL to `pid` if it runs as `uid`. Through a pidfd, taken before
    looking again: a process that got the pid meanwhile is left alone."""
    try:
        fd = os.pidfd_open(pid)
    except OSError:
        return
    try:
        if uid in _uids(pid):
            signal.pidfd_send_signal(fd, signal.SIGKILL)
    except OSError:
        pass
    finally:
        os.close(fd)


def _running_as(uid: int) -> Iterator[int]:
    """Live processes whose user is `uid` (Linux: /proc)."""
    for proc in Path("/proc").glob("[0-9]*"):
        if uid in _uids(int(proc.name)):
            yield int(proc.name)


def _uids(pid: int) -> set[int]:
    """The real, effective, saved and file system users of `pid`, or none when
    it is gone or a zombie."""
    try:
        status = Path(f"/proc/{pid}/status").read_text()
    except OSError:
        return set()
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    if fields.get("State", "").strip().startswith(("Z", "X")):
        return set()
    return {int(u) for u in fields.get("Uid", "").split()}
