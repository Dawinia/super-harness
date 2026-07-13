"""Integration tests for `super-harness status` (Phase 2 Task 2.4).

Read-only command: replays events.jsonl through reducer and renders per-change
state. No event emission, no post_emit_refresh wiring needed.

Coverage map:
- test_status_no_harness_exits_3              — exit 3 when .harness/ missing
- test_status_with_slug_shows_change          — `status <slug>` for known slug
- test_status_unknown_slug_exits_validation_with_format_error
                                                — unknown slug is an
                                                  identifier-miss, NOT an
                                                  empty-filter; exit 2 + Hint
- test_status_rejects_slug_with_all_flag      — `<slug> --all` mutex → exit 2
- test_status_default_most_recent_active      — no args + no flag → most recently active
- test_status_all_includes_terminal           — `--all` includes ARCHIVED/ABANDONED
- test_status_json_envelope_schema            — `--json` shape: envelope.data.changes[]
"""
import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main
from super_harness.core.review_verdict import read_change_events


def _init(tmp_path: Path) -> None:
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "init"])


def _start(tmp_path: Path, slug: str) -> None:
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "change", "start", slug])


def _abandon(tmp_path: Path, slug: str) -> None:
    CliRunner().invoke(main, ["--workspace", str(tmp_path), "change", "abandon", slug])


def test_status_no_harness_exits_3(tmp_path: Path) -> None:
    """No `.harness/` → HarnessNotInitialized → exit 3 (EXIT_NO_CONFIG)."""
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "status"])
    assert r.exit_code == 3


def test_status_with_slug_shows_change(tmp_path: Path) -> None:
    """`status <slug>` renders that single change's current_state line."""
    _init(tmp_path)
    _start(tmp_path, "ch-alpha")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "status", "ch-alpha"]
    )
    assert r.exit_code == 0
    assert "ch-alpha" in r.output
    assert "INTENT_DECLARED" in r.output


def test_status_unknown_slug_exits_validation_with_format_error(tmp_path: Path) -> None:
    """Identifier semantics: unknown slug → EXIT_VALIDATION + actionable stderr.

    `status <slug>` is an identifier query (like `change resume <slug>`, `git
    show <sha>`, `docker inspect <name>`, `kubectl get pod <name>`, `gh pr view
    <num>`): naming a specific missing object is a user error, not "filter
    matched nothing". This contrasts with `change list` (filter command),
    which still returns exit 0 + empty result.

    Verifies the canonical format_error shape lands on stderr: subcommand
    prefix + the offending slug + a Hint line pointing at recovery.
    """
    _init(tmp_path)
    _start(tmp_path, "ch-known")
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "status", "no-such-slug"],
    )
    assert r.exit_code == 2  # EXIT_VALIDATION — same as `change resume <unknown>`
    assert "super-harness status:" in r.stderr
    assert "unknown change slug" in r.stderr
    assert "no-such-slug" in r.stderr
    assert "Hint:" in r.stderr


def test_status_rejects_slug_with_all_flag(tmp_path: Path) -> None:
    """`status <slug> --all` is mutex-incoherent → EXIT_VALIDATION with hint.

    Symmetric with `change list --active --archived` (also exit 2): the user
    asked for one specific change AND every change simultaneously, which can't
    both be honored. Previously, `--all` silently shadowed the slug.
    """
    _init(tmp_path)
    r = CliRunner().invoke(
        main,
        ["--workspace", str(tmp_path), "status", "any-slug", "--all"],
    )
    assert r.exit_code == 2  # EXIT_VALIDATION
    assert "cannot be combined" in r.stderr
    assert "Hint:" in r.stderr


def test_status_default_most_recent_active(tmp_path: Path) -> None:
    """No args + no `--all` → fall back to the MOST RECENTLY active change.

    Two non-terminal changes: the fallback resolves the one most recently active
    (later `last_event_at`), not the oldest — so a stale, earlier change can't
    hijack the resolution (HG-STALE-MERGED-CHANGE).
    """
    _init(tmp_path)
    _start(tmp_path, "ch-first")
    _start(tmp_path, "ch-second")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "status"]
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    changes = payload["data"]["changes"]
    assert len(changes) == 1
    # ch-second was started later → later last_event_at → it is the active one.
    assert changes[0]["change_id"] == "ch-second"


def test_status_all_includes_terminal(tmp_path: Path) -> None:
    """`--all` returns every change, including ABANDONED (terminal state)."""
    _init(tmp_path)
    _start(tmp_path, "ch-active")
    _start(tmp_path, "ch-doomed")
    _abandon(tmp_path, "ch-doomed")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "status", "--all"]
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    slugs = {c["change_id"] for c in payload["data"]["changes"]}
    assert slugs == {"ch-active", "ch-doomed"}
    # Verify the terminal one is actually marked ABANDONED (proves `--all`
    # isn't just "active + something else"; it's truly everything).
    by_id = {c["change_id"]: c for c in payload["data"]["changes"]}
    assert by_id["ch-doomed"]["current_state"] == "ABANDONED"


def test_status_json_envelope_schema(tmp_path: Path) -> None:
    """`--json` emits the standard 6-key envelope; `data.changes` is a list of dicts."""
    _init(tmp_path)
    _start(tmp_path, "ch-only")
    r = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "status", "ch-only"]
    )
    assert r.exit_code == 0
    payload = json.loads(r.output)
    assert payload["command"] == "status"
    assert payload["status"] == "pass"
    assert payload["exit_code"] == 0
    assert payload["errors"] == []
    assert isinstance(payload["data"]["changes"], list)
    assert len(payload["data"]["changes"]) == 1
    entry = payload["data"]["changes"][0]
    # ChangeState fields the contract leans on (per cli/status.py text format)
    assert entry["change_id"] == "ch-only"
    assert entry["current_state"] == "INTENT_DECLARED"
    assert entry["last_event_type"] == "intent_declared"
    assert entry["last_event_at"]


def test_status_shows_next_step_for_blocking_state(tmp_path: Path) -> None:
    """A change in a blocking state surfaces a `next:` step from SUGGESTIONS.

    After `change start`, the change is INTENT_DECLARED (blocking), so the
    human render carries a `next:` line and the `--json` entry a `next` key,
    both holding SUGGESTIONS["INTENT_DECLARED"]. The block-message redirect
    from Task 3 ("run `super-harness status` for the next valid step") lands
    on a real next step, not a dead end.
    """
    _init(tmp_path)
    _start(tmp_path, "ch1")
    human = CliRunner().invoke(main, ["--workspace", str(tmp_path), "status", "ch1"])
    assert human.exit_code == 0, human.output
    assert "next:" in human.output.lower()
    assert "Draft a plan" in human.output  # from SUGGESTIONS["INTENT_DECLARED"]
    js = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "status", "ch1"]
    )
    assert js.exit_code == 0, js.output
    changes = json.loads(js.output)["data"]["changes"]
    assert any("Draft a plan" in str(e.get("next", "")) for e in changes)


# --- HG-02.C: status surfaces the reviewer strategy in review states ----------


def _seed_awaiting_plan_review(tmp_path: Path, slug: str) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    w = EventWriter(events_path(tmp_path))
    for t in ("intent_declared", "plan_ready"):
        w.emit(
            Event(
                event_id=new_event_id(), type=t, change_id=slug,
                timestamp="2026-06-02T00:00:00Z",
                actor=Actor(type="human", identifier="cli"),
                framework="plain", payload={},
            )
        )
    refresh_state_after_emit(tmp_path)


def _seed_awaiting_code_review(tmp_path: Path, slug: str) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    w = EventWriter(events_path(tmp_path))
    for t in (
        "intent_declared",
        "plan_ready",
        "plan_approved",
        "implementation_started",
        "verification_passed",
        "implementation_complete",
    ):
        w.emit(
            Event(
                event_id=new_event_id(), type=t, change_id=slug,
                timestamp="2026-06-02T00:00:00Z",
                actor=Actor(type="human", identifier="cli"),
                framework="plain", payload={},
            )
        )
    refresh_state_after_emit(tmp_path)


def _set_strategy(tmp_path: Path, reviewer: str, strategy: str) -> None:
    assert reviewer == "plan-reviewer"
    assert strategy == "human"


def _set_independent_policy(tmp_path: Path) -> None:
    (tmp_path / ".harness" / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  sources:\n"
        "    subagent:\n"
        "      kind: automated\n"
        "    external:\n"
        "      kind: automated\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        "      participants: [subagent, external]\n"
        "      min_independent: 2\n"
        "    code-reviewer:\n"
        "      participants: [subagent, external]\n"
        "      min_independent: 2\n"
    )
    (tmp_path / ".harness" / "review-profiles.local.yaml").write_text(
        "version: 1\n"
        "sources:\n"
        "  subagent:\n"
        "    protocol: claude-cli\n"
        "    model: claude-review\n"
        "    agent_options:\n"
        "      effort: medium\n"
        "  external:\n"
        "    protocol: codex-cli\n"
        "    model: gpt-review\n"
        "    agent_options:\n"
        "      reasoning_effort: medium\n"
        "      sandbox: read-only\n"
    )


def _set_code_review_independent_policy(tmp_path: Path) -> None:
    _set_independent_policy(tmp_path)


def _record_partial_review(tmp_path: Path, slug: str, source: str) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    events = read_change_events(events_path(tmp_path), slug)
    epoch_id = next(event.event_id for event in reversed(events) if event.type == "plan_ready")
    writer = EventWriter(events_path(tmp_path))
    writer.emit(
        Event(
            event_id=new_event_id(),
            type="review_round_started",
            change_id=slug,
            timestamp="2026-06-02T00:00:01Z",
            actor=Actor(type="agent", identifier="cli"),
            framework="plain",
            payload={
                "reviewer": "plan-reviewer",
                "epoch_id": epoch_id,
                "round_id": "round-1",
                "contract_digest": "current-contract",
                "target_head": "current-head",
                "profile_digest": "current-profiles",
                "runs": [{
                    "run_id": "run-1",
                    "source": source,
                    "protocol": "claude-cli",
                    "requested_model": "claude-review",
                    "requested_options": {"effort": "medium"},
                }],
            },
        )
    )
    writer.emit(
        Event(
            event_id=new_event_id(),
            type="review_result_imported",
            change_id=slug,
            timestamp="2026-06-02T00:00:02Z",
            actor=Actor(type="agent", identifier=source),
            framework="plain",
            payload={
                "reviewer": "plan-reviewer",
                "epoch_id": epoch_id,
                "round_id": "round-1",
                "run_id": "run-1",
                "source": source,
                "contract_digest": "current-contract",
                "target_head": "current-head",
                "result_digest": "result-1",
                "verdict": {"checklist": [], "findings": []},
                "receipt": {"actual_model": None},
            },
        )
    )
    packet_dir = tmp_path / ".harness" / "pending-reviews" / slug / "plan-reviewer"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "draft.packet.json").write_text(
        json.dumps({
            "contract_digest": "current-contract",
            "target_head": "current-head",
            "profile_digest": "current-profiles",
        }) + "\n"
    )
    refresh_state_after_emit(tmp_path)


def _record_code_review_partial(tmp_path: Path, slug: str, source: str, digest: str) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    events = read_change_events(events_path(tmp_path), slug)
    epoch_id = next(
        event.event_id for event in reversed(events) if event.type == "implementation_complete"
    )
    writer = EventWriter(events_path(tmp_path))
    writer.emit(
        Event(
            event_id=new_event_id(),
            type="review_round_started",
            change_id=slug,
            timestamp="2026-06-02T00:00:01Z",
            actor=Actor(type="agent", identifier="cli"),
            framework="plain",
            payload={
                "reviewer": "code-reviewer",
                "epoch_id": epoch_id,
                "round_id": "round-code-1",
                "contract_digest": digest,
                "target_head": "old-head",
                "profile_digest": "old-profiles",
                "runs": [{
                    "run_id": "run-code-1",
                    "source": source,
                    "protocol": "claude-cli",
                    "requested_model": "claude-review",
                    "requested_options": {},
                }],
            },
        )
    )
    writer.emit(
        Event(
            event_id=new_event_id(),
            type="review_result_imported",
            change_id=slug,
            timestamp="2026-06-02T00:00:02Z",
            actor=Actor(type="agent", identifier=source),
            framework="plain",
            payload={
                "reviewer": "code-reviewer",
                "epoch_id": epoch_id,
                "round_id": "round-code-1",
                "run_id": "run-code-1",
                "source": source,
                "contract_digest": digest,
                "target_head": "old-head",
                "result_digest": "old-result",
                "verdict": {"checklist": [], "findings": []},
                "receipt": {},
            },
        )
    )
    refresh_state_after_emit(tmp_path)


def _write_code_review_bundle(tmp_path: Path, slug: str, digest: str) -> None:
    bundle_dir = tmp_path / ".harness" / "pending-reviews" / slug / "code-reviewer"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "draft.packet.json").write_text(
        json.dumps({
            "contract_digest": digest,
            "target_head": "new-head",
            "profile_digest": "new-profiles",
        }) + "\n"
    )


def test_status_shows_reviewer_strategy_in_review_state(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_awaiting_plan_review(tmp_path, "demo")  # → AWAITING_PLAN_REVIEW
    _set_strategy(tmp_path, "plan-reviewer", "human")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "status", "demo"])
    assert r.exit_code == 0, r.output
    assert "plan-reviewer" in r.output
    assert "human" in r.output


def test_status_no_strategy_line_outside_review_state(tmp_path: Path) -> None:
    _init(tmp_path)
    _start(tmp_path, "demo")  # INTENT_DECLARED — not a review state
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "status", "demo"])
    assert r.exit_code == 0, r.output
    assert "strategy" not in r.output.lower()


def test_status_json_carries_human_review_protocol(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_awaiting_plan_review(tmp_path, "demo")
    _set_strategy(tmp_path, "plan-reviewer", "human")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "status", "demo"])
    assert r.exit_code == 0, r.output
    entry = json.loads(r.stdout)["data"]["changes"][0]
    assert entry["reviewer"] == "plan-reviewer"
    progress = entry["review_progress"]
    assert progress["required_sources"] == ["human"]
    assert "review prepare" in progress["next_command"]


def test_status_shows_independent_review_progress(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_awaiting_plan_review(tmp_path, "demo")
    _set_independent_policy(tmp_path)
    _record_partial_review(tmp_path, "demo", "subagent")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "status", "demo"])
    assert r.exit_code == 0, r.output
    assert "review progress:" in r.output
    assert "1/2 imported source(s)" in r.output
    assert "imported: subagent" in r.output
    assert "remaining: external" in r.output
    assert "protocol: codex-cli" in r.output
    assert "model: gpt-review" in r.output
    assert "agent_options: reasoning_effort=medium, sandbox=read-only" in r.output


def test_status_json_carries_independent_review_progress(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_awaiting_plan_review(tmp_path, "demo")
    _set_independent_policy(tmp_path)
    _record_partial_review(tmp_path, "demo", "subagent")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "status", "demo"])
    assert r.exit_code == 0, r.output
    progress = json.loads(r.output)["data"]["changes"][0]["review_progress"]
    assert progress["reviewer"] == "plan-reviewer"
    assert progress["min_independent"] == 2
    assert progress["required_sources"] == ["subagent", "external"]
    assert progress["imported_sources"] == ["subagent"]
    assert progress["pending_sources"] == []
    assert progress["automatic_rounds_used"] == 1
    assert progress["automatic_rounds_remaining"] == 1
    assert progress["source_profiles"]["external"] == {
        "kind": "automated",
        "protocol": "codex-cli",
        "model": "gpt-review",
        "cost_class": "standard",
        "agent_options": {"reasoning_effort": "medium", "sandbox": "read-only"},
    }


def test_status_shows_profile_even_without_source_instructions(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_awaiting_plan_review(tmp_path, "demo")
    (tmp_path / ".harness" / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  sources:\n"
        "    subagent:\n"
        "      kind: automated\n"
        "    aux:\n"
        "      kind: automated\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        "      participants: [subagent, aux]\n"
        "      min_independent: 2\n"
        "    code-reviewer:\n"
        "      participants: [subagent, aux]\n"
        "      min_independent: 2\n"
    )
    (tmp_path / ".harness" / "review-profiles.local.yaml").write_text(
        "version: 1\n"
        "sources:\n"
        "  subagent:\n"
        "    protocol: claude-cli\n"
        "    model: claude-review\n"
        "    agent_options: {effort: medium}\n"
        "  aux:\n"
        "    protocol: codex-cli\n"
        "    model: gpt-review\n"
        "    agent_options: {reasoning_effort: medium, sandbox: read-only}\n"
    )
    _record_partial_review(tmp_path, "demo", "subagent")

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "status", "demo"])

    assert r.exit_code == 0, r.output
    assert "remaining: aux" in r.output
    assert "    aux:" in r.output
    assert "protocol: codex-cli" in r.output
    assert "model: gpt-review" in r.output
    assert "agent_options: reasoning_effort=medium, sandbox=read-only" in r.output


def test_status_code_review_progress_ignores_stale_digest_partial(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_awaiting_code_review(tmp_path, "demo")
    _set_code_review_independent_policy(tmp_path)
    _record_code_review_partial(tmp_path, "demo", "subagent", "old-digest")
    _write_code_review_bundle(tmp_path, "demo", "new-digest")

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "status", "demo"])

    assert r.exit_code == 0, r.output
    progress = json.loads(r.output)["data"]["changes"][0]["review_progress"]
    assert progress["reviewer"] == "code-reviewer"
    assert progress["imported_sources"] == []
    assert progress["stale_sources"] == ["subagent"]
    assert progress["required_sources"] == ["subagent", "external"]


def _write_exhausted_review_governance(
    tmp_path: Path, *, participants: str = "[codex, claude]"
) -> None:
    human_role = "human" in participants
    (tmp_path / ".harness" / "review-governance.yaml").write_text(
        "version: 1\n"
        "review:\n"
        "  sources:\n"
        "    codex:\n"
        "      kind: automated\n"
        "    claude:\n"
        "      kind: automated\n"
        "    human:\n"
        "      kind: human\n"
        "    observer:\n"
        "      kind: human\n"
        "  roles:\n"
        "    plan-reviewer:\n"
        "      participants: [human]\n"
        "      min_independent: 1\n"
        "    code-reviewer:\n"
        f"      participants: {participants}\n"
        "      min_independent: 2\n"
        f"      max_automatic_rounds_per_epoch: {1 if human_role else 2}\n",
        encoding="utf-8",
    )
    (tmp_path / ".harness" / "review-profiles.local.yaml").write_text(
        "version: 1\n"
        "sources:\n"
        "  codex:\n"
        "    protocol: codex-cli\n"
        "    model: gpt-review\n"
        "    agent_options: {reasoning_effort: medium, sandbox: read-only}\n"
        + (
            "  claude:\n"
            "    protocol: claude-cli\n"
            "    model: claude-review\n"
            "    agent_options: {effort: medium}\n"
            if not human_role
            else ""
        ),
        encoding="utf-8",
    )


def _record_review_round(
    tmp_path: Path,
    slug: str,
    *,
    round_id: str,
    contract_digest: str,
    target_head: str,
    profile_digest: str,
    include_failed_claude: bool,
) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    events = read_change_events(events_path(tmp_path), slug)
    epoch_id = next(
        event.event_id for event in reversed(events) if event.type == "implementation_complete"
    )
    writer = EventWriter(events_path(tmp_path))
    runs = [
        {
            "run_id": f"{round_id}-codex",
            "source": "codex",
            "protocol": "codex-cli",
            "requested_model": "gpt-review",
            "requested_options": {},
        }
    ]
    if include_failed_claude:
        runs.append(
            {
                "run_id": f"{round_id}-claude",
                "source": "claude",
                "protocol": "claude-cli",
                "requested_model": "claude-review",
                "requested_options": {},
            }
        )
    common = {
        "reviewer": "code-reviewer",
        "epoch_id": epoch_id,
        "round_id": round_id,
        "contract_digest": contract_digest,
        "target_head": target_head,
        "profile_digest": profile_digest,
    }
    writer.emit(
        Event(
            event_id=new_event_id(),
            type="review_round_started",
            change_id=slug,
            timestamp="2026-06-02T00:00:01Z",
            actor=Actor(type="agent", identifier="cli"),
            framework="plain",
            payload={**common, "runs": runs},
        )
    )
    writer.emit(
        Event(
            event_id=new_event_id(),
            type="review_result_imported",
            change_id=slug,
            timestamp="2026-06-02T00:00:02Z",
            actor=Actor(type="agent", identifier="codex"),
            framework="plain",
            payload={
                **common,
                "run_id": f"{round_id}-codex",
                "source": "codex",
                "result_digest": f"{round_id}-result",
                "verdict": {
                    "scope_sufficient": True,
                    "checklist": [],
                    "findings": [],
                },
                "receipt": {},
            },
        )
    )
    if include_failed_claude:
        writer.emit(
            Event(
                event_id=new_event_id(),
                type="review_run_failed",
                change_id=slug,
                timestamp="2026-06-02T00:00:03Z",
                actor=Actor(type="agent", identifier="cli"),
                framework="plain",
                payload={
                    **common,
                    "run_id": f"{round_id}-claude",
                    "source": "claude",
                    "reason": "subscription unavailable",
                },
            )
        )
    writer.emit(
        Event(
            event_id=new_event_id(),
            type="review_round_closed",
            change_id=slug,
            timestamp="2026-06-02T00:00:04Z",
            actor=Actor(type="agent", identifier="cli"),
            framework="plain",
            payload={**common, "outcome": "execution_failed"},
        )
    )
    refresh_state_after_emit(tmp_path)


def _write_current_code_packet(tmp_path: Path, slug: str) -> None:
    packet_dir = tmp_path / ".harness" / "pending-reviews" / slug / "code-reviewer"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "draft.packet.json").write_text(
        json.dumps(
            {
                "contract_digest": "current-contract",
                "target_head": "current-head",
                "profile_digest": "current-profiles",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_status_current_imported_source_is_not_also_stale_and_exhaustion_is_actionable(
    tmp_path: Path,
) -> None:
    _init(tmp_path)
    _seed_awaiting_code_review(tmp_path, "demo")
    _write_exhausted_review_governance(tmp_path)
    _record_review_round(
        tmp_path,
        "demo",
        round_id="old-round",
        contract_digest="old-contract",
        target_head="old-head",
        profile_digest="old-profiles",
        include_failed_claude=False,
    )
    _record_review_round(
        tmp_path,
        "demo",
        round_id="current-round",
        contract_digest="current-contract",
        target_head="current-head",
        profile_digest="current-profiles",
        include_failed_claude=True,
    )
    _write_current_code_packet(tmp_path, "demo")

    result = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "status", "demo"]
    )

    assert result.exit_code == 0, result.output
    progress = json.loads(result.output)["data"]["changes"][0]["review_progress"]
    assert progress["imported_sources"] == ["codex"]
    assert progress["retained_sources"] == ["codex"]
    assert progress["stale_sources"] == []
    assert progress["automatic_rounds_remaining"] == 0
    assert "restore failed source(s): claude" in progress["next_command"]
    assert "review authorize demo --reviewer code-reviewer" in progress["next_command"]
    assert "review begin demo --reviewer code-reviewer --source claude" in progress[
        "next_command"
    ]
    assert "review skip demo --reviewer code-reviewer --override" in progress[
        "next_command"
    ]
    assert "human-only" in progress["next_command"]
    assert "review human inspect" not in progress["next_command"]


def test_status_invalidates_retained_receipts_when_packet_target_is_not_current_head(
    tmp_path: Path,
) -> None:
    _init(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "status-test@example.com"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Status Test"], cwd=tmp_path, check=True
    )
    (tmp_path / "tracked.txt").write_text("current\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "current head"], cwd=tmp_path, check=True)
    current_head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    _seed_awaiting_code_review(tmp_path, "demo")
    _write_exhausted_review_governance(tmp_path)
    _record_review_round(
        tmp_path,
        "demo",
        round_id="old-round",
        contract_digest="old-contract",
        target_head="old-head",
        profile_digest="old-profiles",
        include_failed_claude=True,
    )
    packet_dir = tmp_path / ".harness" / "pending-reviews" / "demo" / "code-reviewer"
    packet_dir.mkdir(parents=True, exist_ok=True)
    (packet_dir / "draft.packet.json").write_text(
        json.dumps(
            {
                "contract_digest": "old-contract",
                "target_head": "old-head",
                "profile_digest": "old-profiles",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "status", "demo"]
    )

    assert result.exit_code == 0, result.output
    progress = json.loads(result.output)["data"]["changes"][0]["review_progress"]
    assert progress["imported_sources"] == []
    assert progress["pending_sources"] == []
    assert progress["failed_sources"] == []
    assert progress["retained_sources"] == []
    assert progress["stale_sources"] == ["codex"]
    assert progress["packet"]["target_head"] == "old-head"
    assert progress["packet"]["current_head"] == current_head
    assert progress["packet"]["stale"] is True
    assert progress["next_command"] == (
        "super-harness review prepare demo --reviewer code-reviewer"
    )

    rendered = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "status", "demo"]
    )
    assert rendered.exit_code == 0, rendered.output
    assert f"packet target: old-head (current: {current_head}; stale)" in rendered.output
    assert "retained: codex" not in rendered.output
    assert "failed: claude" not in rendered.output
    assert "stale: codex" in rendered.output
    assert (
        "review next: super-harness review prepare demo --reviewer code-reviewer"
        in rendered.output
    )


def test_status_exhaustion_recommends_human_path_only_for_role_participant(
    tmp_path: Path,
) -> None:
    _init(tmp_path)
    _seed_awaiting_code_review(tmp_path, "demo")
    _write_exhausted_review_governance(tmp_path, participants="[codex, human]")
    _record_review_round(
        tmp_path,
        "demo",
        round_id="current-round",
        contract_digest="current-contract",
        target_head="current-head",
        profile_digest="current-profiles",
        include_failed_claude=False,
    )
    _write_current_code_packet(tmp_path, "demo")

    result = CliRunner().invoke(
        main, ["--workspace", str(tmp_path), "--json", "status", "demo"]
    )

    assert result.exit_code == 0, result.output
    next_command = json.loads(result.output)["data"]["changes"][0][
        "review_progress"
    ]["next_command"]
    assert (
        "review human inspect demo --reviewer code-reviewer --source human --pager"
        in next_command
    )
    assert "review skip --override" not in next_command
