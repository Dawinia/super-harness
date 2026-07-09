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
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main


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
    (tmp_path / ".harness" / "policy.yaml").write_text(
        f"reviewers:\n  {reviewer}:\n    strategy: {strategy}\n"
    )


def _set_independent_policy(tmp_path: Path) -> None:
    (tmp_path / ".harness" / "policy.yaml").write_text(
        "reviewers:\n"
        "  sources:\n"
        "    subagent: {}\n"
        "    external:\n"
        "      agent: codex\n"
        "      context: bundle-only\n"
        "      instructions: Run external verifier.\n"
        "      agent_options:\n"
        "        reasoning_effort: medium\n"
        "        sandbox: read-only\n"
        "  plan-reviewer:\n"
        "    strategy: subagent\n"
        "    min_independent: 2\n"
    )


def _set_code_review_independent_policy(tmp_path: Path) -> None:
    (tmp_path / ".harness" / "policy.yaml").write_text(
        "reviewers:\n"
        "  sources:\n"
        "    subagent: {}\n"
        "    external: {}\n"
        "  code-reviewer:\n"
        "    strategy: subagent\n"
        "    min_independent: 2\n"
    )


def _record_partial_review(tmp_path: Path, slug: str, source: str) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    EventWriter(events_path(tmp_path)).emit(
        Event(
            event_id=new_event_id(),
            type="review_verdict_recorded",
            change_id=slug,
            timestamp="2026-06-02T00:00:01Z",
            actor=Actor(type="human", identifier="cli"),
            framework="plain",
            payload={
                "reviewer": "plan-reviewer",
                "reason": "approved",
                "source": source,
                "outcome": "approved",
            },
        )
    )
    refresh_state_after_emit(tmp_path)


def _record_code_review_partial(tmp_path: Path, slug: str, source: str, digest: str) -> None:
    from super_harness.core.events import Actor, Event
    from super_harness.core.paths import events_path
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    EventWriter(events_path(tmp_path)).emit(
        Event(
            event_id=new_event_id(),
            type="review_verdict_recorded",
            change_id=slug,
            timestamp="2026-06-02T00:00:01Z",
            actor=Actor(type="human", identifier="cli"),
            framework="plain",
            payload={
                "reviewer": "code-reviewer",
                "reason": "approved",
                "source": source,
                "outcome": "approved",
                "verdict": {"bundle_digest": digest},
            },
        )
    )
    refresh_state_after_emit(tmp_path)


def _write_code_review_bundle(tmp_path: Path, slug: str, digest: str) -> None:
    bundle_dir = tmp_path / ".harness" / "pending-reviews" / slug
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "code-reviewer.bundle.json").write_text(
        json.dumps({"bundle_digest": digest}) + "\n"
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


def test_status_json_carries_reviewer_strategy(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_awaiting_plan_review(tmp_path, "demo")
    _set_strategy(tmp_path, "plan-reviewer", "human")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "status", "demo"])
    assert r.exit_code == 0, r.output
    entry = json.loads(r.stdout)["data"]["changes"][0]
    assert entry["reviewer"] == "plan-reviewer"
    assert entry["reviewer_strategy"] == "human"


def test_status_shows_independent_review_progress(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_awaiting_plan_review(tmp_path, "demo")
    _set_independent_policy(tmp_path)
    _record_partial_review(tmp_path, "demo", "subagent")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "status", "demo"])
    assert r.exit_code == 0, r.output
    assert "review progress:" in r.output
    assert "1/2 independent source(s)" in r.output
    assert "accepted: subagent" in r.output
    assert "remaining: external" in r.output
    assert "Run external verifier." in r.output
    assert "agent: codex" in r.output
    assert "context: bundle-only" in r.output
    assert "agent_options: reasoning_effort=medium, sandbox=read-only" in r.output


def test_status_json_carries_independent_review_progress(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_awaiting_plan_review(tmp_path, "demo")
    _set_independent_policy(tmp_path)
    _record_partial_review(tmp_path, "demo", "subagent")
    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "--json", "status", "demo"])
    assert r.exit_code == 0, r.output
    progress = json.loads(r.output)["data"]["changes"][0]["review_progress"]
    assert progress == {
        "reviewer": "plan-reviewer",
        "min_independent": 2,
        "accepted_sources": ["subagent"],
        "missing_independent": 1,
        "remaining_sources": ["external"],
        "instructions": {"external": "Run external verifier."},
        "source_profiles": {
            "external": {
                "instructions": "Run external verifier.",
                "agent": "codex",
                "context": "bundle-only",
                "agent_options": {"reasoning_effort": "medium", "sandbox": "read-only"},
            },
        },
    }


def test_status_shows_profile_even_without_source_instructions(tmp_path: Path) -> None:
    _init(tmp_path)
    _seed_awaiting_plan_review(tmp_path, "demo")
    (tmp_path / ".harness" / "policy.yaml").write_text(
        "reviewers:\n"
        "  sources:\n"
        "    subagent: {}\n"
        "    aux:\n"
        "      agent: custom-runner\n"
        "      context: incremental\n"
        "      agent_options:\n"
        "        effort: medium\n"
        "  plan-reviewer:\n"
        "    min_independent: 2\n"
    )
    _record_partial_review(tmp_path, "demo", "subagent")

    r = CliRunner().invoke(main, ["--workspace", str(tmp_path), "status", "demo"])

    assert r.exit_code == 0, r.output
    assert "remaining: aux" in r.output
    assert "    aux:" in r.output
    assert "agent: custom-runner" in r.output
    assert "context: incremental" in r.output
    assert "agent_options: effort=medium" in r.output


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
    assert progress["accepted_sources"] == []
    assert progress["missing_independent"] == 2
    assert progress["remaining_sources"] == ["subagent", "external"]
