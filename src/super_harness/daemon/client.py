"""Pure-protocol UDS client for the super-harness daemon.

Layer 1 of the client/supervisor split per daemon-architecture §3.5:
this module knows the wire protocol and nothing else. It does not spawn
daemons, does not write audit logs, does not implement fail-safe — those
are Layer 2 (`supervisor`) concerns.

Failure model:
- Transport-level errors (socket missing, connect refused, recv timeout)
  raise `DaemonUnreachable` / `DaemonTimeout`. Supervisor catches and
  triggers the fail-safe spawn path.
- Protocol-level errors (server returns a 4xx/5xx in the response envelope)
  are returned as-is in the response dict. The supervisor inspects
  `resp["error"]` and decides (e.g. version-mismatch → restart daemon).
  This split keeps the client policy-free and the supervisor pluggable.
"""
from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

from super_harness.daemon.protocol import (
    GateQueryRequest,
    ProtocolError,
    decode_response,
    encode_request,
)


class DaemonUnreachable(Exception):
    """Raised when the daemon socket cannot be reached (missing / refused / proto)."""


class DaemonTimeout(DaemonUnreachable):
    """Raised when send/recv exceeds the supplied timeout.

    Subclasses DaemonUnreachable because supervisor treats both identically
    (trigger fail-safe spawn + fallback ALLOW).
    """


def query(
    socket_path: Path,
    *,
    method: str,
    params: dict[str, Any],
    timeout: float = 2.0,
    request_id: str | None = None,
) -> dict[str, Any]:
    """Send one request line to `socket_path`, return decoded response dict.

    Returns:
        {"id": ..., "result": <method result or None>, "error": <None or dict>}
        Protocol-level errors (`error` populated) are NOT raised; the
        supervisor decides what to do.

    Raises:
        DaemonUnreachable: socket missing / connect refused / decode error.
        DaemonTimeout: connect / send / recv exceeded `timeout`.
    """
    if not socket_path.exists():
        raise DaemonUnreachable(f"socket missing: {socket_path}")
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        try:
            s.connect(str(socket_path))
        except TimeoutError as e:
            raise DaemonTimeout(f"connect timeout: {socket_path}") from e
        except (ConnectionRefusedError, FileNotFoundError, OSError) as e:
            raise DaemonUnreachable(f"connect failed: {e}") from e

        req = GateQueryRequest(method=method, params=params, id=request_id)
        try:
            s.sendall(encode_request(req))
        except TimeoutError as e:
            raise DaemonTimeout(f"send timeout: {socket_path}") from e
        except OSError as e:
            raise DaemonUnreachable(f"send failed: {e}") from e

        try:
            line = s.makefile("rb").readline()
        except TimeoutError as e:
            raise DaemonTimeout(f"recv timeout: {socket_path}") from e
        except OSError as e:
            raise DaemonUnreachable(f"recv failed: {e}") from e
    finally:
        s.close()

    if not line:
        raise DaemonUnreachable("daemon closed connection before responding")

    try:
        resp = decode_response(line)
    except ProtocolError as e:
        raise DaemonUnreachable(f"protocol decode failed: {e}") from e

    return {"id": resp.id, "result": resp.result, "error": resp.error}
