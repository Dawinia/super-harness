"""What a human needs in order to decide whether to fund another review round.

A pure fold over one change's events: no I/O, never raises — the same discipline as
``value_report``, because this runs inside the CLI path that BLOCKS a round and a
crash there would turn a brake into an outage.

The block is the load-bearing surface, not the authorization prompt: nobody reads a
CLI's stderr, they read what the agent says. An agent without these numbers can only
say "I was blocked, please approve", which is the rubber-stamp path.

Deliberately no automatic divergence verdict. A "three rounds without improvement"
rule was tested against the recorded corpus and the two pathological changes both
defeat it by decreasing locally while staying flat globally. A human reading
``3,2,3,2,2,1,2,3,1,1`` needs no rule.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from super_harness.core.events import Event
from super_harness.engineering.review_runs import count_automatic_rounds
from super_harness.engineering.value_report import usage_tokens


@dataclass(frozen=True)
class RoundBudgetEvidence:
    """One change's review history, as the numbers a funding decision needs."""

    reviewer: str
    # What the budget compares against: automatic rounds STARTED, failures included.
    started_rounds: int
    # Rounds that produced a review. Both counts are stated rather than reconciled —
    # a round the budget counted but the curve cannot show is money spent for no
    # findings, which is exactly what the human must see.
    imported_rounds: int
    blocker_major_curve: tuple[int, ...] = ()
    total_findings_curve: tuple[int, ...] = ()
    # Cumulative reviewer tokens including cache reads. `None` means no round reported
    # usage — never 0, which would read as "this was free".
    tokens: int | None = None
    # source -> how many of the most recent consecutive closed rounds it was missing.
    missing_source_streaks: Mapping[str, int] = field(default_factory=dict)
    # source -> the reason recorded on its most recent failed run. "the second
    # reviewer is unavailable" is useless without "why".
    missing_source_reasons: Mapping[str, str] = field(default_factory=dict)
    # Did the last round improve on the one before, by `blocker+major` and no other
    # curve? `None` with fewer than two imported rounds — there is nothing to compare.
    last_round_improved: bool | None = None


def _severity_counts(verdict: object) -> tuple[int, int] | None:
    """(blocker+major, total findings) for one verdict, or None if unreadable."""
    if not isinstance(verdict, dict):
        return None
    findings = verdict.get("findings")
    if not isinstance(findings, list):
        return 0, 0
    total = 0
    blocking = 0
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        total += 1
        if finding.get("severity") in {"blocker", "major"}:
            blocking += 1
    return blocking, total


def _missing_streaks(closed_missing: list[list[str]]) -> dict[str, int]:
    """Per source, consecutive rounds missing counting back from the latest round.

    A streak, not a total: this information is worth most at round one and decays to
    nothing by round seven, and "missing in 3 of 9 rounds, none of them recent" is a
    different situation from "missing in the last 3".
    """
    if not closed_missing:
        return {}
    # Only a source missing from the LATEST closed round has a live streak. Seeding from
    # anywhere else reports a recovered source as missing, which is worse than silence:
    # the point of this datum is that a dead reviewer should be believed.
    candidates = set(closed_missing[-1])
    streaks: dict[str, int] = {}
    for missing in reversed(closed_missing):
        current = candidates & set(missing)
        if not current:
            break
        for source in current:
            streaks[source] = streaks.get(source, 0) + 1
        candidates = current
    return streaks


def derive_round_budget_evidence(
    events: list[Event], *, reviewer: str
) -> RoundBudgetEvidence:
    """Fold one change's events into the evidence for a funding decision.

    ``events`` is one change's stream in append order. Never raises: an unreadable
    payload contributes nothing rather than aborting the fold.
    """
    # Curves are per ROUND, keyed by round_id in first-appearance order. A round with
    # `min_independent >= 2` imports once per source, so counting imports would render a
    # 4-round change as 8 rounds reviewed and destroy the started-minus-reviewed gap that
    # exists to show rounds which produced nothing. The recorded corpus cannot catch this:
    # all 71 of its plan rounds imported exactly once, its second source having been dead
    # from the first round.
    round_order: list[str] = []
    per_round: dict[str, list[int]] = {}
    tokens: int | None = None
    closed_missing: list[list[str]] = []
    reasons: dict[str, str] = {}

    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("reviewer") != reviewer:
            continue

        if event.type == "review_result_imported":
            counts = _severity_counts(payload.get("verdict"))
            if counts is not None:
                raw_round = payload.get("round_id")
                # An import with no round_id cannot be grouped with anything; give it its
                # own bucket rather than silently merging unrelated rounds.
                key = raw_round if isinstance(raw_round, str) and raw_round else (
                    f"event:{event.event_id}"
                )
                if key not in per_round:
                    round_order.append(key)
                    per_round[key] = [0, 0]
                per_round[key][0] += counts[0]
                per_round[key][1] += counts[1]
            receipt = payload.get("receipt")
            found = usage_tokens(receipt.get("usage")) if isinstance(receipt, dict) else None
            if found is not None:
                tokens = found if tokens is None else tokens + found

        elif event.type == "review_round_closed":
            missing = payload.get("missing_sources")
            closed_missing.append(
                [source for source in missing if isinstance(source, str)]
                if isinstance(missing, list)
                else []
            )

        elif event.type == "review_run_failed":
            source, reason = payload.get("source"), payload.get("reason")
            if isinstance(source, str) and source and isinstance(reason, str) and reason:
                reasons[source] = reason

    blocker_major = [per_round[key][0] for key in round_order]
    totals = [per_round[key][1] for key in round_order]
    improved: bool | None = None
    if len(blocker_major) >= 2:
        improved = blocker_major[-1] < blocker_major[-2]

    streaks = _missing_streaks(closed_missing)
    return RoundBudgetEvidence(
        reviewer=reviewer,
        started_rounds=count_automatic_rounds(events, reviewer),
        imported_rounds=len(blocker_major),
        blocker_major_curve=tuple(blocker_major),
        total_findings_curve=tuple(totals),
        tokens=tokens,
        missing_source_streaks=streaks,
        # Only report a reason for a source that is actually missing right now.
        missing_source_reasons={
            source: reason for source, reason in reasons.items()
            if not streaks or source in streaks or not closed_missing
        },
        last_round_improved=improved,
    )
