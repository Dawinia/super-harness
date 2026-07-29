"""PreToolUseGate — in-process gate for the 10-state pre-tool-use matrix.

Per sensor-gate-architecture §2.2 + lifecycle-event-model §3.7. This gate
reads the canonical policy from `super_harness.gates.decisions` (the single
source of truth shared with the daemon) and maps each ChangeState to an
allow/block verdict. It does NOT invent policy — it only executes the table.

API stability: **experimental** (v0.1). See `super_harness.gates` for the
Gate contract; this gate may change in v0.2 without backwards compatibility.
"""
from __future__ import annotations

import glob
from fnmatch import fnmatchcase
from typing import ClassVar

from super_harness.core.events import Event
from super_harness.core.plan_paths import SLUG_PLACEHOLDER
from super_harness.core.state import ChangeState
from super_harness.gates import (
    Gate,
    GateDecision,
    GateFiresOn,
    GateResult,
    ProposedAction,
)
from super_harness.gates.decisions import (
    PLAN_ARTIFACT_ALLOW_STATES,
    PLAN_PATH_ALLOW_STATES,
    PRE_TOOL_USE_DECISIONS,
    SCRATCH_ROOT,
    SUGGESTIONS,
)


class PreToolUseGate(Gate):
    """Allow/block an agent's file edit based on the change's lifecycle state.

    Reads the canonical policy from `gates.decisions`: the 10-state
    `PRE_TOOL_USE_DECISIONS` matrix plus the `PLAN_ARTIFACT_ALLOW_STATES` carve-out
    (the PLAN_REJECTED plan-artifact narrowing). It reads both — it does not fork or
    invent policy. With no active change (state is None) the gate allows. See
    lifecycle-event-model §3.7 for the per-state rationale + the carve-out.
    """

    name: ClassVar[str] = "pre-tool-use"
    version: ClassVar[str] = "0.1.0"
    fires_on: ClassVar[GateFiresOn] = "pre_tool_use"

    def __init__(self, plan_path_patterns: list[str] | None = None) -> None:
        """`plan_path_patterns` come from `core.plan_paths.load_plan_paths` (already
        validated: each contains `{slug}` and ends in `.md`). Injected rather than
        read here so the gate stays pure and testable. Default `None` keeps every
        existing construction site (and every pre-existing test) behaving exactly as
        before: no patterns → no plan-path allowance.

        Copied into a new `list` (not stored by reference) so a caller mutating the
        list it passed in afterwards cannot silently change this gate's policy after
        construction. A non-`list` input (forged/corrupt config) becomes `[]` here —
        the `isinstance(self._plan_path_patterns, list)` check in `decide()` is kept
        anyway as belt-and-braces, since the attribute remains reachable."""
        self._plan_path_patterns = (
            list(plan_path_patterns) if isinstance(plan_path_patterns, list) else []
        )

    def decide(
        self,
        action: ProposedAction,
        state: ChangeState | None,
        events: list[Event],
    ) -> GateResult:
        if state is None:
            return GateResult(decision=GateDecision.ALLOW, reason="no active change")
        # Plan-artifact carve-out (HG-PLAN-AUTHORING), read from the single policy
        # module. In a PLAN_ARTIFACT_ALLOW_STATES state, an edit to one of the
        # change's recorded plan artifacts (a marked `.md`) is ALLOWED. Guards, in
        # order: `.md` (case-insensitive) so no source path qualifies even if forged
        # into the list; `isinstance(list)` so a forged non-list state.yaml yields a
        # clean BLOCK instead of a `TypeError` the hook would fail-open on. Everything
        # else falls through to the table below (BLOCK).
        # Allowances below are hard-coded path whitelists compared AFTER
        # canonicalization — never derived from gitignore status.
        # @decision:d-gate-governs-git-product
        rp = action.resolved_path
        # Scratch-area allowance (design 2026-07-29): the change's own scratch dir is
        # allowed in EVERY state. `rp` is already canonicalized by the caller, so a
        # `..`/symlink escape has resolved to its real target and fails this prefix.
        # The trailing `/` makes the test segment-aware: `.harness/scratch/my-change`
        # must not admit `.harness/scratch/my-change-evil/x`.
        if rp and state.change_id:
            scratch_prefix = f"{SCRATCH_ROOT}/{state.change_id}/"
            if rp.startswith(scratch_prefix):
                return GateResult(
                    decision=GateDecision.ALLOW,
                    reason=f"{state.current_state}: scratch area ({rp})",
                )
        # Plan-path allowance (design 2026-07-29). Guards, in order: state opted in;
        # a canonicalized path exists; the RESOLVED path is `.md` (so a *symlinked*
        # `docs/plans/x-<slug>.md` -> `src/evil.py` cannot launder — a hardlink is a
        # separate, known, pre-existing gap shared with the PLAN_ARTIFACT_ALLOW_STATES
        # carve-out and is out of scope here); the pattern list is really a list (a
        # forged config must BLOCK, never raise — the hook treats an exception as
        # non-blocking); and, per pattern, that it is a `str` CONTAINING the literal
        # `{slug}` placeholder before it is ever substituted into or matched.
        #
        # The `{slug}` check is defence in depth: `core.plan_paths.load_plan_paths`
        # already rejects a pattern lacking it at load time, but patterns are
        # INJECTED here (the gate never reads the file itself), so the gate must not
        # assume the loader's guarantee holds for whatever list it was constructed
        # with. Without this check a pattern like `"*.md"` would silently degrade the
        # allowance from "this change's plan document" to "any `.md` anywhere in the
        # repo" in INTENT_DECLARED — defeating the very binding this design exists for.
        #
        # `change_id` is ESCAPED before substitution. It is read from
        # `.harness/state.yaml`, which is gitignored and writable by the agent in
        # every ALLOW state, and `change start`'s slug validation does not protect
        # that path — so a forged `change_id` containing `*`, `?` or `[...]` would
        # otherwise widen the pattern past the active change (e.g. `*` turning
        # `docs/plans/*{slug}*.md` into a match for every doc). `glob.escape` renders
        # those characters literal, keeping the binding the guard rail promises.
        #
        # The `.replace`/`fnmatchcase` call is wrapped in `try/except Exception` (not
        # `BaseException`): `isinstance(pattern, str)` is true for a `str` subclass,
        # which could override `.replace` to raise. An exception escaping `decide()`
        # is indistinguishable, to the hook, from a gate that allows — it is treated
        # as non-blocking. A hostile pattern must degrade to "does not match", never
        # to "gate bypassed".
        if (
            state.current_state in PLAN_PATH_ALLOW_STATES
            and rp
            and rp.lower().endswith(".md")
            and state.change_id
            and isinstance(state.change_id, str)
            and isinstance(self._plan_path_patterns, list)
        ):
            safe_slug = glob.escape(state.change_id)
            for pattern in self._plan_path_patterns:
                if not isinstance(pattern, str) or SLUG_PLACEHOLDER not in pattern:
                    continue
                try:
                    matched = fnmatchcase(rp, pattern.replace(SLUG_PLACEHOLDER, safe_slug))
                except Exception:
                    continue
                if matched:
                    return GateResult(
                        decision=GateDecision.ALLOW,
                        reason=(
                            f"{state.current_state}: plan-document authoring "
                            f"authorized ({rp})"
                        ),
                    )
        if (
            state.current_state in PLAN_ARTIFACT_ALLOW_STATES
            and rp
            and rp.lower().endswith(".md")
            and isinstance(state.plan_artifacts, list)
            and rp in state.plan_artifacts
        ):
            return GateResult(
                decision=GateDecision.ALLOW,
                reason=f"{state.current_state}: plan-artifact revision authorized ({rp})",
            )
        decision_str, reason = PRE_TOOL_USE_DECISIONS.get(
            state.current_state, ("block", f"unknown state: {state.current_state}")
        )
        decision = (
            GateDecision.ALLOW if decision_str == "allow" else GateDecision.BLOCK
        )
        blocked = f"{action.kind} {action.file or ''}".strip() or None
        return GateResult(
            decision=decision,
            reason=reason,
            blocked_action=blocked,
            suggested_action=SUGGESTIONS.get(state.current_state),
        )
