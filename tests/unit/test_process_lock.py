"""Unit tests for `maury.process_lock`."""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

import pytest

from maury.process_lock import (
    DEFAULT_LOCK_PATH,
    ProcessLockHeldError,
    process_lock,
)


def test_default_lock_path_under_xdg_config(tmp_path: Path) -> None:
    """Default lives under ~/.config/maury/."""
    assert DEFAULT_LOCK_PATH.name == ".lock"
    assert DEFAULT_LOCK_PATH.parent.name == "maury"


def test_acquire_and_release(tmp_path: Path) -> None:
    lock = tmp_path / ".lock"
    with process_lock(lock) as acquired:
        assert acquired == lock
        assert lock.is_file()
    # File survives after release (the dataclass-style contract); only
    # the OS-level lock state is cleared.
    assert lock.is_file()


def test_lock_writes_pid_inside_file(tmp_path: Path) -> None:
    lock = tmp_path / ".lock"
    with process_lock(lock):
        body = lock.read_text().strip()
    assert body == str(os.getpid())


def test_creates_parent_directory(tmp_path: Path) -> None:
    lock = tmp_path / "deeply" / "nested" / ".lock"
    with process_lock(lock):
        assert lock.is_file()
    assert lock.parent.is_dir()


def test_second_acquire_in_same_process_succeeds(tmp_path: Path) -> None:
    """flock is per-file-descriptor; same process reacquiring opens a new fd.

    Two sequential `with process_lock(...)` blocks in one process are
    not contention — they just open + close + reopen.
    """
    lock = tmp_path / ".lock"
    with process_lock(lock):
        pass
    with process_lock(lock):
        pass  # no error


def test_contention_from_child_process_raises(tmp_path: Path) -> None:
    """A second OS-level fcntl lock holder triggers ProcessLockHeldError."""
    import fcntl

    lock = tmp_path / ".lock"
    # Open a parallel handle holding the lock to simulate another process.
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ProcessLockHeldError) as exc_info, process_lock(lock):
            pass
        assert exc_info.value.lock_path == lock
        assert "another maury process" in str(exc_info.value)
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_release_on_exception_clears_lock(tmp_path: Path) -> None:
    """If the body raises, the lock still releases."""
    import fcntl

    lock = tmp_path / ".lock"
    with pytest.raises(RuntimeError, match="boom"), process_lock(lock):
        raise RuntimeError("boom")
    # After release, a fresh holder can acquire.
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)  # succeeds → not held
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_lock_file_pid_overwritten_each_acquisition(tmp_path: Path) -> None:
    """The PID-in-lock is rewritten each acquisition, not appended."""
    lock = tmp_path / ".lock"
    lock.write_text("old pid: 99999\n" * 100)  # decoy long content
    with process_lock(lock):
        body = lock.read_text()
    assert body.strip() == str(os.getpid())
    assert "99999" not in body
