"""Tests for engineering/pr_metadata.py — parse half only.

Pure-function module; no I/O, no mocks needed.
Mirrors the style/imports of test_gh.py for consistency.
"""

from __future__ import annotations

import pytest

from super_harness.engineering.pr_metadata import (
    METADATA_BEGIN,
    METADATA_END,
    REQUIRED_METADATA_KEYS,
    MetadataBlock,
    parse_metadata_block,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VERIF_DETAILS = (
    "Verification details: .harness/verification-results/"
    "2026-05-26-add-l1-anchors/2026-05-26T16:00:00Z/summary.json"
)
_FULL_BODY = "\n".join(
    [
        "## Summary",
        "Some change.",
        "",
        METADATA_BEGIN,
        "Change: 2026-05-26-add-l1-anchors",
        "Tier: Normal",
        "Plan: docs/superpowers/plans/2026-05-26-add-l1-anchors.md",
        "Affected anchors: capability-l1-anchor-check, capability-state-reducer",
        "Verification: passed",
        _VERIF_DETAILS,
        "Spec: docs/superpowers/specs/2026-05-26-add-l1-anchors.md",
        "First commit: a3b4c5d",
        "Implementation started: 2026-05-26T14:00:00Z",
        "super-harness version: v0.1.0",
        METADATA_END,
        "",
    ]
)

_ALL_REQUIRED = {
    "Change": "2026-05-26-add-l1-anchors",
    "Tier": "Normal",
    "Verification": "passed",
    "super-harness version": "v0.1.0",
}


# ---------------------------------------------------------------------------
# Constant / dataclass shape
# ---------------------------------------------------------------------------


class TestConstants:
    def test_begin_marker_value(self) -> None:
        assert METADATA_BEGIN == "<!-- super-harness:metadata -->"

    def test_end_marker_value(self) -> None:
        assert METADATA_END == "<!-- /super-harness:metadata -->"

    def test_required_keys_is_frozenset(self) -> None:
        assert isinstance(REQUIRED_METADATA_KEYS, frozenset)

    def test_required_keys_contents(self) -> None:
        assert REQUIRED_METADATA_KEYS == frozenset(
            {"Change", "Tier", "Verification", "super-harness version"}
        )

    def test_metadata_block_is_frozen_dataclass(self) -> None:
        block = MetadataBlock(present=True, fields={"k": "v"}, block_count=1)
        with pytest.raises((AttributeError, TypeError)):
            block.present = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Zero blocks
# ---------------------------------------------------------------------------


class TestZeroBlocks:
    def test_empty_string(self) -> None:
        block = parse_metadata_block("")
        assert block.present is False
        assert block.block_count == 0
        assert block.fields == {}

    def test_no_markers_in_body(self) -> None:
        block = parse_metadata_block("## Summary\nJust some text\n\nNo markers.")
        assert block.present is False
        assert block.block_count == 0
        assert block.fields == {}

    def test_only_whitespace(self) -> None:
        block = parse_metadata_block("   \n\n\t\n")
        assert block.present is False
        assert block.block_count == 0


# ---------------------------------------------------------------------------
# Exactly one complete block — all REQUIRED keys present
# ---------------------------------------------------------------------------


class TestOneCompleteBlock:
    def test_full_body_present(self) -> None:
        block = parse_metadata_block(_FULL_BODY)
        assert block.present is True
        assert block.block_count == 1

    def test_full_body_has_all_required_keys(self) -> None:
        block = parse_metadata_block(_FULL_BODY)
        # All required keys must be present (fields_complete check the caller does)
        assert REQUIRED_METADATA_KEYS <= block.fields.keys()

    def test_full_body_exact_field_values(self) -> None:
        block = parse_metadata_block(_FULL_BODY)
        assert block.fields["Change"] == "2026-05-26-add-l1-anchors"
        assert block.fields["Tier"] == "Normal"
        assert block.fields["Verification"] == "passed"
        assert block.fields["super-harness version"] == "v0.1.0"

    def test_minimal_complete_block(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            "Change: my-change\n"
            "Tier: Normal\n"
            "Verification: passed\n"
            "super-harness version: v0.1.0\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert block.present is True
        assert block.block_count == 1
        assert REQUIRED_METADATA_KEYS <= block.fields.keys()


# ---------------------------------------------------------------------------
# One block — missing a required key
# ---------------------------------------------------------------------------


class TestOneBlockMissingKey:
    def test_missing_tier(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            "Change: my-change\n"
            "Verification: passed\n"
            "super-harness version: v0.1.0\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert block.present is True
        assert block.block_count == 1
        # "Tier" is absent → caller's fields_complete check would be False
        assert "Tier" not in block.fields

    def test_missing_verification_key(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            "Change: my-change\n"
            "Tier: Normal\n"
            "super-harness version: v0.1.0\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert block.present is True
        assert not (REQUIRED_METADATA_KEYS <= block.fields.keys())


# ---------------------------------------------------------------------------
# Two or more clean blocks
# ---------------------------------------------------------------------------


class TestTwoCleanBlocks:
    def test_block_count_two(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            "Change: first\n"
            f"{METADATA_END}\n"
            "\n"
            f"{METADATA_BEGIN}\n"
            "Change: second\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        # Both pairs are clean — block_count=2; present=True (caller decides on ≥2 anomaly)
        assert block.block_count == 2
        assert block.present is True

    def test_fields_last_wins_across_two_blocks(self) -> None:
        """Fields from both blocks are merged; second block's Change wins."""
        body = (
            f"{METADATA_BEGIN}\n"
            "Change: first\n"
            f"{METADATA_END}\n"
            f"{METADATA_BEGIN}\n"
            "Change: second\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert block.fields["Change"] == "second"


# ---------------------------------------------------------------------------
# Unbalanced markers
# ---------------------------------------------------------------------------


class TestUnbalancedMarkers:
    def test_begin_only_no_end(self) -> None:
        body = f"{METADATA_BEGIN}\nChange: my-change\n"
        block = parse_metadata_block(body)
        assert block.present is False
        assert block.block_count == 0

    def test_end_only_no_begin(self) -> None:
        body = f"Change: my-change\n{METADATA_END}\n"
        block = parse_metadata_block(body)
        assert block.present is False
        assert block.block_count == 0

    def test_begin_only_does_not_populate_fields(self) -> None:
        body = f"{METADATA_BEGIN}\nChange: my-change\n"
        block = parse_metadata_block(body)
        # Unclosed block → not captured
        assert block.fields == {}


# ---------------------------------------------------------------------------
# Nested begin (structural anomaly)
# ---------------------------------------------------------------------------


class TestNestedBegin:
    def test_nested_begin_begin_end_present_false(self) -> None:
        """BEGIN, BEGIN, END — the inner BEGIN is a nested anomaly."""
        body = (
            f"{METADATA_BEGIN}\n"
            "Change: outer\n"
            f"{METADATA_BEGIN}\n"  # nested — anomaly
            "Change: inner\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert block.present is False

    def test_nested_begin_block_count(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            f"{METADATA_BEGIN}\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        # malformed → present=False regardless of block_count value
        assert block.present is False


# ---------------------------------------------------------------------------
# Value with colons (timestamp)
# ---------------------------------------------------------------------------


class TestValueWithColons:
    def test_implementation_started_timestamp(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            "Implementation started: 2026-05-26T14:00:00Z\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        # partition(": ") splits on FIRST ": " only
        assert block.fields["Implementation started"] == "2026-05-26T14:00:00Z"

    def test_verification_details_path_with_colons(self) -> None:
        val = ".harness/verification-results/slug/2026-05-26T16:00:00Z/summary.json"
        body = (
            f"{METADATA_BEGIN}\n"
            f"Verification details: {val}\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert block.fields["Verification details"] == val


# ---------------------------------------------------------------------------
# Space-bearing key
# ---------------------------------------------------------------------------


class TestSpaceBearingKey:
    def test_super_harness_version_key(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            "super-harness version: v0.1.0\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert "super-harness version" in block.fields
        assert block.fields["super-harness version"] == "v0.1.0"


# ---------------------------------------------------------------------------
# Duplicate key — last-wins
# ---------------------------------------------------------------------------


class TestDuplicateKey:
    def test_duplicate_change_line_last_wins(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            "Change: first-value\n"
            "Change: second-value\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert block.fields["Change"] == "second-value"

    def test_duplicate_across_two_blocks_last_wins(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            "Change: block-one\n"
            f"{METADATA_END}\n"
            f"{METADATA_BEGIN}\n"
            "Change: block-two\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert block.fields["Change"] == "block-two"


# ---------------------------------------------------------------------------
# Key:Value without space after colon — must be skipped, not an error
# ---------------------------------------------------------------------------


class TestNoSpaceAfterColon:
    def test_key_colon_value_no_space_is_skipped(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            "NoSpace:ShouldBeSkipped\n"
            "Change: real-value\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert "NoSpace" not in block.fields
        assert block.fields["Change"] == "real-value"

    def test_key_colon_no_value_at_all_skipped(self) -> None:
        """'Key:' with nothing after colon — no ': ' separator → skip."""
        body = (
            f"{METADATA_BEGIN}\n"
            "Tier:\n"
            "Change: something\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        # "Tier:" has no ": " — the partition returns sep="" → skip
        assert "Tier" not in block.fields


# ---------------------------------------------------------------------------
# Comma-valued line (multi-value anchors)
# ---------------------------------------------------------------------------


class TestCommaValues:
    def test_affected_anchors_raw_string(self) -> None:
        body = (
            f"{METADATA_BEGIN}\n"
            "Affected anchors: a, b\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        # Splitting is caller's concern — we store the raw string
        assert block.fields["Affected anchors"] == "a, b"

    def test_many_anchors_raw_string(self) -> None:
        val = "capability-l1-anchor-check, capability-state-reducer, capability-events"
        body = (
            f"{METADATA_BEGIN}\n"
            f"Affected anchors: {val}\n"
            f"{METADATA_END}\n"
        )
        block = parse_metadata_block(body)
        assert block.fields["Affected anchors"] == val


# ---------------------------------------------------------------------------
# §2.6 placeholder — markers wrap ONLY the HTML comment line, no Key: Value
# ---------------------------------------------------------------------------


class TestSection26Placeholder:
    _PLACEHOLDER_BODY = (
        "## Summary\n"
        "<!-- describe your change here -->\n"
        "\n"
        "## Test plan\n"
        "<!-- how was this verified -->\n"
        "\n"
        "---\n"
        f"{METADATA_BEGIN}\n"
        "<!-- auto-filled by super-harness PR-decorator sensor; do not edit manually -->\n"
        f"{METADATA_END}\n"
    )

    def test_placeholder_present_true(self) -> None:
        block = parse_metadata_block(self._PLACEHOLDER_BODY)
        assert block.present is True

    def test_placeholder_block_count_one(self) -> None:
        block = parse_metadata_block(self._PLACEHOLDER_BODY)
        assert block.block_count == 1

    def test_placeholder_fields_empty(self) -> None:
        block = parse_metadata_block(self._PLACEHOLDER_BODY)
        assert block.fields == {}

    def test_placeholder_required_keys_missing(self) -> None:
        block = parse_metadata_block(self._PLACEHOLDER_BODY)
        # fields_complete = False — caller would report missing required keys
        assert not (REQUIRED_METADATA_KEYS <= block.fields.keys())
