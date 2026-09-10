"""Cross-process exclusive locks on workspace-local sentinel files.

The interface deliberately hides the host locking primitive. POSIX uses
``flock``; native Windows uses a one-byte ``msvcrt`` region lock. Callers only
choose the sentinel path and place their complete critical section inside the
context manager.
"""

from __future__ import annotations

import errno
import importlib
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, BinaryIO

if os.name == "nt":
    _windows_locking: Any = importlib.import_module("msvcrt")
else:
    _posix_lock: Any = importlib.import_module("fcntl")

__all__ = ["exclusive_file_lock"]

_RETRY_SECONDS = 0.01
_WINDOWS_CONTENTION_ERRNOS = frozenset({errno.EACCES, errno.EAGAIN, errno.EDEADLK})
_WINDOWS_CONTENTION_WINERRORS = frozenset({33, 36})


def _prepare_lock_byte(lock_file: BinaryIO) -> None:
    """Ensure byte zero exists and position the handle at its start."""
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"\0")
        lock_file.flush()
    lock_file.seek(0)


def _windows_lock(lock_file: BinaryIO) -> None:
    while True:
        lock_file.seek(0)
        try:
            _windows_locking.locking(
                lock_file.fileno(), _windows_locking.LK_NBLCK, 1
            )
            return
        except OSError as exc:
            if (
                exc.errno not in _WINDOWS_CONTENTION_ERRNOS
                and getattr(exc, "winerror", None) not in _WINDOWS_CONTENTION_WINERRORS
            ):
                raise
            time.sleep(_RETRY_SECONDS)


def _lock(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        _windows_lock(lock_file)
    else:
        _posix_lock.flock(lock_file.fileno(), _posix_lock.LOCK_EX)


def _unlock(lock_file: BinaryIO) -> None:
    if os.name == "nt":
        lock_file.seek(0)
        _windows_locking.locking(
            lock_file.fileno(), _windows_locking.LK_UNLCK, 1
        )
    else:
        _posix_lock.flock(lock_file.fileno(), _posix_lock.LOCK_UN)


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Block until an exclusive lock is held on ``path``, then release it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as lock_file:
        _lock(lock_file)
        try:
            _prepare_lock_byte(lock_file)
            yield
        finally:
            _unlock(lock_file)
