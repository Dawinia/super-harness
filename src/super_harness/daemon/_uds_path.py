"""Shared AF_UNIX socket path resolution per daemon-architecture §3.6 #8.

Linux's `sockaddr_un.sun_path` is 108 bytes; macOS is 104. We use the tighter
bound. When `<workspace>/.harness/daemon.sock` exceeds that limit, both the
daemon (server) and the discoverer (client / supervisor / hook) must compute
the same fallback path — `$TMPDIR/super-harness-<sha256(workspace)[:16]>.sock` —
or the supervisor will spawn a daemon and then fail to find its socket.

This module is the single source of truth for that algorithm.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

__all__ = ["UDS_PATH_MAX", "resolve_socket_path"]

# sockaddr_un.sun_path limits: Linux 108, macOS 104. Use the tighter bound so
# that fallback behavior is identical across platforms.
UDS_PATH_MAX: int = 104


def resolve_socket_path(workspace_root: Path) -> Path:
    """Return the AF_UNIX socket path super-harness uses for `workspace_root`.

    If `<workspace_root>/.harness/daemon.sock` fits within `UDS_PATH_MAX`
    bytes, that path is returned verbatim. Otherwise the fallback per spec
    §3.6 #8 is used: `$TMPDIR/super-harness-<sha256[:16]>.sock`, where the
    hash is derived from the resolved (symlink-followed) workspace path.

    The hash MUST be computed as
    `hashlib.sha256(str(workspace_root.resolve()).encode("utf-8")).hexdigest()[:16]`
    — any deviation breaks daemon/client agreement on socket location.
    """
    default = workspace_root / ".harness" / "daemon.sock"
    if len(str(default).encode("utf-8")) <= UDS_PATH_MAX:
        return default
    workspace_hash = hashlib.sha256(
        str(workspace_root.resolve()).encode("utf-8")
    ).hexdigest()[:16]
    return Path(os.environ.get("TMPDIR", "/tmp")) / f"super-harness-{workspace_hash}.sock"
