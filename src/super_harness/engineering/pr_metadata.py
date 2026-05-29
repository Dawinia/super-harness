"""PR description metadata block — parse half (engineering-integration spec §2.5).

This module is pure-function: no I/O, no subprocess calls, no global state.

Format SSOT: engineering-integration spec §2.5 (required/recommended/optional
keys, ``Key: Value`` colon-space, marker pair) and §2.6 (pull_request_template
placeholder = markers wrapping ONE HTML comment line, no Key: Value fields).

The **write** half (``build_metadata``) lives in Phase 13 (Task 13.1).
"""

from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Marker constants (format SSOT = engineering-integration §2.5)
# ---------------------------------------------------------------------------

METADATA_BEGIN = "<!-- super-harness:metadata -->"
METADATA_END = "<!-- /super-harness:metadata -->"

# §2.5 required keys.  Recommended/optional keys do NOT count toward
# completeness — that check is the caller's responsibility:
#   fields_complete = REQUIRED_METADATA_KEYS <= block.fields.keys()
REQUIRED_METADATA_KEYS: frozenset[str] = frozenset(
    {"Change", "Tier", "Verification", "super-harness version"}
)


# ---------------------------------------------------------------------------
# Return type
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MetadataBlock:
    """Result of parsing zero or more begin/end marker pairs in a PR body.

    Attributes:
        present:     ``True`` iff at least one well-formed (balanced) pair
                     exists AND no structural anomaly (nested begin / dangling
                     end / unclosed begin) was detected.  Any anomaly yields
                     ``present=False`` so callers can report the "no metadata
                     block" blocker rather than crashing or splicing.
        fields:      Merged ``Key: Value`` pairs from all closed blocks,
                     last-wins across duplicate keys within or across blocks.
                     Empty dict when no block is present.
        block_count: Number of cleanly-closed begin/end pairs.  The caller
                     treats ``block_count >= 2`` as an AC-3 violation blocker.
    """

    present: bool
    fields: dict[str, str]
    block_count: int


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def parse_metadata_block(body: str) -> MetadataBlock:
    """Find the marker pair(s) in *body* and parse ``Key: Value`` lines.

    Uses a **line-walking state machine** (NOT a greedy regex) to avoid the
    data-loss bug where a stray marker makes a non-greedy span eat content
    (the exact issue that broke AGENTS.md inject_section in §3.2).

    Algorithm:
    - Walk lines, tracking whether we are ``inside`` a block.
    - BEGIN while already inside  → nested begin  → ``malformed = True``.
    - END while not inside        → dangling end  → ``malformed = True``.
    - BEGIN when outside          → enter block, start fresh field dict.
    - END when inside             → close block, append field dict.
    - Non-marker line inside      → parse as ``Key: Value`` via
      ``line.partition(": ")``.  If sep is empty (no ``": "`` found) the
      line is silently skipped — covers HTML comments, blank lines, and
      ``Key:`` with no space.  Duplicate key → last-wins (deliberate v0.1
      choice; dup is a cosmetic issue, not a security/data concern).
    - After loop, if still inside → unclosed begin → ``malformed = True``.

    Returns:
        ``MetadataBlock`` with ``present = block_count >= 1 and not malformed``.
    """
    inside: bool = False
    malformed: bool = False
    closed_blocks: list[dict[str, str]] = []
    current_fields: dict[str, str] = {}

    for raw_line in body.splitlines():
        line = raw_line.strip()

        if line == METADATA_BEGIN:
            if inside:
                # Nested begin — structural anomaly
                malformed = True
            else:
                inside = True
                current_fields = {}
        elif line == METADATA_END:
            if not inside:
                # Dangling end — structural anomaly
                malformed = True
            else:
                closed_blocks.append(current_fields)
                current_fields = {}
                inside = False
        elif inside:
            # Attempt to parse as a Key: Value field.
            # partition(": ") splits on the FIRST occurrence of ": " only,
            # so values containing colons (e.g. ISO timestamps) are preserved.
            key, sep, val = line.partition(": ")
            if sep == "":
                # No ": " found — not a Key: Value field; skip silently.
                # This covers the §2.6 HTML comment line, blank lines,
                # and "Key:" entries with no space after the colon.
                continue
            current_fields[key.strip()] = val.strip()

    # Unclosed block (BEGIN with no END at EOF)
    if inside:
        malformed = True

    # Merge all closed blocks' fields into one dict (last-wins across blocks).
    merged_fields: dict[str, str] = {}
    for block_fields in closed_blocks:
        merged_fields.update(block_fields)

    block_count = len(closed_blocks)
    present = block_count >= 1 and not malformed

    return MetadataBlock(present=present, fields=merged_fields, block_count=block_count)
