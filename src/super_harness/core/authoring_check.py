"""Authoring-time conformance verdict (design 2026-07-01, Rev 2).

Agent-agnostic: run the ratified, authoring-opted-in tier-1 decision checks once and
return a structured Verdict. Reused by the Stop-hook path. No agent knowledge, no
prose, no daemon. This is deliberately NOT a `Sensor` (no dispatcher / event
emission) — it is a synchronous verdict producer.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from super_harness.core.check_runner import CheckFailure, run_executable_checks
from super_harness.core.decisions import Decision, compute_body_hash, load_decisions

# Inner check budget; MUST stay strictly below the Claude Code hook's declared 10s
# outer timeout so a slow graph degrades to `unavailable` (silent) rather than a hard
# kill (design §5).
AUTHORING_CHECK_TIMEOUT = 8

# Exit codes that mean "the check could not run" (NOT a violation): timeout/spawn
# failure (runner sentinel -1) and tool-not-found / not-executable under shell=True
# (126/127, e.g. `lint-imports` absent). Design §3/§5: never emit a false "you violated".
_UNAVAILABLE_EXIT_CODES = frozenset({-1, 126, 127})


@dataclass(frozen=True)
class Violation:
    decision_id: str
    detail: str  # the check's own output (CheckFailure.detail)
    decision_doc_path: str


@dataclass(frozen=True)
class Verdict:
    violations: list[Violation]


def _to_violations(failures: list[CheckFailure]) -> list[Violation]:
    """Map real check failures to violations, dropping `unavailable` results (a check
    that could not run is NOT 'you violated X' — design §3)."""
    return [
        Violation(
            decision_id=f.id,
            detail=f.detail,
            decision_doc_path=f"docs/decisions/{f.id}.md",
        )
        for f in failures
        if f.exit_code not in _UNAVAILABLE_EXIT_CODES
    ]


def _integrity_ok(d: Decision) -> bool:
    """True if the decision's body still matches its ratified hash. Mirror the CI floor
    (cli/decision.py): a tamper-detected decision must NOT have its arbitrary shell
    check run automatically in the interactive loop (design §4 trust control)."""
    if not d.ratified_text_hash:
        return True
    return compute_body_hash(d.body) == d.ratified_text_hash


def run_authoring_check(workspace_root: Path) -> Verdict:
    """Run the ratified, `authoring_time`, integrity-clean tier-1 checks once.

    Only decisions that opted into the interactive loop (`authoring_time: true`) AND
    whose body still matches their ratified hash run — the safety control (design §4).
    Never raises for a check failure (a failing check is data, not an error).
    """
    decisions, _errors = load_decisions(workspace_root)
    opted = [d for d in decisions if d.authoring_time and _integrity_ok(d)]
    if not opted:
        return Verdict(violations=[])
    # run_executable_checks already skips non-ratified + `check is None`.
    failures = run_executable_checks(workspace_root, opted, timeout=AUTHORING_CHECK_TIMEOUT)
    return Verdict(violations=_to_violations(failures))
