"""Durable, best-effort local telemetry of pre-tool-use gate BLOCK decisions (Stage 2).

The gate is a pure query (gates never emit events); the hook dispatcher records a
BLOCK out-of-band here. This is OBSERVABILITY — proof the harness kept an agent
inside the lifecycle — NOT a lifecycle event: it never drives state, never gates
anything, and lives OUTSIDE ``events.jsonl`` so the fail-open + fast hot path is
untouched.

Storage: ``.harness/gate-blocks.jsonl``, one JSON object per BLOCK, append-only.
``record_block`` NEVER raises — a failed write must not flip a real BLOCK into an
ALLOW (the Claude shim treats an uncaught hook exception as exit 1 = non-blocking
= fail-open). ``read_blocks`` parses tolerantly (skip malformed), never raises.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from super_harness.core.clock import utc_now_iso
from super_harness.core.paths import gate_blocks_path


@dataclass(frozen=True)
class GateBlockRecord:
    ts: str
    change_id: str
    state: str
    tool: str
    file: str | None
    reason: str
    gate: str


def record_block(
    root: Path,
    *,
    change_id: str,
    state: str,
    tool: str,
    file: str | None,
    reason: str,
    gate: str = "pre-tool-use",
) -> None:
    """Best-effort append ONE BLOCK record. NEVER raises.

    Any failure (unwritable dir, disk full, encoding) is swallowed: recording is
    telemetry and must never change or crash the gate decision.
    """
    try:
        line = json.dumps(
            {
                "ts": utc_now_iso(),
                "change_id": change_id,
                "state": state,
                "tool": tool,
                "file": file,
                "reason": reason,
                "gate": gate,
            },
            ensure_ascii=False,
        )
        with gate_blocks_path(root).open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def read_blocks(path: Path) -> list[GateBlockRecord]:
    """Parse ``.harness/gate-blocks.jsonl`` tolerantly. Missing file -> []. A
    malformed / non-object / required-field-missing line is skipped, never raised
    (mirrors the reducer / value-report tolerance)."""
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[GateBlockRecord] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        ts, change_id, state = obj.get("ts"), obj.get("change_id"), obj.get("state")
        if not (
            isinstance(ts, str) and isinstance(change_id, str) and isinstance(state, str)
        ):
            continue
        file = obj.get("file")
        tool = obj.get("tool")
        reason = obj.get("reason")
        gate = obj.get("gate")
        out.append(
            GateBlockRecord(
                ts=ts,
                change_id=change_id,
                state=state,
                tool=tool if isinstance(tool, str) else "",
                file=file if isinstance(file, str) else None,
                reason=reason if isinstance(reason, str) else "",
                gate=gate if isinstance(gate, str) else "",
            )
        )
    return out
