# tests/unit/engineering/test_review_budget.py
"""Round-budget evidence, replayed against the recorded review corpus.

The corpus (`tests/fixtures/review-corpus/`) is a redacted real event stream, not a
fabricated fixture — see its README for the redaction contract and for what it
cannot test. Tests here identify changes by their **curve**, never by a hardcoded
`corpus-NN` id: the id is an artefact of first-appearance ordering and would move
if the export ever changed.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

CORPUS = (
    Path(__file__).resolve().parents[2] / "fixtures" / "review-corpus" / "corpus.jsonl"
)

# The eight per-change `blocker+major` sequences the design document tabulates
# (docs/plans/2026-08-06-plan-review-round-budget-design.md). Order-independent:
# a curve is the identity, an id is not.
DESIGN_CURVES: tuple[tuple[int, ...], ...] = (
    (3, 2, 0),
    (2, 1, 0, 0),
    (3, 2, 2, 1, 0, 0),
    (3, 1, 0, 1, 1, 0),
    (2, 1, 2, 0, 1, 1, 1, 0, 0),
    (3, 2, 1, 0, 0, 1, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0),
    (3, 4, 2, 2, 2, 1, 2, 2, 1, 1, 3, 1, 0),
    (3, 2, 3, 2, 2, 1, 2, 3, 1, 1),
)

# The design's pathological case: flat over ten rounds, cut off without converging.
FLAT_CURVE = (3, 2, 3, 2, 2, 1, 2, 3, 1, 1)


def _events() -> list[dict]:
    with CORPUS.open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _plan_curves() -> dict[str, tuple[int, ...]]:
    """`blocker+major` per imported plan round, per change, in round order."""
    curves: dict[str, list[int]] = defaultdict(list)
    for event in _events():
        payload = event.get("payload") or {}
        if payload.get("reviewer") != "plan-reviewer":
            continue
        if event.get("type") != "review_result_imported":
            continue
        verdict = payload.get("verdict") or {}
        findings = verdict.get("findings") or []
        curves[event["change_id"]].append(
            sum(1 for f in findings if f.get("severity") in {"blocker", "major"})
        )
    return {change: tuple(curve) for change, curve in curves.items()}


def _started_automatic_plan_rounds() -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for event in _events():
        payload = event.get("payload") or {}
        if payload.get("reviewer") != "plan-reviewer":
            continue
        if event.get("type") != "review_round_started":
            continue
        if payload.get("automatic"):
            counts[event["change_id"]] += 1
    return dict(counts)


def test_corpus_reproduces_known_curves() -> None:
    """Every curve the design tabulates is present, exactly once."""
    present = list(_plan_curves().values())
    for curve in DESIGN_CURVES:
        assert present.count(curve) == 1, f"design curve {curve} not reproduced exactly once"


def test_corpus_started_rounds_exceed_imported_where_runs_failed() -> None:
    """A round can start, cost money, and import nothing — the counts must differ.

    This is the distinction the budget depends on: it counts started rounds, while
    the finding curves can only come from imported ones.
    """
    started = _started_automatic_plan_rounds()
    imported = {change: len(curve) for change, curve in _plan_curves().items()}
    assert all(started[c] >= imported.get(c, 0) for c in started)
    assert sum(started.values()) > sum(imported.values())
    flat = next(c for c, curve in _plan_curves().items() if curve == FLAT_CURVE)
    assert started[flat] > len(FLAT_CURVE)


def test_corpus_carries_no_source_repository_residue() -> None:
    """The redaction is verified on the committed bytes, not on the exporter."""
    text = CORPUS.read_text(encoding="utf-8")
    forbidden = re.compile(
        r"pack|rebuttal|ranking|consensus|revision|session-state|interaction"
        r"|card-select|uuid|lan-origins|stage\d|pantheon|docs/|src/|\.ts\b|\.mjs\b"
        r"|[0-9a-f]{40}",
        re.IGNORECASE,
    )
    assert forbidden.search(text) is None
    emails = set(re.findall(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+", text))
    assert emails <= {"corpus@example.invalid"}


def test_corpus_finding_ids_are_rewritten_not_dropped() -> None:
    """Cut 2 is per-id disposal; a corpus without finding ids cannot replay it."""
    seen = 0
    for event in _events():
        verdict = (event.get("payload") or {}).get("verdict") or {}
        for finding in verdict.get("findings") or []:
            assert set(finding) == {"id", "severity"}, finding
            assert re.fullmatch(r"c\d{2}/f-\d{2}", finding["id"]), finding["id"]
            seen += 1
    assert seen > 0
