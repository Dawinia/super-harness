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


def _authorizations_at(budget: int) -> dict[str, int]:
    """Prompts each corpus change would need, by the rule the counter implements."""
    started = _started_automatic_plan_rounds()
    return {change: max(0, n - budget) for change, n in started.items()}


def test_corpus_replays_the_budget_rule() -> None:
    """The criterion is the RULE, and the corpus supplies the numbers. Nothing here is
    transcribed from the plan or from the design — the design's tables are measured in
    imported rounds while the counter counts started ones, so copying them across would
    assert against the wrong quantity."""
    from super_harness.engineering.review_runs import count_automatic_rounds

    prompts = _authorizations_at(6)
    started = _started_automatic_plan_rounds()
    for change, n in started.items():
        assert prompts[change] == max(0, n - 6)

    # The rule and the shipped counter agree on the same event stream.
    from super_harness.core.events import Actor, Event

    for change in started:
        events = [
            Event(
                event_id=e["event_id"], type=e["type"], change_id=e["change_id"],
                timestamp=e["timestamp"],
                actor=Actor(type=e["actor"]["type"], identifier=e["actor"]["identifier"]),
                framework=e["framework"], payload=e["payload"],
            )
            for e in _events() if e["change_id"] == change
        ]
        assert count_automatic_rounds(events, "plan-reviewer") == started[change]


def test_corpus_budget_of_six_leaves_the_quiet_changes_alone() -> None:
    """Changes at or under the budget are never interrupted — the property the default
    was chosen for. Changes above it are, including ones the design calls converged;
    that is this cut shipping the noisy half of the design's pair on purpose."""
    started = _started_automatic_plan_rounds()
    prompts = _authorizations_at(6)

    assert all(prompts[c] == 0 for c, n in started.items() if n <= 6)
    assert all(prompts[c] > 0 for c, n in started.items() if n > 6)
    assert sum(prompts.values()) > 0


def test_corpus_a_failed_round_still_consumes_budget() -> None:
    """A round that imported nothing cost real money and produced no findings — the
    worst round to hide from a brake that bounds spend. At least one corpus change is
    over budget only because of rounds that never imported."""
    started = _started_automatic_plan_rounds()
    imported = {change: len(curve) for change, curve in _plan_curves().items()}
    over_only_via_failures = [
        change
        for change, n in started.items()
        if n > 6 and imported.get(change, 0) <= 6
    ]
    assert over_only_via_failures


# --- Task 4: the evidence derivation -------------------------------------------------

def _corpus_events_for(change: str):
    from super_harness.core.events import Actor, Event

    return [
        Event(
            event_id=e["event_id"], type=e["type"], change_id=e["change_id"],
            timestamp=e["timestamp"],
            actor=Actor(type=e["actor"]["type"], identifier=e["actor"]["identifier"]),
            framework=e["framework"], payload=e["payload"],
        )
        for e in _events() if e["change_id"] == change
    ]


def _flat_change() -> str:
    return next(c for c, curve in _plan_curves().items() if curve == FLAT_CURVE)


def test_evidence_round_number_is_the_budgets_own_counter() -> None:
    """The block and the counter must never disagree about which round this is."""
    from super_harness.engineering.review_budget import derive_round_budget_evidence
    from super_harness.engineering.review_runs import count_automatic_rounds

    for change, started in _started_automatic_plan_rounds().items():
        events = _corpus_events_for(change)
        evidence = derive_round_budget_evidence(events, reviewer="plan-reviewer")
        assert evidence.started_rounds == started
        assert evidence.started_rounds == count_automatic_rounds(events, "plan-reviewer")


def test_evidence_reports_both_round_counts_rather_than_reconciling_them() -> None:
    """A round counted by the budget but absent from the curve is the case the human
    needs to see, so the evidence states both numbers side by side."""
    from super_harness.engineering.review_budget import derive_round_budget_evidence

    change = next(
        c for c, n in _started_automatic_plan_rounds().items()
        if n > len(_plan_curves().get(c, ()))
    )
    evidence = derive_round_budget_evidence(
        _corpus_events_for(change), reviewer="plan-reviewer"
    )
    assert evidence.started_rounds > evidence.imported_rounds
    assert evidence.imported_rounds == len(evidence.blocker_major_curve)


def test_evidence_curves_are_in_round_order() -> None:
    from super_harness.engineering.review_budget import derive_round_budget_evidence

    for change, curve in _plan_curves().items():
        evidence = derive_round_budget_evidence(
            _corpus_events_for(change), reviewer="plan-reviewer"
        )
        assert evidence.blocker_major_curve == curve
        assert len(evidence.total_findings_curve) == len(curve)
        assert all(
            total >= bm
            for total, bm in zip(evidence.total_findings_curve, curve, strict=True)
        )


def test_evidence_counts_cumulative_tokens_including_cache() -> None:
    """Reuses the shared token counter — no second implementation."""
    from super_harness.engineering.review_budget import derive_round_budget_evidence
    from super_harness.engineering.value_report import usage_tokens

    change = _flat_change()
    expected = 0
    for e in _events():
        if e["change_id"] != change or e["type"] != "review_result_imported":
            continue
        if (e["payload"] or {}).get("reviewer") != "plan-reviewer":
            continue
        t = usage_tokens(((e["payload"] or {}).get("receipt") or {}).get("usage"))
        if t is not None:
            expected += t
    evidence = derive_round_budget_evidence(
        _corpus_events_for(change), reviewer="plan-reviewer"
    )
    assert evidence.tokens == expected
    assert evidence.tokens and evidence.tokens > 0


def test_evidence_counts_consecutive_missing_source_rounds() -> None:
    """One source dead from round 1 and eleven single-source rounds passed unremarked;
    the streak is the number that makes that visible."""
    from super_harness.engineering.review_budget import derive_round_budget_evidence

    evidence = derive_round_budget_evidence(
        _corpus_events_for(_flat_change()), reviewer="plan-reviewer"
    )
    assert evidence.missing_source_streaks
    assert max(evidence.missing_source_streaks.values()) >= 6


def test_evidence_last_round_improvement_is_measured_on_the_blocker_major_curve() -> None:
    from super_harness.engineering.review_budget import derive_round_budget_evidence

    for change, curve in _plan_curves().items():
        evidence = derive_round_budget_evidence(
            _corpus_events_for(change), reviewer="plan-reviewer"
        )
        if len(curve) < 2:
            assert evidence.last_round_improved is None
        else:
            assert evidence.last_round_improved is (curve[-1] < curve[-2])


def _events_through_started_round(change: str, n: int) -> list:
    """The stream as it stood when round `n` was frozen — which is when the block
    derives its evidence in production. Replaying a finished change means slicing;
    the fold itself has no notion of "as of round n", it folds what it is given."""
    events = _corpus_events_for(change)
    started = 0
    for index, event in enumerate(events):
        payload = event.payload or {}
        if (
            event.type == "review_round_started"
            and payload.get("reviewer") == "plan-reviewer"
            and payload.get("automatic")
        ):
            started += 1
            if started == n:
                return events[:index + 1]
    raise AssertionError(f"{change} never started round {n}")


def test_evidence_headline_case_is_derived_from_the_corpus() -> None:
    """At the round where the flat change first exceeds a budget of 6: an improving
    last step while the curve as a whole has not come down, and a source missing for
    every round so far. Every number comes from the corpus — none is transcribed.

    This is the case that defeats any automatic rule and needs a human to look: the
    most recent step down is exactly what would talk someone into funding one more
    round."""
    from super_harness.engineering.review_budget import derive_round_budget_evidence

    at_block = _events_through_started_round(_flat_change(), 7)
    evidence = derive_round_budget_evidence(at_block, reviewer="plan-reviewer")

    assert evidence.started_rounds == 7                  # over a budget of 6
    curve = evidence.blocker_major_curve

    # Only some of those seven rounds produced a review at all. The gap IS the
    # headline: money was spent on rounds that reviewed nothing, and a human reading
    # only the curve would never know.
    assert evidence.imported_rounds < evidence.started_rounds
    assert len(curve) == evidence.imported_rounds

    assert evidence.last_round_improved is True          # ... and yet improving
    assert curve[-1] < curve[-2]
    assert curve[-1] >= min(curve)                       # never better than before
    assert max(curve) >= curve[0]                        # no downward trend overall
    # Streaks count CLOSED rounds, so a source can be missing from more rounds than
    # ever produced a review — which is the same story from the other side.
    assert max(evidence.missing_source_streaks.values()) >= evidence.imported_rounds


def test_evidence_never_raises_on_a_malformed_stream() -> None:
    """Same discipline as value_report: a pure fold that cannot crash a CLI."""
    from super_harness.core.events import Actor, Event
    from super_harness.engineering.review_budget import derive_round_budget_evidence

    junk = [
        Event(event_id="e1", type="review_round_started", change_id="c",
              timestamp="nonsense", actor=Actor(type="agent", identifier="x"),
              framework="plain", payload={"reviewer": "plan-reviewer", "runs": "no"}),
        Event(event_id="e2", type="review_result_imported", change_id="c",
              timestamp="", actor=Actor(type="agent", identifier="x"),
              framework="plain", payload={"reviewer": "plan-reviewer", "verdict": 7}),
        Event(event_id="e3", type="review_round_closed", change_id="c",
              timestamp="", actor=Actor(type="agent", identifier="x"),
              framework="plain", payload={"reviewer": "plan-reviewer",
                                          "missing_sources": "claude"}),
    ]
    evidence = derive_round_budget_evidence(junk, reviewer="plan-reviewer")
    assert evidence.started_rounds == 1
    assert evidence.blocker_major_curve == ()
    assert derive_round_budget_evidence([], reviewer="plan-reviewer").tokens is None


def test_missing_source_reason_from_fixture_not_corpus() -> None:
    """The corpus strips `reason` free text (see its README), so this one datum cannot
    be corpus-replayed and is pinned by a hand-written fixture instead. Do not weaken
    the redaction to make this test easier."""
    from super_harness.core.events import Actor, Event
    from super_harness.engineering.review_budget import derive_round_budget_evidence

    def ev(eid, etype, payload):
        return Event(event_id=eid, type=etype, change_id="c",
                     timestamp="2026-08-06T00:00:00Z",
                     actor=Actor(type="agent", identifier="x"),
                     framework="plain", payload=payload)

    events = [
        ev("e1", "review_run_failed", {
            "reviewer": "plan-reviewer", "source": "codex",
            "reason": "the ChatGPT account rejects the configured model (HTTP 400)"}),
        ev("e2", "review_run_failed", {
            "reviewer": "plan-reviewer", "source": "codex",
            "reason": "usage limit reached; try again 2026-08-28"}),
    ]
    evidence = derive_round_budget_evidence(events, reviewer="plan-reviewer")

    assert evidence.missing_source_reasons["codex"] == (
        "usage limit reached; try again 2026-08-28"
    )
