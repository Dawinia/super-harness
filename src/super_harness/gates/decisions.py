"""Single source of truth for the 10-state pre-tool-use gate matrix.

This module is the **canonical** copy of lifecycle-event-model §3.7's "Gate
矩阵": each of the 10 states maps to an `(decision, reason)` pair. Both the
daemon (`super_harness.daemon.server.DaemonServer`) and the in-process
`super_harness.gates.pre_tool_use.PreToolUseGate` read THIS literal so the
policy lives in exactly one place. The daemon does NOT invent gate policy —
it only executes this table.

Kept import-light on purpose (pure literals, no heavy imports) so that
importing the policy never drags in the daemon, socket, or CLI stacks.
"""
from __future__ import annotations

__all__ = ["PRE_TOOL_USE_DECISIONS", "SUGGESTIONS"]

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
