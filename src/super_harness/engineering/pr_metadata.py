"""PR description metadata block — parse half + write half (engineering-integration §2.5).

Parse half (``parse_metadata_block``) is pure-function: no I/O, no global state.
Write half (``build_metadata``) reads events.jsonl to derive field values.
Strict-resolve half (``resolve_slug_from_pr_body_strict``) parses a PR body
and classifies every failure mode into an exit-code intent (via
``PrSlugLookupError``) for CLI callers (``verify --pr`` / ``done --pr`` —
Phase 14 Task 14.3); also pure-function (the gh fetch stays at the CLI
boundary so tests can patch it at each CLI's import site).

Format SSOT: engineering-integration spec §2.5 (required/recommended/optional
keys, ``Key: Value`` colon-space, marker pair) and §2.6 (pull_request_template
placeholder = markers wrapping ONE HTML comment line, no Key: Value fields).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from super_harness.core.slug import SlugError, validate_slug

# Local mirror of two CLI exit codes used in PrSlugLookupError. We DO NOT
# `from super_harness.cli.exit_codes import …` here because that creates a
# circular import at package init: `cli/__init__.py` → `cli/adapter.py` →
# `adapters/__init__.py` → `sensors/__init__.py` (which loads
# `pr_decorator.py`, which imports from this module). The CLI layer sits
# ABOVE engineering; engineering must never reach back into cli. The two
# constants below are the single source of truth's frozen public contract
# (cli-command-surface §2.2); duplication is safe — the values are stable
# v0.1 surface — and the test suite contains regression tests that pin both
# the CLI constants and the failure exit codes here.
_EXIT_VALIDATION = 2
_EXIT_EXTERNAL_TOOL = 4

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


# ---------------------------------------------------------------------------
# Write half — build_metadata (Phase 13, Task 13.1)
# Introduces file I/O; all reads are confined inside this function and its
# private helpers below.  The parse half above remains I/O-free.
# ---------------------------------------------------------------------------


def _iter_events(events_file: Path) -> Iterator[dict[str, Any]]:
    """Yield parsed JSON objects from events.jsonl line-by-line.

    Tolerant: missing file or unparseable lines are silently skipped so
    ``build_metadata`` can always return a valid block.
    """
    if not events_file.exists():
        return
    for raw in events_file.read_text().splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            yield obj


def _derive_tier(events_file: Path, change_id: str) -> str:
    """Derive Tier from the latest plan_ready event's tier_hint, or 'unknown'."""
    tier: str | None = None
    for ev in _iter_events(events_file):
        if ev.get("change_id") != change_id:
            continue
        if ev.get("type") == "plan_ready":
            payload = ev.get("payload") or {}
            if "tier_hint" in payload:
                tier = str(payload["tier_hint"])
    return tier if tier is not None else "unknown"


def _derive_affected_anchors(events_file: Path, change_id: str) -> list[str]:
    """Return affected_anchors from the latest plan_ready event, or empty list."""
    anchors: list[str] = []
    for ev in _iter_events(events_file):
        if ev.get("change_id") != change_id:
            continue
        if ev.get("type") == "plan_ready":
            payload = ev.get("payload") or {}
            raw = payload.get("affected_anchors")
            if isinstance(raw, list):
                anchors = [str(a) for a in raw]
    return anchors


def _derive_verification(events_file: Path, change_id: str) -> str:
    """Return the Verification token from the latest verification_* event.

    Token set: 'passed' / 'failed' / 'skipped' / 'pending' (no event).
    'skipped' is a verification_passed where payload.skipped is truthy
    (emitted by ``done --skip-verify``).
    """
    token: str | None = None
    for ev in _iter_events(events_file):
        if ev.get("change_id") != change_id:
            continue
        etype = ev.get("type")
        if etype == "verification_failed":
            token = "failed"
        elif etype == "verification_passed":
            payload = ev.get("payload") or {}
            token = "skipped" if payload.get("skipped") else "passed"
    return token if token is not None else "pending"


def _derive_implementation_started(
    events_file: Path, change_id: str
) -> tuple[str | None, str | None]:
    """Return (first_commit, timestamp) from the latest implementation_started event.

    Either or both may be None when absent.
    """
    first_commit: str | None = None
    impl_ts: str | None = None
    for ev in _iter_events(events_file):
        if ev.get("change_id") != change_id:
            continue
        if ev.get("type") == "implementation_started":
            impl_ts = ev.get("timestamp")
            payload = ev.get("payload") or {}
            first_commit = payload.get("first_commit")
    return first_commit, impl_ts


def build_metadata(change_id: str, root: Path) -> str:
    """Build the §2.5 metadata block string for *change_id* in the workspace at *root*.

    Reads ``.harness/events.jsonl`` to derive field values.  Missing file or
    unreadable events are treated as no events → Tier=unknown, Verification=pending.

    Field order matches §2.5 example:
        Change → Tier → Affected anchors (if non-empty) → Verification
        → First commit (if present) → Implementation started (if present)
        → super-harness version

    Plan, Spec, and Verification details are always omitted in v0.1 (no
    provenance source exists for them yet).

    Returns:
        A string starting with ``METADATA_BEGIN`` and ending with ``METADATA_END``
        that round-trips cleanly through ``parse_metadata_block``.
    """
    from super_harness.core.paths import events_path
    from super_harness.version import __version__

    ep = events_path(root)

    tier = _derive_tier(ep, change_id)
    anchors = _derive_affected_anchors(ep, change_id)
    verification = _derive_verification(ep, change_id)
    first_commit, impl_ts = _derive_implementation_started(ep, change_id)

    lines: list[str] = [METADATA_BEGIN]
    lines.append(f"Change: {change_id}")
    lines.append(f"Tier: {tier}")
    # Plan omitted (v0.1 — no provenance source)
    if anchors:
        lines.append(f"Affected anchors: {', '.join(anchors)}")
    lines.append(f"Verification: {verification}")
    # Verification details omitted (v0.1 — no stable archive path resolution)
    # Spec omitted (v0.1 — no provenance source)
    if first_commit:
        lines.append(f"First commit: {first_commit}")
    if impl_ts:
        lines.append(f"Implementation started: {impl_ts}")
    lines.append(f"super-harness version: v{__version__}")
    lines.append(METADATA_END)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Strict-resolve half — resolve_slug_from_pr_strict (Phase 14 Task 14.3)
#
# Used by `verify --pr` / `done --pr` to resolve a slug from a PR's metadata
# block. Unlike Phase 12's `resolve_change_from_pr` (cli/pr.py) which collapses
# every failure to ``None``, this helper classifies failure modes into a
# typed exception carrying the intended exit code so the CLI can emit precise
# error verdicts without re-deriving the classification at each call site.
#
# IMPORTANT divergence from `pr validate` (cli-command-surface spec, locked
# in spec reviewer round 1): `pr validate` treats "missing block" as a
# validation blocker (exit 2 EXIT_VALIDATION). `verify --pr` / `done --pr`
# treat the same observable state as a precondition failure (exit 4
# EXIT_EXTERNAL_TOOL) because the block is a slug-lookup PRECONDITION here,
# not the verdict subject. Do NOT align them.
#
# Exit-code matrix for this helper (mirrored in CLI tests). gh.GhError →
# exit 4 is the CALLER's job — the helper is a pure function over the body
# string so tests can patch the gh wrapper at the CLI import site (Phase 12
# pattern, parallel with `cli/pr.py`).
#
#   No metadata block at all                    → 4 EXIT_EXTERNAL_TOOL
#   Malformed metadata (unbalanced markers)     → 2 EXIT_VALIDATION
#   ≥2 metadata blocks (AC-3 violation)         → 2 EXIT_VALIDATION
#   Block present, missing Change field         → 4 EXIT_EXTERNAL_TOOL
#   Block present, Change present, bad slug     → 2 EXIT_VALIDATION  (A6 gate)
#   Block present, Change present, valid slug   → return slug
# ---------------------------------------------------------------------------


class PrSlugLookupError(Exception):
    """Resolution failure with classified exit-code intent + actionable hint.

    Carries the (exit_code, message, hint) triple the CLI needs to render a
    ``format_error`` to stderr + ``sys.exit(exit_code)``. ``exit_code`` is one
    of ``EXIT_VALIDATION`` (2) or ``EXIT_EXTERNAL_TOOL`` (4) per the matrix
    above — never any other code.
    """

    def __init__(self, *, exit_code: int, message: str, hint: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.message = message
        self.hint = hint


def resolve_slug_from_pr_body_strict(body: str, *, pr_number: int) -> str:
    """Parse a PR body, classify failure modes, and return a validated slug.

    Pure function over the body string — fetching the body is the CALLER's
    job (the CLI keeps the gh boundary visible at its own import site so
    tests can patch ``cli.verify.gh.view_pr`` / ``cli.done.gh.view_pr``
    directly, the way Phase 12 tests patch ``cli.pr.gh.view_pr``).

    See the module-level matrix above for the 5 non-fetch failure modes this
    raises ``PrSlugLookupError`` on. The A6 slug-format gate
    (``validate_slug``) applies unconditionally to the resolved Change field —
    PR bodies are attacker-influenceable, so a tampered block (e.g.
    ``Change: feature/foo``) must not slip past.

    Parameters
    ----------
    body:
        The PR body text. Pass ``""`` for a null/empty PR body.
    pr_number:
        The PR number, used only for human-readable error messages so the
        CLI's stderr line names the PR.

    Returns
    -------
    str
        The resolved + validated change slug.

    Raises
    ------
    PrSlugLookupError
        See the exit-code matrix above. The exception always carries an
        actionable hint pointing at the operator's next step.
    """
    # Parse + classify the four block-shape outcomes.
    #
    # NOTE on the malformed-vs-missing split:
    # ``parse_metadata_block`` returns ``present=False`` for both "no markers
    # at all" AND "unbalanced markers" — and when the imbalance is an
    # unclosed BEGIN (no END at EOF) or a dangling END alone, ``block_count``
    # is 0, so we can't differentiate via that field. We add a direct marker
    # string check: if ANY marker appears in the body but ``present`` is
    # False, we know the operator tried to write a block and got the syntax
    # wrong → malformed (exit 2). Otherwise "no block at all" (exit 4).
    block = parse_metadata_block(body)
    has_any_marker = METADATA_BEGIN in body or METADATA_END in body

    if not block.present and not has_any_marker:
        # No markers anywhere — the PR-decorator never ran (or its output
        # was stripped). EXIT_EXTERNAL_TOOL: this is a precondition fail (a
        # PR decorator on the CI side was supposed to inject this), NOT a
        # verdict on whether the block is well-formed. Divergence from
        # `pr validate` is intentional — see module docstring.
        raise PrSlugLookupError(
            exit_code=_EXIT_EXTERNAL_TOOL,
            message=f"PR #{pr_number} has no super-harness metadata block",
            hint=(
                f"Inject the metadata block by running "
                f"`super-harness pr emit-opened --pr {pr_number} "
                f"--change <slug>` first."
            ),
        )

    if not block.present:
        # Markers exist but the block is not well-formed (nested begin /
        # dangling end / unclosed begin). EXIT_VALIDATION: this is a
        # structural defect in the PR body, not a fetch / decoration issue.
        raise PrSlugLookupError(
            exit_code=_EXIT_VALIDATION,
            message=(
                f"PR #{pr_number} has a malformed super-harness metadata block "
                "(unbalanced markers)"
            ),
            hint="Manually fix or remove the broken block, then re-run.",
        )

    if block.block_count >= 2:
        # AC-3 violation — engineering-integration spec §AC-3 requires
        # exactly one block per PR body.
        raise PrSlugLookupError(
            exit_code=_EXIT_VALIDATION,
            message=(
                f"PR #{pr_number} has {block.block_count} super-harness metadata "
                "blocks (AC-3: exactly one expected)"
            ),
            hint="Remove the duplicate block(s).",
        )

    # 3. Block is well-formed + single — extract Change field.
    slug = block.fields.get("Change")
    if slug is None:
        # The PR-decorator was supposed to set Change but the field is
        # absent (a decorated-but-broken block). EXIT_EXTERNAL_TOOL: same
        # rationale as "no block" — the decorator's output is the
        # precondition, and it failed to satisfy the required key.
        raise PrSlugLookupError(
            exit_code=_EXIT_EXTERNAL_TOOL,
            message=(
                f"PR #{pr_number}'s super-harness metadata block has no "
                "Change field"
            ),
            hint=(
                "The PR-decorator should set Change. Re-run "
                f"`super-harness pr emit-opened --pr {pr_number} "
                f"--change <slug>`."
            ),
        )

    # 4. A6 slug-format gate — applies regardless of source. The PR body is
    #    attacker-influenceable, so a Change field like `feature/foo` must
    #    not slip through as a valid slug.
    try:
        validate_slug(slug)
    except SlugError as e:
        raise PrSlugLookupError(
            exit_code=_EXIT_VALIDATION,
            message=(
                f"resolved slug {slug!r} from PR #{pr_number} is invalid: {e}"
            ),
            hint=(
                "The Change field must be a kebab-case slug (3-80 chars, "
                "lower-case alphanumeric + hyphens; e.g. `add-foo`)."
            ),
        ) from e

    return slug
