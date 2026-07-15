"""Pure derivation of reviewer epochs, rounds, source runs, and receipts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal

from super_harness.core.events import Event
from super_harness.core.review_verdict import verdict_blocks

_EPOCH_BOUNDARIES: dict[str, frozenset[str]] = {
    "plan-reviewer": frozenset({"plan_ready"}),
    "code-reviewer": frozenset({"implementation_complete"}),
}


@dataclass(frozen=True)
class ReviewRunState:
    run_id: str
    source: str
    protocol: str
    requested_model: str
    requested_options: dict[str, Any]
    cost_class: str | None = None
    prompt_digest: str | None = None
    invocation: dict[str, Any] | None = None
    status: Literal["pending", "imported", "failed"] = "pending"
    result_digest: str | None = None
    verdict: dict[str, Any] | None = None
    receipt: dict[str, Any] | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class ReviewRoundState:
    round_id: str
    contract_digest: str
    target_head: str
    profile_digest: str
    runs: dict[str, ReviewRunState]
    bundle_digest: str | None = None
    checklist: tuple[str, ...] = ()
    open_finding_ids: tuple[str, ...] = ()
    required_sources: tuple[str, ...] = ()
    min_independent: int = 0
    require_distinct_model_families: bool = False
    blocking_severity: str = "minor"
    frozen_governance_complete: bool = False
    automatic: bool = True
    authorization_id: str | None = None
    status: Literal["open", "closed"] = "open"
    outcome: str | None = None


@dataclass(frozen=True)
class ReviewRoundAuthorization:
    authorization_id: str
    contract_digest: str
    profile_digest: str
    sources: tuple[str, ...]
    reason: str
    consumed_by_round_id: str | None = None


@dataclass(frozen=True)
class ReviewExecutionState:
    reviewer: str
    epoch_id: str | None
    rounds: tuple[ReviewRoundState, ...]
    authorizations: tuple[ReviewRoundAuthorization, ...]

    @property
    def automatic_rounds_used(self) -> int:
        return sum(1 for round_state in self.rounds if round_state.automatic)

    @property
    def retained_sources(self) -> tuple[str, ...]:
        """Imported sources reusable by the latest identical frozen contract."""

        if not self.rounds:
            return ()

        def succeeded(run: ReviewRunState, blocking_severity: str) -> bool:
            verdict = run.verdict
            if run.status != "imported" or not isinstance(verdict, dict):
                return False
            if verdict.get("outcome") in {"rejected", "failed"}:
                return False
            # Mirror the round-close reject predicate: a source is reusable iff
            # its verdict is non-blocking under the round's frozen threshold
            # (code review grades by finding severity; plan review by checklist
            # fail). Shared `verdict_blocks` keeps retention and close aligned —
            # a minor-only code source that the round would approve must be
            # retained across a peer failure, not forced to re-run.
            return not verdict_blocks(
                verdict, reviewer=self.reviewer, blocking_severity=blocking_severity
            )

        latest = self.rounds[-1]
        retained: set[str] = set()
        for round_state in self.rounds[:-1]:
            if (
                round_state.contract_digest != latest.contract_digest
                or round_state.target_head != latest.target_head
                or round_state.profile_digest != latest.profile_digest
                or round_state.outcome != "execution_failed"
            ):
                continue
            retained.update(
                source
                for source, run in round_state.runs.items()
                if succeeded(run, round_state.blocking_severity)
            )
        if latest.status == "open" or latest.outcome == "execution_failed":
            for source, run in latest.runs.items():
                retained.discard(source)
                if succeeded(run, latest.blocking_severity):
                    retained.add(source)
        return tuple(sorted(retained))

    @property
    def available_authorization_ids(self) -> tuple[str, ...]:
        return tuple(
            authorization.authorization_id
            for authorization in self.authorizations
            if authorization.consumed_by_round_id is None
        )


def derive_review_execution(
    events: list[Event], reviewer: str
) -> ReviewExecutionState:
    """Fold append-ordered events into one review role's current epoch state."""

    boundaries = _EPOCH_BOUNDARIES.get(reviewer)
    if boundaries is None:
        raise ValueError(f"unknown reviewer role {reviewer!r}")
    epoch_id: str | None = None
    start = len(events)
    for index, event in enumerate(events):
        if event.type in boundaries:
            epoch_id = event.event_id
            start = index + 1

    rounds: list[ReviewRoundState] = []
    authorizations: list[ReviewRoundAuthorization] = []
    if epoch_id is None:
        return ReviewExecutionState(
            reviewer=reviewer, epoch_id=None, rounds=(), authorizations=()
        )

    for event in events[start:]:
        payload = event.payload or {}
        if payload.get("reviewer") != reviewer or payload.get("epoch_id") != epoch_id:
            continue
        if event.type == "review_round_authorized":
            contract_digest = payload.get("contract_digest")
            profile_digest = payload.get("profile_digest")
            raw_sources = payload.get("sources")
            reason = payload.get("reason")
            if (
                not isinstance(contract_digest, str)
                or not contract_digest
                or not isinstance(profile_digest, str)
                or not profile_digest
                or not isinstance(raw_sources, list)
                or any(not isinstance(source, str) or not source for source in raw_sources)
                or len(set(raw_sources)) != len(raw_sources)
                or not isinstance(reason, str)
                or not reason
            ):
                continue
            authorizations.append(
                ReviewRoundAuthorization(
                    authorization_id=event.event_id,
                    contract_digest=contract_digest,
                    profile_digest=profile_digest,
                    sources=tuple(sorted(raw_sources)),
                    reason=reason,
                )
            )
            continue
        if event.type == "review_round_started":
            raw_runs = payload.get("runs")
            if not isinstance(raw_runs, list):
                continue
            runs: dict[str, ReviewRunState] = {}
            for raw in raw_runs:
                if not isinstance(raw, dict):
                    continue
                source = raw.get("source")
                run_id = raw.get("run_id")
                protocol = raw.get("protocol")
                requested_model = raw.get("requested_model")
                requested_options = raw.get("requested_options")
                if not all(
                    isinstance(value, str) and value
                    for value in (source, run_id, protocol, requested_model)
                ) or not isinstance(requested_options, dict):
                    continue
                assert isinstance(source, str)
                assert isinstance(run_id, str)
                assert isinstance(protocol, str)
                assert isinstance(requested_model, str)
                runs[source] = ReviewRunState(
                    run_id=run_id,
                    source=source,
                    protocol=protocol,
                    requested_model=requested_model,
                    requested_options=dict(requested_options),
                    cost_class=(
                        raw.get("cost_class")
                        if isinstance(raw.get("cost_class"), str)
                        else None
                    ),
                    prompt_digest=(
                        raw.get("prompt_digest")
                        if isinstance(raw.get("prompt_digest"), str)
                        else None
                    ),
                    invocation=(
                        dict(raw["invocation"])
                        if isinstance(raw.get("invocation"), dict)
                        else None
                    ),
                )
            round_id = payload.get("round_id")
            contract_digest = payload.get("contract_digest")
            target_head = payload.get("target_head")
            profile_digest = payload.get("profile_digest")
            if not all(
                isinstance(value, str) and value
                for value in (round_id, contract_digest, target_head, profile_digest)
            ):
                continue
            assert isinstance(round_id, str)
            assert isinstance(contract_digest, str)
            assert isinstance(target_head, str)
            assert isinstance(profile_digest, str)
            authorization_id = payload.get("authorization_id")
            automatic = payload.get("automatic", True)
            if not isinstance(automatic, bool):
                continue
            resolved_authorization_id = (
                authorization_id
                if isinstance(authorization_id, str) and authorization_id
                else None
            )
            raw_checklist = payload.get("checklist", [])
            checklist = (
                tuple(raw_checklist)
                if isinstance(raw_checklist, list)
                and all(isinstance(item, str) for item in raw_checklist)
                else ()
            )
            raw_open_findings = payload.get("open_finding_ids", [])
            open_finding_ids = (
                tuple(raw_open_findings)
                if isinstance(raw_open_findings, list)
                and all(isinstance(item, str) for item in raw_open_findings)
                else ()
            )
            raw_retained = payload.get("retained_sources", [])
            retained_sources = (
                tuple(raw_retained)
                if isinstance(raw_retained, list)
                and all(isinstance(item, str) for item in raw_retained)
                else ()
            )
            raw_required = payload.get("required_sources")
            required_sources = (
                tuple(raw_required)
                if isinstance(raw_required, list)
                and all(isinstance(item, str) and item for item in raw_required)
                and len(set(raw_required)) == len(raw_required)
                else tuple(dict.fromkeys((*retained_sources, *runs)))
            )
            raw_min_independent = payload.get("min_independent")
            min_independent = (
                raw_min_independent
                if isinstance(raw_min_independent, int)
                and not isinstance(raw_min_independent, bool)
                and raw_min_independent > 0
                else len(required_sources)
            )
            raw_distinct_families = payload.get("require_distinct_model_families")
            require_distinct_model_families = (
                raw_distinct_families
                if isinstance(raw_distinct_families, bool)
                else False
            )
            raw_blocking_severity = payload.get("blocking_severity")
            blocking_severity = (
                raw_blocking_severity
                if isinstance(raw_blocking_severity, str)
                and raw_blocking_severity in {"blocker", "major", "minor"}
                else "minor"
            )
            frozen_governance_complete = (
                isinstance(raw_required, list)
                and bool(raw_required)
                and all(isinstance(item, str) and item for item in raw_required)
                and len(set(raw_required)) == len(raw_required)
                and isinstance(raw_min_independent, int)
                and not isinstance(raw_min_independent, bool)
                and raw_min_independent > 0
                and isinstance(raw_distinct_families, bool)
            )
            rounds.append(
                ReviewRoundState(
                    round_id=round_id,
                    contract_digest=contract_digest,
                    target_head=target_head,
                    profile_digest=profile_digest,
                    runs=runs,
                    bundle_digest=(
                        payload.get("bundle_digest")
                        if isinstance(payload.get("bundle_digest"), str)
                        else None
                    ),
                    checklist=checklist,
                    open_finding_ids=open_finding_ids,
                    required_sources=required_sources,
                    min_independent=min_independent,
                    require_distinct_model_families=require_distinct_model_families,
                    blocking_severity=blocking_severity,
                    frozen_governance_complete=frozen_governance_complete,
                    automatic=automatic,
                    authorization_id=resolved_authorization_id,
                )
            )
            if resolved_authorization_id is not None:
                for index, authorization in enumerate(authorizations):
                    if (
                        authorization.authorization_id == resolved_authorization_id
                        and authorization.consumed_by_round_id is None
                        and authorization.contract_digest == contract_digest
                        and authorization.profile_digest == profile_digest
                        and authorization.sources == tuple(sorted(runs))
                    ):
                        authorizations[index] = replace(
                            authorization, consumed_by_round_id=round_id
                        )
                        break
            continue
        if event.type == "review_result_imported":
            round_id = payload.get("round_id")
            run_id = payload.get("run_id")
            source = payload.get("source")
            result_digest = payload.get("result_digest")
            verdict = payload.get("verdict")
            receipt = payload.get("receipt")
            if not all(
                isinstance(value, str) and value
                for value in (round_id, run_id, source, result_digest)
            ) or not isinstance(verdict, dict) or not isinstance(receipt, dict):
                continue
            assert isinstance(round_id, str)
            assert isinstance(run_id, str)
            assert isinstance(source, str)
            assert isinstance(result_digest, str)
            for index, round_state in enumerate(rounds):
                run = round_state.runs.get(source)
                if (
                    round_state.round_id != round_id
                    or round_state.contract_digest != payload.get("contract_digest")
                    or round_state.target_head != payload.get("target_head")
                    or run is None
                    or run.run_id != run_id
                ):
                    continue
                updated_runs = dict(round_state.runs)
                updated_runs[source] = replace(
                    run,
                    status="imported",
                    result_digest=result_digest,
                    verdict=dict(verdict),
                    receipt=dict(receipt),
                )
                rounds[index] = replace(round_state, runs=updated_runs)
                break
            continue
        if event.type == "review_run_failed":
            round_id = payload.get("round_id")
            run_id = payload.get("run_id")
            source = payload.get("source")
            reason = payload.get("reason")
            if not all(
                isinstance(value, str) and value
                for value in (round_id, run_id, source, reason)
            ):
                continue
            assert isinstance(round_id, str)
            assert isinstance(run_id, str)
            assert isinstance(source, str)
            assert isinstance(reason, str)
            for index, round_state in enumerate(rounds):
                run = round_state.runs.get(source)
                if (
                    round_state.round_id != round_id
                    or round_state.contract_digest != payload.get("contract_digest")
                    or round_state.target_head != payload.get("target_head")
                    or run is None
                    or run.run_id != run_id
                ):
                    continue
                updated_runs = dict(round_state.runs)
                updated_runs[source] = replace(
                    run, status="failed", failure_reason=reason
                )
                rounds[index] = replace(round_state, runs=updated_runs)
                break
            continue
        if event.type == "review_round_closed":
            round_id = payload.get("round_id")
            outcome = payload.get("outcome")
            if not isinstance(round_id, str) or not isinstance(outcome, str):
                continue
            for index, round_state in enumerate(rounds):
                if (
                    round_state.round_id == round_id
                    and round_state.contract_digest == payload.get("contract_digest")
                    and round_state.target_head == payload.get("target_head")
                ):
                    rounds[index] = replace(
                        round_state, status="closed", outcome=outcome
                    )
                    break
    return ReviewExecutionState(
        reviewer=reviewer,
        epoch_id=epoch_id,
        rounds=tuple(rounds),
        authorizations=tuple(authorizations),
    )
