from __future__ import annotations

from super_harness.sensors._anchor_policy import anchor_must_pass_for_tier

# --- anchor_must_pass_for_tier (tier-aware) ----------------------------------


def test_anchor_must_pass_for_tier_is_tier_aware() -> None:
    assert anchor_must_pass_for_tier("Micro") is False
    assert anchor_must_pass_for_tier("Normal") is True
    assert anchor_must_pass_for_tier("Large") is True
    assert anchor_must_pass_for_tier(None) is True  # unknown → fail-closed
    assert anchor_must_pass_for_tier("Weird") is True
