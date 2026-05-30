"""Tests for engineering/pr_metadata.py — parse half + build_metadata write half.

Parse-half tests are pure-function (no I/O, no mocks).
build_metadata tests use tmp_path + EventWriter to seed events.jsonl.
No gh / network calls anywhere in this file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from super_harness.core.clock import utc_now_iso
from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.engineering.pr_metadata import (
    METADATA_BEGIN,
    METADATA_END,
    REQUIRED_METADATA_KEYS,
    MetadataBlock,
    build_metadata,
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


# ---------------------------------------------------------------------------
# build_metadata helpers
# ---------------------------------------------------------------------------


def _make_ev(
    change_id: str,
    event_type: str,
    payload: dict | None = None,
    timestamp: str | None = None,
) -> Event:
    return Event(
        event_id=new_event_id(),
        type=event_type,
        change_id=change_id,
        timestamp=timestamp or utc_now_iso(),
        actor=Actor(type="human", identifier="test"),
        framework="plain",
        payload=payload or {},
    )


def _seed_lifecycle(tmp_path: Path, change_id: str) -> Path:
    """Seed a minimal valid lifecycle up to IMPLEMENTATION_IN_PROGRESS.

    Returns the repo root (tmp_path), which has .harness/events.jsonl inside.
    Uses skip_validation=True for speed; we just need events on disk.
    """
    ep = events_path(tmp_path)
    w = EventWriter(ep)
    for etype in ("intent_declared", "plan_ready", "plan_approved", "implementation_started"):
        w.emit(_make_ev(change_id, etype), skip_validation=True)
    return tmp_path


# ---------------------------------------------------------------------------
# build_metadata — required-keys completeness + round-trip
# ---------------------------------------------------------------------------


class TestBuildMetadataRequiredKeys:
    def test_required_keys_always_present(self, tmp_path: Path) -> None:
        """All 4 required keys must appear in the parsed block from build_metadata."""
        root = tmp_path
        result = build_metadata("my-change", root)
        block = parse_metadata_block(result)
        assert REQUIRED_METADATA_KEYS <= block.fields.keys()

    def test_round_trip_change_key(self, tmp_path: Path) -> None:
        change_id = "2026-05-30-round-trip"
        result = build_metadata(change_id, tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Change"] == change_id

    def test_round_trip_version_key(self, tmp_path: Path) -> None:
        result = build_metadata("some-change", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["super-harness version"] == "v0.1.0"

    def test_round_trip_tier_key_present(self, tmp_path: Path) -> None:
        result = build_metadata("some-change", tmp_path)
        block = parse_metadata_block(result)
        assert "Tier" in block.fields

    def test_round_trip_verification_key_present(self, tmp_path: Path) -> None:
        result = build_metadata("some-change", tmp_path)
        block = parse_metadata_block(result)
        assert "Verification" in block.fields


# ---------------------------------------------------------------------------
# build_metadata — Tier field
# ---------------------------------------------------------------------------


class TestBuildMetadataTier:
    def test_tier_unknown_when_no_events(self, tmp_path: Path) -> None:
        """Fresh repo with no events.jsonl → Tier: unknown."""
        result = build_metadata("fresh-change", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Tier"] == "unknown"

    def test_tier_unknown_when_change_not_in_events(self, tmp_path: Path) -> None:
        """events.jsonl exists but has no events for this change → Tier: unknown."""
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("other-change", "intent_declared"), skip_validation=True)
        result = build_metadata("my-change", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Tier"] == "unknown"

    def test_tier_value_from_plan_ready_tier_hint(self, tmp_path: Path) -> None:
        """plan_ready with tier_hint → Tier rendered with that value."""
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("my-change", "intent_declared"), skip_validation=True)
        w.emit(
            _make_ev("my-change", "plan_ready", payload={"tier_hint": "Normal"}),
            skip_validation=True,
        )
        result = build_metadata("my-change", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Tier"] == "Normal"

    def test_tier_critical_value(self, tmp_path: Path) -> None:
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        w.emit(
            _make_ev("c1", "plan_ready", payload={"tier_hint": "Critical"}),
            skip_validation=True,
        )
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Tier"] == "Critical"


# ---------------------------------------------------------------------------
# build_metadata — Verification field
# ---------------------------------------------------------------------------


class TestBuildMetadataVerification:
    def test_verification_pending_when_no_events(self, tmp_path: Path) -> None:
        """No events at all → Verification: pending."""
        result = build_metadata("no-events-change", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Verification"] == "pending"

    def test_verification_pending_when_no_verify_event(self, tmp_path: Path) -> None:
        """Events exist but no verification event → Verification: pending."""
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Verification"] == "pending"

    def test_verification_passed(self, tmp_path: Path) -> None:
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_ready"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_approved"), skip_validation=True)
        w.emit(_make_ev("c1", "implementation_started"), skip_validation=True)
        w.emit(
            _make_ev("c1", "verification_passed", payload={}),
            skip_validation=True,
        )
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Verification"] == "passed"

    def test_verification_failed(self, tmp_path: Path) -> None:
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_ready"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_approved"), skip_validation=True)
        w.emit(_make_ev("c1", "implementation_started"), skip_validation=True)
        w.emit(
            _make_ev("c1", "verification_failed", payload={}),
            skip_validation=True,
        )
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Verification"] == "failed"

    def test_verification_skipped(self, tmp_path: Path) -> None:
        """verification_passed with payload.skipped=True → Verification: skipped."""
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_ready"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_approved"), skip_validation=True)
        w.emit(_make_ev("c1", "implementation_started"), skip_validation=True)
        w.emit(
            _make_ev(
                "c1", "verification_passed", payload={"skipped": True, "reason": "--skip-verify"}
            ),
            skip_validation=True,
        )
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Verification"] == "skipped"

    def test_verification_latest_event_wins(self, tmp_path: Path) -> None:
        """If verification_failed then verification_passed: latest wins → passed."""
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_ready"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_approved"), skip_validation=True)
        w.emit(_make_ev("c1", "implementation_started"), skip_validation=True)
        w.emit(_make_ev("c1", "verification_failed"), skip_validation=True)
        w.emit(_make_ev("c1", "verification_passed", payload={}), skip_validation=True)
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Verification"] == "passed"


# ---------------------------------------------------------------------------
# build_metadata — Affected anchors
# ---------------------------------------------------------------------------


class TestBuildMetadataAffectedAnchors:
    def test_affected_anchors_omitted_when_empty(self, tmp_path: Path) -> None:
        """No affected_anchors in plan_ready → key absent from output."""
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_ready", payload={}), skip_validation=True)
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert "Affected anchors" not in block.fields

    def test_affected_anchors_omitted_when_no_plan_ready(self, tmp_path: Path) -> None:
        """No plan_ready event → key absent."""
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert "Affected anchors" not in block.fields

    def test_affected_anchors_comma_joined_when_non_empty(self, tmp_path: Path) -> None:
        """affected_anchors list → comma+space joined, round-trip recovers same value."""
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        w.emit(
            _make_ev(
                "c1",
                "plan_ready",
                payload={"affected_anchors": ["cap-foo", "cap-bar"]},
            ),
            skip_validation=True,
        )
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Affected anchors"] == "cap-foo, cap-bar"

    def test_affected_anchors_single_item(self, tmp_path: Path) -> None:
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        w.emit(
            _make_ev("c1", "plan_ready", payload={"affected_anchors": ["cap-only"]}),
            skip_validation=True,
        )
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Affected anchors"] == "cap-only"


# ---------------------------------------------------------------------------
# build_metadata — Plan / Spec / Verification details always omitted (v0.1)
# ---------------------------------------------------------------------------


class TestBuildMetadataOmittedV0Keys:
    def test_plan_always_omitted(self, tmp_path: Path) -> None:
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert "Plan" not in block.fields

    def test_spec_always_omitted(self, tmp_path: Path) -> None:
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert "Spec" not in block.fields

    def test_verification_details_always_omitted(self, tmp_path: Path) -> None:
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert "Verification details" not in block.fields


# ---------------------------------------------------------------------------
# build_metadata — First commit + Implementation started
# ---------------------------------------------------------------------------


class TestBuildMetadataImplementationStarted:
    def test_first_commit_and_impl_started_rendered(self, tmp_path: Path) -> None:
        """implementation_started event with first_commit + timestamp → both rendered."""
        ts = "2026-05-30T12:00:00Z"
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_ready"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_approved"), skip_validation=True)
        w.emit(
            _make_ev(
                "c1",
                "implementation_started",
                payload={"first_commit": "abc1234"},
                timestamp=ts,
            ),
            skip_validation=True,
        )
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["First commit"] == "abc1234"
        assert block.fields["Implementation started"] == ts

    def test_first_commit_omitted_when_no_implementation_started(self, tmp_path: Path) -> None:
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert "First commit" not in block.fields

    def test_impl_started_omitted_when_no_implementation_started(self, tmp_path: Path) -> None:
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert "Implementation started" not in block.fields

    def test_first_commit_omitted_when_payload_missing_key(self, tmp_path: Path) -> None:
        """implementation_started event without first_commit in payload → key absent."""
        ep = events_path(tmp_path)
        w = EventWriter(ep)
        w.emit(_make_ev("c1", "intent_declared"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_ready"), skip_validation=True)
        w.emit(_make_ev("c1", "plan_approved"), skip_validation=True)
        w.emit(
            _make_ev("c1", "implementation_started", payload={}),
            skip_validation=True,
        )
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert "First commit" not in block.fields
        # Implementation started IS rendered (the event exists), just no first_commit key
        assert "Implementation started" in block.fields


# ---------------------------------------------------------------------------
# build_metadata — events.jsonl absent
# ---------------------------------------------------------------------------


class TestBuildMetadataNoEventsFile:
    def test_valid_block_when_no_events_file(self, tmp_path: Path) -> None:
        """No .harness/events.jsonl → build_metadata must not crash."""
        result = build_metadata("ghost-change", tmp_path)
        block = parse_metadata_block(result)
        assert block.present is True
        assert REQUIRED_METADATA_KEYS <= block.fields.keys()

    def test_tier_unknown_when_no_events_file(self, tmp_path: Path) -> None:
        result = build_metadata("ghost-change", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Tier"] == "unknown"

    def test_verification_pending_when_no_events_file(self, tmp_path: Path) -> None:
        result = build_metadata("ghost-change", tmp_path)
        block = parse_metadata_block(result)
        assert block.fields["Verification"] == "pending"


# ---------------------------------------------------------------------------
# build_metadata — Marker presence + uniqueness
# ---------------------------------------------------------------------------


class TestBuildMetadataMarkers:
    def test_metadata_begin_present_exactly_once(self, tmp_path: Path) -> None:
        result = build_metadata("c1", tmp_path)
        assert result.count(METADATA_BEGIN) == 1

    def test_metadata_end_present_exactly_once(self, tmp_path: Path) -> None:
        result = build_metadata("c1", tmp_path)
        assert result.count(METADATA_END) == 1

    def test_parse_block_count_exactly_one(self, tmp_path: Path) -> None:
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.block_count == 1

    def test_block_present_true(self, tmp_path: Path) -> None:
        result = build_metadata("c1", tmp_path)
        block = parse_metadata_block(result)
        assert block.present is True
