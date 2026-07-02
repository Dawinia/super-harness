"""super-harness daemon package.

Hosts the click-less PreToolUse hook entry-point (`hook_entry` — the in-process
decision plane) and the optional framework-observer host (`server` +
`framework_observer` — the observation plane). Post-2026-07-03 there is no UDS
server or RPC protocol; the two planes meet only through events.jsonl/state.yaml.
"""
from __future__ import annotations
