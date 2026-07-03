"""Shared helpers for the daemon integration tests.

Post-demotion (design 2026-07-03) the resident process is a pure observer host —
no UDS socket, no gate dispatch — so the socket-liveness / daemon-killer helpers
and the hook-query-timeout fixture that the retired UDS server needed are gone.
What remains is a generic state.yaml writer used to seed lifecycle fixtures.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def write_state(workspace: Path, change_id: str, current_state: str) -> None:
    state_path = workspace / ".harness" / "state.yaml"
    # Real reducer shape: `changes` map only, NO top-level active_change_id
    # (the reducer never writes it; "active" is derived = most recently active non-terminal).
    state_path.write_text(
        yaml.safe_dump(
            {
                "changes": {
                    change_id: {
                        "change_id": change_id,
                        "current_state": current_state,
                    }
                },
            }
        )
    )
