"""DaemonServer — AF_UNIX accept loop + per-connection gate dispatch.

Implements daemon-architecture §3.2 (threading model) + §3.4 (request flow) +
§3.6 #8 (socket path length fallback) + UC-8 (oversized request) + UC-9
(BrokenPipe survival) + AC-9 (0o600 socket).

The `_PRE_TOOL_USE_DECISIONS` table mirrors lifecycle-event-model §3.7's
"Gate 矩阵" verbatim: each of the 11 states maps to an `(decision, reason)`
pair. The daemon does NOT invent gate policy — it only executes the table.
"""
from __future__ import annotations

import hashlib
import logging
import os
import socket
import threading
from pathlib import Path
from typing import Any, ClassVar

from super_harness.daemon.hot_state import HotState
from super_harness.daemon.protocol import (
    MAX_REQUEST_BYTES,
    PROTOCOL_VERSION,
    GateQueryRequest,
    GateQueryResponse,
    ProtocolError,
    ProtocolVersionMismatch,
    decode_request,
    encode_response,
)

__all__ = ["DaemonServer"]

_log = logging.getLogger(__name__)

# sockaddr_un.sun_path limits: Linux 108, macOS 104. Use the tighter bound.
_UDS_PATH_MAX: int = 104


class DaemonServer:
    """Long-running UDS server for PreToolUse gate decisions.

    Per lifecycle-event-model §3.7 Gate 矩阵.
    """

    # 11-state decision table from lifecycle-event-model §3.7.
    _PRE_TOOL_USE_DECISIONS: ClassVar[dict[str, tuple[str, str]]] = {
        "INTENT_DECLARED": ("block", "INTENT_DECLARED: plan not drafted yet"),
        "AWAITING_PLAN_REVIEW": ("block", "AWAITING_PLAN_REVIEW: plan review in progress"),
        "PLAN_REJECTED": ("block", "PLAN_REJECTED: awaiting plan revision"),
        "PLAN_APPROVED": ("allow", "PLAN_APPROVED: implementation may proceed"),
        "IMPLEMENTATION_IN_PROGRESS": ("allow", "IMPLEMENTATION_IN_PROGRESS"),
        "AWAITING_CODE_REVIEW": ("block", "AWAITING_CODE_REVIEW: frozen pending review"),
        "CODE_REVIEW_REJECTED": (
            "allow",
            "CODE_REVIEW_REJECTED: edits permitted to fix review feedback",
        ),
        "READY_TO_MERGE": ("block", "READY_TO_MERGE: ready for merge, no further edits"),
        "MERGED": ("block", "MERGED: L1 update pending"),
        "ARCHIVED": ("block", "ARCHIVED: terminal state"),
        "ABANDONED": ("block", "ABANDONED: terminal state"),
    }

    def __init__(
        self,
        *,
        socket_path: Path,
        state_path: Path,
        events_path: Path,
        max_parallelism: int = 4,
    ) -> None:
        # §3.6 #8: if requested socket_path exceeds UDS sun_path limit, fall back
        # to $TMPDIR/super-harness-<sha256(workspace)>.sock. Clients use the same
        # algorithm to discover the fallback path (supervisor implementation in
        # Task 4.4).
        if len(str(socket_path).encode("utf-8")) > _UDS_PATH_MAX:
            workspace_root = socket_path.parent.parent  # strip /.harness/daemon.sock
            workspace_hash = hashlib.sha256(
                str(workspace_root.resolve()).encode("utf-8")
            ).hexdigest()[:16]
            fallback = (
                Path(os.environ.get("TMPDIR", "/tmp"))
                / f"super-harness-{workspace_hash}.sock"
            )
            _log.warning(
                "socket path %s exceeds UDS limit (%d bytes); falling back to %s",
                socket_path,
                _UDS_PATH_MAX,
                fallback,
            )
            socket_path = fallback

        self.socket_path: Path = socket_path
        self.state_path: Path = state_path
        self.events_path: Path = events_path
        self.max_parallelism: int = max_parallelism
        self._hot_state: HotState = HotState(state_path)
        self._sock: socket.socket | None = None
        self._stop: threading.Event = threading.Event()

    def serve_forever(self) -> None:
        """Bind UDS socket + accept loop until `shutdown()`."""
        # §3.9 #9: fork-then-thread invariant — caller (daemonize) must run
        # before any thread is spawned. Here we already past that point; we are
        # only spawning per-conn threads from the main accept thread.
        if self.socket_path.exists():
            self.socket_path.unlink()
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)  # AC-9
        sock.listen(64)
        sock.settimeout(0.5)  # let _stop break the accept loop
        self._sock = sock
        _log.info("daemon listening on %s", self.socket_path)
        try:
            while not self._stop.is_set():
                try:
                    conn, _addr = sock.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                threading.Thread(
                    target=self._serve_conn, args=(conn,), daemon=True
                ).start()
        finally:
            try:
                sock.close()
            except OSError:
                pass
            try:
                self.socket_path.unlink()
            except FileNotFoundError:
                pass

    def shutdown(self) -> None:
        """Signal the accept loop to exit. Idempotent."""
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass

    def _serve_conn(self, conn: socket.socket) -> None:
        """Read newline-delimited requests until EOF; reply per line.

        DoS defense: each `readline()` is capped at `MAX_REQUEST_BYTES + 1`
        bytes. A malicious client streaming a 100MB line without newline
        would otherwise stall this thread for the duration of the full
        read. With the cap, we read at most 1MiB+1 byte before deciding to
        reject. The "+1" is the sentinel that lets us distinguish "line
        ended within cap" from "cap exceeded mid-line" (see UC-8).
        """
        try:
            rf = conn.makefile("rb")
            while True:
                line = rf.readline(MAX_REQUEST_BYTES + 1)
                if not line:
                    break  # EOF
                if len(line) > MAX_REQUEST_BYTES:
                    # Reject without parse attempt (UC-8). Close the
                    # connection — the client is misbehaving and any
                    # remaining bytes on the socket cannot be reliably
                    # framed (we may be mid-line).
                    response = encode_response(GateQueryResponse(
                        id=None,
                        result=None,
                        error={"code": 400,
                               "message": f"request exceeds {MAX_REQUEST_BYTES} bytes"},
                    ))
                    try:
                        conn.sendall(response)
                    except BrokenPipeError:
                        pass
                    break
                self._handle_line(conn, line)
        except BrokenPipeError:
            _log.warning("client closed connection mid-reply")  # UC-9
        except Exception:
            _log.exception("connection handler crashed")
        finally:
            try:
                conn.close()
            except OSError:
                pass

    def _handle_line(self, conn: socket.socket, line: bytes) -> None:
        if not line:
            return
        try:
            req = decode_request(line)
        except ProtocolVersionMismatch as exc:
            self._send(conn, GateQueryResponse(id=None, result=None,
                                               error={"code": 400, "message": str(exc)}))
            return
        except ProtocolError as exc:
            self._send(conn, GateQueryResponse(id=None, result=None,
                                               error={"code": 400, "message": str(exc)}))
            return
        try:
            if req.method == "gate.pre_tool_use":
                resp = self._gate_pre_tool_use(req)
            elif req.method == "ping":
                resp = GateQueryResponse(
                    id=req.id,
                    result={"version": PROTOCOL_VERSION},
                    error=None,
                )
            else:
                resp = GateQueryResponse(
                    id=req.id,
                    result=None,
                    error={"code": 404, "message": f"unknown method: {req.method}"},
                )
        except Exception as exc:
            _log.exception("method dispatch failed for %s", req.method)
            resp = GateQueryResponse(
                id=req.id,
                result=None,
                error={"code": 500, "message": f"internal error: {exc}"},
            )
        self._send(conn, resp)

    def _gate_pre_tool_use(self, req: GateQueryRequest) -> GateQueryResponse:
        params: dict[str, Any] = req.params
        change_id = params.get("change_id")
        if not change_id:
            # UC-7 framing per spec §3.6: silent ALLOW is anti-pattern.
            # AI agents reading daemon.log must see WHY a permissive decision
            # was issued. No change_id supplied = no policy to apply.
            _log.info(
                "gate.pre_tool_use: no change_id; allowing",
                extra={"reason": "no_change_id", "tool": params.get("tool"),
                       "file": params.get("file")},
            )
            return GateQueryResponse(
                id=req.id,
                result={"decision": "allow", "reason": "no active change",
                        "current_state": None},
                error=None,
            )
        record = self._hot_state.get_change(str(change_id))
        if record is None:
            # UC-7 framing per spec §3.6: distinguish "no record found" from
            # "no change_id supplied". HotState resolves to None on both
            # FileNotFoundError and unknown change_id — both worth logging
            # so an AI debugger can correlate with state.yaml contents.
            _log.info(
                "gate.pre_tool_use: no record for change_id; allowing",
                extra={"change_id": change_id, "reason": "no_record",
                       "tool": params.get("tool"), "file": params.get("file")},
            )
            return GateQueryResponse(
                id=req.id,
                result={"decision": "allow", "reason": "no active change",
                        "current_state": None},
                error=None,
            )
        current_state = record.get("current_state", "INTENT_DECLARED")
        decision, reason = self._PRE_TOOL_USE_DECISIONS.get(
            current_state, ("block", f"unknown state: {current_state}")
        )
        return GateQueryResponse(
            id=req.id,
            result={"decision": decision, "reason": reason, "current_state": current_state},
            error=None,
        )

    @staticmethod
    def _send(conn: socket.socket, resp: GateQueryResponse) -> None:
        try:
            conn.sendall(encode_response(resp))
        except BrokenPipeError:
            _log.warning("client closed before reply could be written")  # UC-9
