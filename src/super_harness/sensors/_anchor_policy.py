"""Shared anchor-gate tier policy (SSOT for the anchor-presence must_pass rule).

Both the Phase 8 verification baseline (`anchor-sentinel-presence-final`) and the
Phase 11 standalone `anchor-sentinel-presence` sensor decide whether a missing
anchor is blocking vs advisory using the SAME tier rule. Kept here — stdlib-only,
never importing the sensors package — so both share one rule with no cycle.
"""
from __future__ import annotations

MICRO_TIER = "Micro"


def anchor_must_pass_for_tier(tier: str | None) -> bool:
    """Micro -> advisory (False, warn only). Normal/Large/unknown/None -> must_pass
    (True). Defaulting an unknown tier to must_pass is fail-closed."""
    return tier != MICRO_TIER
