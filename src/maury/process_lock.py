"""Advisory per-host process lock for manifest-mutating commands.

Per ADR-0024 §"Other concurrency hygiene": `~/.config/maury/.lock`
(advisory fcntl) prevents two `maury` processes on the same host from
racing each other through manifest-mutating flows.

The lock is **advisory** — only processes that explicitly acquire it
are gated. The merge resolver and any manifest-mutating CLI command
should wrap their work in `process_lock(...)`; commands that only read
(validate, show, status, doctor) don't need it.
"""

from __future__ import annotations

import errno
import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from maury.paths import lock_file as _lock_file


@dataclass(frozen=True)
class ProcessLockHeldError(RuntimeError):
    """Raised when the lock is held by another process.

    Carries the lock path so the caller can surface it to the user.
    """

    lock_path: Path

    def __post_init__(self) -> None:
        # RuntimeError init via the dataclass-frozen pattern.
        super().__init__(f"another maury process holds {self.lock_path}; refusing to proceed.")


@contextmanager
def process_lock(lock_path: Path | None = None) -> Iterator[Path]:
    """Acquire an exclusive advisory fcntl lock on `lock_path`.

    Defaults to `paths.lock_file()` (`$XDG_CONFIG_HOME/maury/.lock`,
    default `~/.config/maury/.lock`; ADR-0029). Raises
    `ProcessLockHeldError` if another process holds the lock. Releases on
    exit (normal or exception).

    The lock file is created if absent; not deleted on release (so
    callers can inspect it). `fcntl.flock` is per-file-descriptor
    advisory locking, so different processes contend correctly.
    """
    path = (lock_path or _lock_file()).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Open in append mode so we don't truncate someone else's lock
    # file. Use os.open so we can pass O_CREAT explicitly.
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
                raise ProcessLockHeldError(lock_path=path) from None
            raise
        try:
            # Record this process's PID inside the lock file for
            # debuggability. Truncate first since prior runs may have
            # written longer content.
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n".encode())
            os.fsync(fd)
            yield path
        finally:
            # Lock auto-releases on close, but be explicit.
            with suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


__all__ = ["ProcessLockHeldError", "process_lock"]
