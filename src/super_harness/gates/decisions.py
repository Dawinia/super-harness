"""Single source of truth for the 10-state pre-tool-use gate matrix.

This module is the **canonical** home of lifecycle-event-model §3.7's "Gate
矩阵". Gate policy lives here in two literals: `PRE_TOOL_USE_DECISIONS` (the
per-state default `(decision, reason)` matrix) and `PLAN_ARTIFACT_ALLOW_STATES`
(the PLAN_REJECTED plan-artifact narrowing — HG-PLAN-AUTHORING). The in-process
`super_harness.gates.pre_tool_use.PreToolUseGate` — used by both the
`super-harness-hook` decision path and the `gate check` CLI — reads BOTH literals
so the policy lives in exactly one module. The gate does NOT invent or fork policy
— it only executes what this module declares. (Cold-path gates in Phase 12/13 read
their own tables; the invariant is that no reader forks THESE.)

Kept import-light on purpose (pure literals, no heavy imports) so that importing
the policy never drags in the CLI or observer stacks.
"""
from __future__ import annotations

__all__ = ["PLAN_ARTIFACT_ALLOW_STATES", "PRE_TOOL_USE_DECISIONS", "SUGGESTIONS"]

# 10-state decision table from lifecycle-event-model §3.7. Verbatim copy —
# every (decision, reason) pair must match the spec's Gate 矩阵 exactly.
# @decision:d-single-gate-policy
PRE_TOOL_USE_DECISIONS: dict[str, tuple[str, str]] = {
    "INTENT_DECLARED": ("block", "INTENT_DECLARED: plan not drafted yet"),
    "AWAITING_PLAN_REVIEW": ("block", "AWAITING_PLAN_REVIEW: plan review in progress"),
    "PLAN_REJECTED": ("block", "PLAN_REJECTED: awaiting plan revision"),
    "PLAN_APPROVED": ("allow", "PLAN_APPROVED: implementation may proceed"),
    "IMPLEMENTATION_IN_PROGRESS": ("allow", "IMPLEMENTATION_IN_PROGRESS"),
    "AWAITING_CODE_REVIEW": ("block", "AWAITING_CODE_REVIEW: frozen pending review"),
    "CODE_REVIEW_REJECTED": (
        "allow",
        "CODE_REVIEW_REJECTED: edits permitted to fix review feedback",
    ),
    "READY_TO_MERGE": ("block", "READY_TO_MERGE: ready for merge, no further edits"),
    "ARCHIVED": ("block", "ARCHIVED: terminal state"),
    "ABANDONED": ("block", "ABANDONED: terminal state"),
}

# States whose default (above) is `block`, but where an edit to one of the change's
# recorded plan artifacts (ChangeState.plan_artifacts — marked `.md` only) is ALLOWED:
# the authorized in-gate plan-authoring path (HG-PLAN-AUTHORING). This lives HERE, in
# the single policy module, so d-single-gate-policy holds — the gate READS this set,
# it does not fork its own per-path policy. The exception can only NARROW `block` to a
# specific allow for a marked `.md` the (reviewed) plan submission recorded; it never
# widens to source. PLAN_REJECTED is the only state that both blocks by default and is
# where plan revision legitimately happens (AWAITING_PLAN_REVIEW stays frozen;
# INTENT_DECLARED has no recorded artifact yet; PLAN_APPROVED onward already allow all
# edits, so the carve-out is moot there).
# @decision:d-single-gate-policy
PLAN_ARTIFACT_ALLOW_STATES: frozenset[str] = frozenset({"PLAN_REJECTED"})

# Imperative "what to do next" line for each BLOCKING state. The reason string
# tells the agent WHY the edit was blocked; the suggestion tells it the next
# concrete step. Only blocking states appear here — allowed states need no
# remediation. PreToolUseGate surfaces these via GateResult.suggested_action.
SUGGESTIONS: dict[str, str] = {
    "INTENT_DECLARED": "Draft a plan, then mark it ready, then retry the edit.",
    "AWAITING_PLAN_REVIEW": "Wait for the plan reviewer; `super-harness status` shows progress.",
    "PLAN_REJECTED": "Revise the plan and re-submit, then retry.",
    "AWAITING_CODE_REVIEW": "Code is frozen during review; address feedback once it lands.",
    "READY_TO_MERGE": "Open/merge the PR; do not edit further.",
    "ARCHIVED": "This change is terminal; start a new change.",
    "ABANDONED": "This change is terminal; start a new change.",
}
