from __future__ import annotations

from super_harness.core.events import Actor, Event
from super_harness.engineering.review_runs import derive_review_execution


def _event(event_id: str, event_type: str, payload: dict[str, object]) -> Event:
    return Event(
        event_id=event_id,
        type=event_type,
        change_id="change",
        timestamp="2026-07-13T00:00:00Z",
        actor=Actor(type="agent", identifier="test"),
        framework="plain",
        payload=payload,
    )


def test_started_round_consumes_budget_and_leaves_runs_pending() -> None:
    events = [
        _event("epoch-plan", "plan_ready", {}),
        _event(
            "event-round-1",
            "review_round_started",
            {
                "reviewer": "plan-reviewer",
                "epoch_id": "epoch-plan",
                "round_id": "round-1",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "profile_digest": "profiles-1",
                "runs": [
                    {
                        "run_id": "run-codex",
                        "source": "codex",
                        "protocol": "codex-cli",
                        "requested_model": "gpt-review",
                        "requested_options": {"reasoning_effort": "medium"},
                    },
                    {
                        "run_id": "run-claude",
                        "source": "claude",
                        "protocol": "claude-cli",
                        "requested_model": "claude-review",
                        "requested_options": {"effort": "medium"},
                    },
                ],
            },
        ),
    ]

    execution = derive_review_execution(events, "plan-reviewer")

    assert execution.epoch_id == "epoch-plan"
    assert execution.automatic_rounds_used == 1
    assert execution.rounds[0].round_id == "round-1"
    assert execution.rounds[0].status == "open"
    assert execution.rounds[0].runs["codex"].status == "pending"
    assert execution.rounds[0].runs["claude"].status == "pending"


def test_imported_result_updates_only_its_bound_run() -> None:
    events = [
        _event("epoch-plan", "plan_ready", {}),
        _event(
            "event-round-1",
            "review_round_started",
            {
                "reviewer": "plan-reviewer",
                "epoch_id": "epoch-plan",
                "round_id": "round-1",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "profile_digest": "profiles-1",
                "runs": [
                    {
                        "run_id": "run-codex",
                        "source": "codex",
                        "protocol": "codex-cli",
                        "requested_model": "gpt-review",
                        "requested_options": {"reasoning_effort": "medium"},
                    },
                    {
                        "run_id": "run-claude",
                        "source": "claude",
                        "protocol": "claude-cli",
                        "requested_model": "claude-review",
                        "requested_options": {"effort": "medium"},
                    },
                ],
            },
        ),
        _event(
            "event-result-codex",
            "review_result_imported",
            {
                "reviewer": "plan-reviewer",
                "epoch_id": "epoch-plan",
                "round_id": "round-1",
                "run_id": "run-codex",
                "source": "codex",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "result_digest": "result-1",
                "verdict": {"outcome": "approved"},
                "receipt": {"actual_model": None, "usage": None},
            },
        ),
    ]

    execution = derive_review_execution(events, "plan-reviewer")

    codex = execution.rounds[0].runs["codex"]
    assert codex.status == "imported"
    assert codex.result_digest == "result-1"
    assert codex.verdict == {"outcome": "approved"}
    assert codex.receipt == {"actual_model": None, "usage": None}
    assert execution.rounds[0].runs["claude"].status == "pending"


def test_execution_failed_round_retains_successful_peer_result() -> None:
    events = [
        _event("epoch-code", "implementation_complete", {}),
        _event(
            "event-round-1",
            "review_round_started",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "profile_digest": "profiles-1",
                "runs": [
                    {
                        "run_id": "run-codex",
                        "source": "codex",
                        "protocol": "codex-cli",
                        "requested_model": "gpt-review",
                        "requested_options": {"reasoning_effort": "medium"},
                    },
                    {
                        "run_id": "run-claude",
                        "source": "claude",
                        "protocol": "claude-cli",
                        "requested_model": "claude-review",
                        "requested_options": {"effort": "medium"},
                    },
                ],
            },
        ),
        _event(
            "event-result-codex",
            "review_result_imported",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "run_id": "run-codex",
                "source": "codex",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "result_digest": "result-1",
                "verdict": {"outcome": "approved"},
                "receipt": {},
            },
        ),
        _event(
            "event-failed-claude",
            "review_run_failed",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "run_id": "run-claude",
                "source": "claude",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "reason": "producer exited 1",
            },
        ),
        _event(
            "event-close-1",
            "review_round_closed",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "outcome": "execution_failed",
            },
        ),
    ]

    execution = derive_review_execution(events, "code-reviewer")

    round_state = execution.rounds[0]
    assert round_state.status == "closed"
    assert round_state.outcome == "execution_failed"
    assert round_state.runs["codex"].status == "imported"
    assert round_state.runs["claude"].status == "failed"
    assert round_state.runs["claude"].failure_reason == "producer exited 1"


def test_failed_source_retry_keeps_identical_contract_peer_receipt() -> None:
    events = [
        _event("epoch-code", "implementation_complete", {}),
        _event(
            "event-round-1",
            "review_round_started",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "profile_digest": "profiles-1",
                "runs": [
                    {
                        "run_id": "run-codex",
                        "source": "codex",
                        "protocol": "codex-cli",
                        "requested_model": "gpt-review",
                        "requested_options": {},
                    },
                    {
                        "run_id": "run-claude-1",
                        "source": "claude",
                        "protocol": "claude-cli",
                        "requested_model": "claude-review",
                        "requested_options": {},
                    },
                ],
            },
        ),
        _event(
            "event-result-codex",
            "review_result_imported",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "run_id": "run-codex",
                "source": "codex",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "result_digest": "result-1",
                "verdict": {"outcome": "approved"},
                "receipt": {},
            },
        ),
        _event(
            "event-failed-claude",
            "review_run_failed",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "run_id": "run-claude-1",
                "source": "claude",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "reason": "crashed",
            },
        ),
        _event(
            "event-close-1",
            "review_round_closed",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "outcome": "execution_failed",
            },
        ),
        _event(
            "event-round-2",
            "review_round_started",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-2",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "profile_digest": "profiles-1",
                "runs": [
                    {
                        "run_id": "run-claude-2",
                        "source": "claude",
                        "protocol": "claude-cli",
                        "requested_model": "claude-review",
                        "requested_options": {},
                    }
                ],
            },
        ),
    ]

    execution = derive_review_execution(events, "code-reviewer")

    assert execution.automatic_rounds_used == 2
    assert execution.retained_sources == ("codex",)


def test_rejected_result_is_not_retained_as_a_successful_peer() -> None:
    events = [
        _event("epoch-code", "implementation_complete", {}),
        _event(
            "event-round-1",
            "review_round_started",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "profile_digest": "profiles-1",
                "runs": [
                    {
                        "run_id": "run-codex",
                        "source": "codex",
                        "protocol": "codex-cli",
                        "requested_model": "gpt-review",
                        "requested_options": {},
                    },
                    {
                        "run_id": "run-claude",
                        "source": "claude",
                        "protocol": "claude-cli",
                        "requested_model": "claude-review",
                        "requested_options": {},
                    },
                ],
            },
        ),
        _event(
            "event-result-codex",
            "review_result_imported",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "run_id": "run-codex",
                "source": "codex",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "result_digest": "result-1",
                "verdict": {
                    "scope_sufficient": True,
                    "checklist": [{"item": "code-quality", "status": "fail"}],
                },
                "receipt": {},
            },
        ),
        _event(
            "event-failed-claude",
            "review_run_failed",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "run_id": "run-claude",
                "source": "claude",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "reason": "producer unavailable",
            },
        ),
        _event(
            "event-close-1",
            "review_round_closed",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "outcome": "execution_failed",
            },
        ),
    ]

    execution = derive_review_execution(events, "code-reviewer")

    assert execution.retained_sources == ()


def test_extra_round_authorization_is_bound_and_consumed_once() -> None:
    events = [_event("epoch-code", "implementation_complete", {})]
    for number in (1, 2):
        events.append(
            _event(
                f"event-round-{number}",
                "review_round_started",
                {
                    "reviewer": "code-reviewer",
                    "epoch_id": "epoch-code",
                    "round_id": f"round-{number}",
                    "contract_digest": "contract-1",
                    "target_head": "abc123",
                    "profile_digest": "profiles-1",
                    "runs": [
                        {
                            "run_id": f"run-codex-{number}",
                            "source": "codex",
                            "protocol": "codex-cli",
                            "requested_model": "gpt-review",
                            "requested_options": {},
                        }
                    ],
                },
            )
        )
    events.extend(
        [
            _event(
                "authorization-1",
                "review_round_authorized",
                {
                    "reviewer": "code-reviewer",
                    "epoch_id": "epoch-code",
                    "contract_digest": "contract-1",
                    "profile_digest": "profiles-1",
                    "sources": ["codex"],
                    "reason": "Human requested another automated round",
                },
            ),
            _event(
                "event-round-3",
                "review_round_started",
                {
                    "reviewer": "code-reviewer",
                    "epoch_id": "epoch-code",
                    "round_id": "round-3",
                    "contract_digest": "contract-1",
                    "target_head": "abc123",
                    "profile_digest": "profiles-1",
                    "authorization_id": "authorization-1",
                    "runs": [
                        {
                            "run_id": "run-codex-3",
                            "source": "codex",
                            "protocol": "codex-cli",
                            "requested_model": "gpt-review",
                            "requested_options": {},
                        }
                    ],
                },
            ),
        ]
    )

    execution = derive_review_execution(events, "code-reviewer")

    assert execution.automatic_rounds_used == 3
    assert execution.authorizations[0].authorization_id == "authorization-1"
    assert execution.authorizations[0].consumed_by_round_id == "round-3"
    assert execution.available_authorization_ids == ()


def test_changed_contract_does_not_retain_prior_source_result() -> None:
    events = [
        _event("epoch-code", "implementation_complete", {}),
        _event(
            "event-round-1",
            "review_round_started",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "profile_digest": "profiles-1",
                "runs": [
                    {
                        "run_id": "run-codex-1",
                        "source": "codex",
                        "protocol": "codex-cli",
                        "requested_model": "gpt-review",
                        "requested_options": {},
                    }
                ],
            },
        ),
        _event(
            "event-result-codex",
            "review_result_imported",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "run_id": "run-codex-1",
                "source": "codex",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "result_digest": "result-1",
                "verdict": {"outcome": "approved"},
                "receipt": {},
            },
        ),
        _event(
            "event-round-2",
            "review_round_started",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-2",
                "contract_digest": "contract-2",
                "target_head": "def456",
                "profile_digest": "profiles-1",
                "runs": [
                    {
                        "run_id": "run-claude-2",
                        "source": "claude",
                        "protocol": "claude-cli",
                        "requested_model": "claude-review",
                        "requested_options": {},
                    }
                ],
            },
        ),
    ]

    execution = derive_review_execution(events, "code-reviewer")

    assert execution.retained_sources == ()


def _round_started(payload_extra: dict[str, object]) -> list[Event]:
    return [
        _event("epoch-code", "implementation_complete", {}),
        _event(
            "event-round-1",
            "review_round_started",
            {
                "reviewer": "code-reviewer",
                "epoch_id": "epoch-code",
                "round_id": "round-1",
                "contract_digest": "contract-1",
                "target_head": "abc123",
                "profile_digest": "profiles-1",
                "runs": [
                    {
                        "run_id": "run-codex",
                        "source": "codex",
                        "protocol": "codex-cli",
                        "requested_model": "gpt-review",
                        "requested_options": {},
                    }
                ],
                **payload_extra,
            },
        ),
    ]


def test_round_state_reads_frozen_blocking_severity() -> None:
    events = _round_started({"blocking_severity": "blocker"})

    state = derive_review_execution(events, "code-reviewer").rounds[0]

    assert state.blocking_severity == "blocker"


def test_round_state_missing_blocking_severity_defaults_to_minor() -> None:
    # Pre-feature review_round_started events lack the field; reproduce the
    # strict "everything blocks" behavior those rounds were opened under.
    events = _round_started({})

    state = derive_review_execution(events, "code-reviewer").rounds[0]

    assert state.blocking_severity == "minor"
