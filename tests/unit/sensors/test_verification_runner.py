from __future__ import annotations

import dataclasses
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from super_harness.core.events import Actor, Event
from super_harness.core.paths import events_path
from super_harness.core.ulid import new_event_id
from super_harness.core.writer import EventWriter
from super_harness.engineering.verification_config import (
    CheckSpec,
    Defaults,
    Execution,
    Layers,
    VerificationConfig,
)
from super_harness.sensors import Activity, WorkspaceContext
from super_harness.sensors.registry import get_builtin, list_builtins
from super_harness.sensors.verification_runner import (
    BASELINE_CHECK_IDS,
    CheckResult,
    CheckTask,
    VerificationRunner,
    _all_pass_must,
    _baseline_lifecycle_ordering,
    _baseline_scope_vs_plan,
    _covered_by_scope,
    _scrubbed_environ,
    baseline_check_tasks,
    build_variables,
    collect_checks,
    collectable_check_ids,
    make_verification_event,
    run_check,
    run_checks,
    verify_data_block,
    write_summary_json,
)

# subprocess.run(env=...) REPLACES the environment, so any command resolved via
# PATH (sleep, ls, python …) needs PATH present. See run_check docstring.
# (`true`/`false` are shell builtins and need no PATH, but we keep PATH for the
# rest.)
_ENV: dict[str, str] = {"PATH": os.environ["PATH"]}


def _spec(
    *,
    command: str,
    check_id: str = "c",
    must_pass: bool = True,
    timeout_seconds: int = 30,
    capture: str = "both",
    workdir: str = ".",
    env: dict[str, str] | None = None,
) -> CheckSpec:
    return CheckSpec(
        id=check_id,
        command=command,
        must_pass=must_pass,
        timeout_seconds=timeout_seconds,
        capture=capture,
        workdir=workdir,
        env=env if env is not None else {},
    )


def test_pass_zero_exit(tmp_path: Path) -> None:
    res = run_check(
        _spec(command="true", capture="none"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=tmp_path / "arch",
        variables={},
    )
    assert res.status == "pass"
    assert res.exit_code == 0
    assert res.must_pass is True


def test_fail_nonzero_exit(tmp_path: Path) -> None:
    res = run_check(
        _spec(command="false", capture="none", must_pass=False),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=tmp_path / "arch",
        variables={},
    )
    assert res.status == "fail"
    assert res.exit_code == 1
    assert res.must_pass is False


def test_timeout(tmp_path: Path) -> None:
    res = run_check(
        _spec(command="sleep 5", timeout_seconds=1, capture="both"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=tmp_path / "arch",
        variables={},
    )
    assert res.status == "timeout"
    assert res.exit_code == -1
    assert res.must_pass is True
    # command is still populated on timeout; output_path is None (nothing archived).
    assert res.command == "sleep 5"
    assert res.output_path is None
    # No archive files written on timeout.
    assert not (tmp_path / "arch" / "c.stdout").exists()
    assert not (tmp_path / "arch" / "c.stderr").exists()


def test_capture_stdout_only(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    res = run_check(
        _spec(command="echo hello", capture="stdout"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert res.status == "pass"
    assert (archive / "c.stdout").read_text() == "hello\n"
    assert not (archive / "c.stderr").exists()
    assert res.output_path == str(archive / "c.stdout")


def test_capture_stderr_only(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    res = run_check(
        _spec(command="echo oops 1>&2", capture="stderr"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert (archive / "c.stderr").read_text() == "oops\n"
    assert not (archive / "c.stdout").exists()
    assert res.output_path == str(archive / "c.stderr")


def test_capture_both(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    res = run_check(
        _spec(command="echo out; echo err 1>&2", capture="both"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert (archive / "c.stdout").read_text() == "out\n"
    assert (archive / "c.stderr").read_text() == "err\n"
    assert res.output_path == str(archive)


def test_capture_none(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    res = run_check(
        _spec(command="echo hello", capture="none"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert res.status == "pass"
    assert not (archive / "c.stdout").exists()
    assert not (archive / "c.stderr").exists()
    assert res.output_path is None


def test_interpolation_applied(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    res = run_check(
        _spec(command="echo ${SLUG}", capture="stdout"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={"SLUG": "x"},
    )
    assert (archive / "c.stdout").read_text() == "x\n"
    # command field holds the INTERPOLATED string actually run.
    assert res.command == "echo x"


def test_duration_ms_is_nonneg_int(tmp_path: Path) -> None:
    res = run_check(
        _spec(command="true", capture="none"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=tmp_path / "arch",
        variables={},
    )
    assert isinstance(res.duration_ms, int)
    assert res.duration_ms >= 0


def test_command_field_holds_interpolated(tmp_path: Path) -> None:
    res = run_check(
        _spec(command="echo ${CHANGE_ID}", capture="none"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=tmp_path / "arch",
        variables={"CHANGE_ID": "abc-123"},
    )
    assert res.command == "echo abc-123"


def test_workdir_is_used(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "marker.txt").write_text("here")
    res = run_check(
        _spec(command="ls", capture="stdout"),
        workdir=sub,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert "marker.txt" in (archive / "c.stdout").read_text()
    assert res.status == "pass"


def test_archive_dir_created_when_missing(tmp_path: Path) -> None:
    archive = tmp_path / "nested" / "deep" / "arch"
    assert not archive.exists()
    run_check(
        _spec(command="echo hi", capture="stdout"),
        workdir=tmp_path,
        env=_ENV,
        archive_dir=archive,
        variables={},
    )
    assert (archive / "c.stdout").exists()


def test_run_check_env_replaces_not_layers(tmp_path: Path) -> None:
    # ENV-REPLACEMENT contract: subprocess.run(env=...) REPLACES the child
    # environment, it does NOT layer on top of os.environ. A sentinel var that
    # lives in os.environ but is ABSENT from the passed `env` must be invisible
    # to the child. (We keep PATH in `env` so `echo` still resolves.)
    archive = tmp_path / "arch"
    sentinel = "SUPER_HARNESS_ENV_REPLACE_SENTINEL"
    os.environ[sentinel] = "leaked"
    try:
        # The f-string `${sentinel}` is a PYTHON field → expands to a bare,
        # UNBRACED `$SUPER_HARNESS_...` shell var. interpolate() only touches
        # BRACED `${NAME}` placeholders, so it leaves this untouched and the
        # SHELL expands it — which is exactly what we want to probe. printf %s
        # emits the value verbatim with no trailing newline (and, unlike
        # `echo -n`, behaves consistently across /bin/sh variants).
        res = run_check(
            _spec(command=f'printf "%s" "${sentinel}"', capture="stdout"),
            workdir=tmp_path,
            env={"PATH": os.environ["PATH"]},  # sentinel deliberately omitted
            archive_dir=archive,
            variables={},
        )
    finally:
        del os.environ[sentinel]
    assert res.status == "pass"
    # The sentinel expands to empty in the child → archived output is empty.
    assert (archive / "c.stdout").read_text() == ""


def test_check_result_is_frozen() -> None:
    res = CheckResult(
        id="c",
        status="pass",
        exit_code=0,
        duration_ms=1,
        must_pass=True,
        command="echo hi",
        output_path=None,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        res.status = "fail"  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Task 8.4 — runner / sensor
# --------------------------------------------------------------------------- #


def _config(
    *,
    checks: list[CheckSpec] | None = None,
    adapter_provided: list[CheckSpec] | None = None,
    layers: Layers | None = None,
    execution: Execution | None = None,
    defaults: Defaults | None = None,
) -> VerificationConfig:
    return VerificationConfig(
        layers=layers if layers is not None else Layers(),
        defaults=defaults if defaults is not None else Defaults(),
        execution=execution if execution is not None else Execution(),
        checks=checks if checks is not None else [],
        adapter_provided=adapter_provided if adapter_provided is not None else [],
    )


def _ctx(root: Path) -> WorkspaceContext:
    return WorkspaceContext(workspace_root=root, active_change_id=None)


def _result(check_id: str, status: str, *, must_pass: bool = True) -> CheckResult:
    return CheckResult(
        id=check_id,
        status=status,  # type: ignore[arg-type]
        exit_code=0 if status == "pass" else 1,
        duration_ms=1,
        must_pass=must_pass,
        command=f"echo {check_id}",
        output_path=None,
    )


# --- build_variables --------------------------------------------------------


def test_build_variables_aliases_and_empty_paths(tmp_path: Path) -> None:
    # No framework on the context → spec/plan paths empty (the None branch).
    v = build_variables("my-change", _ctx(tmp_path))
    assert v == {
        "SLUG": "my-change",
        "CHANGE_ID": "my-change",
        "SPEC_PATH": "",
        "PLAN_PATH": "",
    }


def test_build_variables_resolves_openspec_paths(tmp_path: Path) -> None:
    # HG-01: framework on context → ${SPEC_PATH}/${PLAN_PATH} resolve to real paths.
    ctx = WorkspaceContext(workspace_root=tmp_path, framework="openspec")
    v = build_variables("2026-06-02-x", ctx)
    base = tmp_path / "openspec" / "changes" / "2026-06-02-x"
    assert v["SPEC_PATH"] == str(base / "proposal.md")
    assert v["PLAN_PATH"] == str(base / "tasks.md")


def test_build_variables_empty_for_unknown_framework(tmp_path: Path) -> None:
    # Unknown framework name → no adapter → empty paths, no crash.
    ctx = WorkspaceContext(workspace_root=tmp_path, framework="nope-fw")
    v = build_variables("x", ctx)
    assert v["SPEC_PATH"] == "" and v["PLAN_PATH"] == ""


# --- collect_checks ---------------------------------------------------------


def test_collect_checks_baseline_layer_yields_two_baselines(tmp_path: Path) -> None:
    # Task 8.5: the baseline layer ships 2 in-process checks in fixed order.
    cfg = _config(layers=Layers(baseline=True, framework_adapter=False, user_checks=False))
    tasks = collect_checks(
        cfg,
        context=_ctx(tmp_path),
        archive=tmp_path / "a",
        variables={"CHANGE_ID": "ch", "SLUG": "ch"},
        layer="baseline",
    )
    assert [t.id for t in tasks] == [
        "lifecycle-ordering",
        "scope-vs-plan-final",
    ]
    # No events.jsonl → clean stream / no scope → all pass.
    by_id = {t.id: t for t in tasks}
    assert by_id["lifecycle-ordering"].must_pass is True
    assert by_id["scope-vs-plan-final"].must_pass is False


def test_collect_checks_includes_adapter_and_user_in_order(tmp_path: Path) -> None:
    # Baseline disabled here to isolate the adapter→user ordering of config checks.
    cfg = _config(
        layers=Layers(baseline=False),
        adapter_provided=[_spec(check_id="adapter-c", command="true")],
        checks=[_spec(check_id="user-c", command="true")],
    )
    tasks = collect_checks(cfg, context=_ctx(tmp_path), archive=tmp_path / "a", variables={})
    # adapter → user (baseline disabled).
    assert [t.id for t in tasks] == ["adapter-c", "user-c"]


def test_collect_checks_layer_filter_restricts_to_one_layer(tmp_path: Path) -> None:
    cfg = _config(
        adapter_provided=[_spec(check_id="adapter-c", command="true")],
        checks=[_spec(check_id="user-c", command="true")],
    )
    only_user = collect_checks(
        cfg, context=_ctx(tmp_path), archive=tmp_path / "a", variables={}, layer="user"
    )
    assert [t.id for t in only_user] == ["user-c"]
    only_adapter = collect_checks(
        cfg, context=_ctx(tmp_path), archive=tmp_path / "a", variables={}, layer="adapter"
    )
    assert [t.id for t in only_adapter] == ["adapter-c"]


def test_collect_checks_enabled_flag_gates_layer(tmp_path: Path) -> None:
    # Baseline disabled so this focuses on the framework_adapter enable flag.
    cfg = _config(
        layers=Layers(baseline=False, framework_adapter=False, user_checks=True),
        adapter_provided=[_spec(check_id="adapter-c", command="true")],
        checks=[_spec(check_id="user-c", command="true")],
    )
    tasks = collect_checks(cfg, context=_ctx(tmp_path), archive=tmp_path / "a", variables={})
    # framework_adapter disabled → adapter check dropped; user check kept.
    assert [t.id for t in tasks] == ["user-c"]


def test_collect_checks_disabled_layer_named_explicitly_still_empty(tmp_path: Path) -> None:
    # `layer="adapter"` selects the adapter layer, but its enable flag is off.
    cfg = _config(
        layers=Layers(framework_adapter=False),
        adapter_provided=[_spec(check_id="adapter-c", command="true")],
    )
    tasks = collect_checks(
        cfg, context=_ctx(tmp_path), archive=tmp_path / "a", variables={}, layer="adapter"
    )
    assert tasks == []


def test_collect_checks_only_ids_filters_across_layers(tmp_path: Path) -> None:
    cfg = _config(
        adapter_provided=[_spec(check_id="a1", command="true")],
        checks=[
            _spec(check_id="u1", command="true"),
            _spec(check_id="u2", command="true"),
        ],
    )
    tasks = collect_checks(
        cfg,
        context=_ctx(tmp_path),
        archive=tmp_path / "a",
        variables={},
        only_ids=["a1", "u2"],
    )
    assert [t.id for t in tasks] == ["a1", "u2"]


# --- collectable_check_ids (FIX 2) ------------------------------------------


def test_baseline_check_ids_is_the_two_baselines() -> None:
    assert set(BASELINE_CHECK_IDS) == {
        "lifecycle-ordering",
        "scope-vs-plan-final",
    }


def test_collectable_check_ids_all_layers() -> None:
    cfg = _config(
        adapter_provided=[_spec(check_id="a1", command="true")],
        checks=[_spec(check_id="u1", command="true")],
    )
    # baseline (2) + adapter (a1) + user (u1), all enabled.
    assert collectable_check_ids(cfg) == set(BASELINE_CHECK_IDS) | {"a1", "u1"}


def test_collectable_check_ids_respects_enable_flags() -> None:
    cfg = _config(
        layers=Layers(baseline=False, framework_adapter=False, user_checks=True),
        adapter_provided=[_spec(check_id="a1", command="true")],
        checks=[_spec(check_id="u1", command="true")],
    )
    # baseline + adapter disabled → only the user layer's ids are collectable.
    assert collectable_check_ids(cfg) == {"u1"}


def test_collectable_check_ids_layer_aware() -> None:
    cfg = _config(
        adapter_provided=[_spec(check_id="a1", command="true")],
        checks=[_spec(check_id="u1", command="true")],
    )
    # A baseline id is NOT collectable under --layer user.
    assert collectable_check_ids(cfg, layer="user") == {"u1"}
    assert "lifecycle-ordering" not in collectable_check_ids(cfg, layer="user")
    assert collectable_check_ids(cfg, layer="baseline") == set(BASELINE_CHECK_IDS)


def test_collect_checks_late_binding_closures_are_per_spec(tmp_path: Path) -> None:
    # Each task must run ITS OWN spec, not the loop's last one (late-binding trap).
    # Baseline disabled so the collected tasks are exactly the two config checks.
    archive = tmp_path / "arch"
    cfg = _config(
        layers=Layers(baseline=False),
        checks=[
            _spec(check_id="first", command="echo first", capture="stdout"),
            _spec(check_id="second", command="echo second", capture="stdout"),
        ],
        defaults=Defaults(env={}),
    )
    # The merged env (incl. PATH from os.environ) is built inside each task.
    tasks = collect_checks(
        cfg, context=_ctx(tmp_path), archive=archive, variables={}
    )
    results = {t.id: t.run() for t in tasks}
    assert (archive / "first.stdout").read_text() == "first\n"
    assert (archive / "second.stdout").read_text() == "second\n"
    assert results["first"].command == "echo first"
    assert results["second"].command == "echo second"


def test_collect_checks_merges_env_with_os_environ(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    # Per-check env layered over defaults.env over os.environ; PATH (from
    # os.environ) must survive the merge so `echo` resolves. Baseline disabled so
    # `tasks[0]` is the config check under test.
    cfg = _config(
        layers=Layers(baseline=False),
        checks=[
            _spec(
                check_id="envc",
                command="echo $FOO-$BAR",
                capture="stdout",
                env={"BAR": "fromcheck"},
            )
        ],
        defaults=Defaults(env={"FOO": "fromdefault"}),
    )
    tasks = collect_checks(cfg, context=_ctx(tmp_path), archive=archive, variables={})
    tasks[0].run()
    assert (archive / "envc.stdout").read_text() == "fromdefault-fromcheck\n"


def test_scrubbed_environ_strips_harness_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every SUPER_HARNESS_* knob is dropped from the ambient base; unrelated
    # vars (PATH, and any non-harness name) survive.
    monkeypatch.setenv("SUPER_HARNESS_CHANGE_ID", "leaked")
    monkeypatch.setenv("SUPER_HARNESS_ACTOR", "leaked@x")
    monkeypatch.setenv("NOT_HARNESS", "kept")

    scrubbed = _scrubbed_environ()

    assert not any(k.startswith("SUPER_HARNESS_") for k in scrubbed)
    assert scrubbed["NOT_HARNESS"] == "kept"
    assert "PATH" in scrubbed  # unrelated ambient vars pass through


def test_collect_checks_scrubs_ambient_harness_env_but_keeps_declared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ambient SUPER_HARNESS_CHANGE_ID must NOT reach the check subprocess (it
    # would poison e2e hooks). A SUPER_HARNESS_* var DECLARED in defaults.env is
    # a deliberate config layer and MUST survive. Baseline disabled so tasks[0]
    # is the check under test.
    monkeypatch.setenv("SUPER_HARNESS_CHANGE_ID", "leaked")
    archive = tmp_path / "arch"
    cfg = _config(
        layers=Layers(baseline=False),
        checks=[
            _spec(
                check_id="envc",
                command="echo [$SUPER_HARNESS_CHANGE_ID][$SUPER_HARNESS_KEEP]",
                capture="stdout",
            )
        ],
        defaults=Defaults(env={"SUPER_HARNESS_KEEP": "declared"}),
    )
    tasks = collect_checks(cfg, context=_ctx(tmp_path), archive=archive, variables={})
    tasks[0].run()
    assert (archive / "envc.stdout").read_text() == "[][declared]\n"


# --- run_checks -------------------------------------------------------------


def _task(check_id: str, *, status: str, must_pass: bool = True) -> CheckTask:
    return CheckTask(
        id=check_id,
        must_pass=must_pass,
        run=lambda check_id=check_id, status=status, must_pass=must_pass: _result(
            check_id, status, must_pass=must_pass
        ),
    )


def test_run_checks_empty_returns_empty() -> None:
    assert run_checks([], mode="parallel", max_workers=4, fail_fast=False) == []


def test_run_checks_sequential_runs_all_in_order() -> None:
    tasks = [_task("a", status="pass"), _task("b", status="fail"), _task("c", status="pass")]
    results = run_checks(tasks, mode="sequential", max_workers=4, fail_fast=False)
    assert [r.id for r in results] == ["a", "b", "c"]


def test_run_checks_parallel_runs_all() -> None:
    tasks = [_task("a", status="pass"), _task("b", status="pass")]
    results = run_checks(tasks, mode="parallel", max_workers=4, fail_fast=False)
    assert {r.id for r in results} == {"a", "b"}


def test_run_checks_fail_fast_sequential_aborts_remaining() -> None:
    ran: list[str] = []

    def make(check_id: str, status: str, must_pass: bool = True) -> CheckTask:
        def _run(check_id: str = check_id, status: str = status) -> CheckResult:
            ran.append(check_id)
            return _result(check_id, status, must_pass=must_pass)

        return CheckTask(id=check_id, must_pass=must_pass, run=_run)

    tasks = [make("a", "pass"), make("b", "fail"), make("c", "pass")]
    results = run_checks(tasks, mode="sequential", max_workers=4, fail_fast=True)
    # `c` never runs — the loop breaks after the must_pass failure of `b`.
    assert ran == ["a", "b"]
    assert [r.id for r in results] == ["a", "b"]


def test_run_checks_fail_fast_ignores_advisory_failures() -> None:
    # An advisory (must_pass=False) failure must NOT abort the run.
    tasks = [
        _task("a", status="fail", must_pass=False),
        _task("b", status="pass"),
    ]
    results = run_checks(tasks, mode="sequential", max_workers=4, fail_fast=True)
    assert [r.id for r in results] == ["a", "b"]


def test_run_checks_fail_fast_parallel_drops_remaining_results() -> None:
    tasks = [_task("a", status="fail"), _task("b", status="pass"), _task("c", status="pass")]
    results = run_checks(tasks, mode="parallel", max_workers=1, fail_fast=True)
    # With max_workers=1 the futures resolve in submission order; `a` fails
    # (must_pass), so b/c results are dropped (cancelled / not collected).
    assert [r.id for r in results] == ["a"]


def test_run_checks_fail_fast_parallel_verdict_safety_with_real_concurrency() -> None:
    # VERDICT-SAFETY invariant: with REAL concurrency (max_workers >= 4) and an
    # early must_pass FAILURE, the best-effort/nondeterministic cancellation must
    # never flip the verdict. The failing must_pass check must always be present
    # in the collected results so `all_pass_must` stays False. We do NOT assert
    # an exact `checks_run` — how many of the others slip through is timing-
    # dependent BY DESIGN (see run_checks' best-effort note).
    tasks = [
        _task("fails-early", status="fail"),  # must_pass failure, submitted first
        _task("b", status="pass"),
        _task("c", status="pass"),
        _task("d", status="pass"),
        _task("e", status="pass"),
        _task("f", status="pass"),
    ]
    results = run_checks(tasks, mode="parallel", max_workers=4, fail_fast=True)

    # The failing must_pass check is always collected (it is what triggers abort).
    ids = {r.id for r in results}
    assert "fails-early" in ids
    # Verdict is driven solely by must_pass results → must be `failed`.
    assert _all_pass_must(results) is False
    must_pass_failed = [r for r in results if r.must_pass and r.status != "pass"]
    verdict = "passed" if not must_pass_failed else "failed"
    assert verdict == "failed"


# --- verify_data_block (FROZEN keys) ---------------------------------------


def test_verify_data_block_exact_keys(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    results = [
        CheckResult("a", "pass", 0, 5, True, "echo a", str(archive / "a.stdout")),
        CheckResult("b", "fail", 1, 7, False, "echo b", None),
    ]
    block = verify_data_block("ch", results, archive, tmp_path)
    assert set(block.keys()) == {
        "change_id",
        "all_pass_must",
        "checks_run",
        "results",
        "summary_path",
    }
    assert block["change_id"] == "ch"
    assert block["all_pass_must"] is True  # the only failure is advisory
    assert block["checks_run"] == 2
    # FIX 3: summary_path + output_path are REPO-RELATIVE to workspace_root (no
    # leading slash), matching the frozen cli-surface §3.4 schema example.
    assert block["summary_path"] == "arch/summary.json"
    assert block["results"][0]["output_path"] == "arch/a.stdout"
    assert block["results"][1]["output_path"] is None  # None passes through
    assert set(block["results"][0].keys()) == {
        "id",
        "status",
        "exit_code",
        "duration_ms",
        "must_pass",
        "output_path",
    }


def test_verify_data_block_all_pass_must_false_when_must_pass_fails(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    results = [CheckResult("a", "fail", 1, 5, True, "echo a", None)]
    block = verify_data_block("ch", results, archive, tmp_path)
    assert block["all_pass_must"] is False


# --- make_verification_event (EXACT payloads) ------------------------------


def test_make_verification_event_passed_payload(tmp_path: Path) -> None:
    results = [
        _result("a", "pass"),
        _result("b", "pass", must_pass=False),
    ]
    ev = make_verification_event("verification_passed", "ch", results, tmp_path)
    assert ev.type == "verification_passed"
    assert ev.change_id == "ch"
    assert ev.framework == "plain"
    assert ev.payload == {"checks_run": 2, "all_pass_must": True}
    # event_id / timestamp left blank for the dispatcher to stamp.
    assert ev.event_id == ""
    assert ev.timestamp == ""


def test_make_verification_event_failed_payload(tmp_path: Path) -> None:
    results = [
        _result("ok", "pass"),
        CheckResult("bad", "fail", 2, 3, True, "echo bad", "/tmp/bad.stdout"),
        # advisory failure must NOT appear in failed_checks
        CheckResult("adv", "fail", 1, 3, False, "echo adv", None),
    ]
    ev = make_verification_event("verification_failed", "ch", results, tmp_path)
    assert ev.type == "verification_failed"
    assert set(ev.payload.keys()) == {"failed_checks", "suggested_fix"}
    assert ev.payload["failed_checks"] == [
        {
            "id": "bad",
            "command": "echo bad",
            "exit_code": 2,
            "output_path": "/tmp/bad.stdout",
        }
    ]
    assert isinstance(ev.payload["suggested_fix"], str)


# --- write_summary_json -----------------------------------------------------


def test_write_summary_json_writes_only_summary(tmp_path: Path) -> None:
    archive = tmp_path / "results"
    results = [_result("a", "pass")]
    write_summary_json(archive, results, "passed")
    summary = archive / "summary.json"
    assert summary.exists()
    assert not (archive / "verdict.json").exists()
    data = json.loads(summary.read_text())
    assert data["verdict"] == "passed"
    assert data["checks_run"] == 1
    assert data["results"][0]["id"] == "a"


# --- VerificationRunner.check() end-to-end ---------------------------------


# Baseline layer DISABLED in this fixture so these config-check end-to-end tests
# keep their exact `checks_run` counts. The baseline layer (2 in-process checks)
# is exercised separately by `test_runner_check_baselines_*` below.
_VERIFY_YAML = """\
layers:
  baseline: {{ enabled: false }}
  framework_adapter: {{ enabled: true }}
  user_checks: {{ enabled: true }}
defaults:
  timeout_seconds: 30
  must_pass: true
  capture: none
  workdir: .
execution:
  mode: {mode}
  max_parallelism: 4
  fail_fast: false
checks:
  - id: passing
    command: "true"
  - id: failing
    command: "false"
    must_pass: {failing_must_pass}
"""


def _write_workspace(tmp_path: Path, *, mode: str = "sequential",
                     failing_must_pass: str = "true") -> Path:
    harness = tmp_path / ".harness"
    harness.mkdir()
    (harness / "verification.yaml").write_text(
        _VERIFY_YAML.format(mode=mode, failing_must_pass=failing_must_pass)
    )
    return tmp_path


def test_runner_check_end_to_end_failed(tmp_path: Path) -> None:
    root = _write_workspace(tmp_path, failing_must_pass="true")
    runner = VerificationRunner()
    trigger = Activity(type="cli_verify", change_id="my-change", payload={})
    res = runner.check(trigger, WorkspaceContext(workspace_root=root))

    assert res.status == "fail"
    assert "failed" in res.summary
    assert res.details is not None
    assert res.details["change_id"] == "my-change"
    assert res.details["checks_run"] == 2
    assert res.details["all_pass_must"] is False
    # Exactly one emitted event, of the failed type.
    assert len(res.emit_events) == 1
    ev = res.emit_events[0]
    assert ev.type == "verification_failed"
    assert ev.change_id == "my-change"
    assert [fc["id"] for fc in ev.payload["failed_checks"]] == ["failing"]
    # summary.json written under the archive dir referenced by details. The
    # details path is now REPO-RELATIVE (FIX 3) so it resolves against root.
    rel_summary = res.details["summary_path"]
    assert rel_summary.startswith(".harness/verification-results/")
    summary_path = root / rel_summary
    assert summary_path.exists()
    assert json.loads(summary_path.read_text())["verdict"] == "failed"


def test_runner_check_end_to_end_passed(tmp_path: Path) -> None:
    # Make the failing check advisory so the run passes overall.
    root = _write_workspace(tmp_path, failing_must_pass="false")
    runner = VerificationRunner()
    trigger = Activity(type="cli_verify", change_id="ch", payload={})
    res = runner.check(trigger, WorkspaceContext(workspace_root=root))

    assert res.status == "pass"
    assert "passed" in res.summary
    assert res.emit_events[0].type == "verification_passed"
    assert res.emit_events[0].payload == {"checks_run": 2, "all_pass_must": True}


def test_runner_check_parallel_mode(tmp_path: Path) -> None:
    root = _write_workspace(tmp_path, mode="parallel", failing_must_pass="false")
    runner = VerificationRunner()
    trigger = Activity(type="cli_done", change_id="ch", payload={})
    res = runner.check(trigger, WorkspaceContext(workspace_root=root))
    assert res.status == "pass"
    assert res.details is not None
    assert res.details["checks_run"] == 2


def test_runner_check_layer_filter_via_payload(tmp_path: Path) -> None:
    root = _write_workspace(tmp_path, failing_must_pass="true")
    runner = VerificationRunner()
    # Restrict to the baseline layer, which this fixture DISABLES → 0 checks →
    # passes vacuously (the enable flag gates the layer even when named).
    trigger = Activity(type="cli_verify", change_id="ch", payload={"layer": "baseline"})
    res = runner.check(trigger, WorkspaceContext(workspace_root=root))
    assert res.details is not None
    assert res.details["checks_run"] == 0
    assert res.status == "pass"


def test_runner_check_only_ids_via_payload(tmp_path: Path) -> None:
    root = _write_workspace(tmp_path, failing_must_pass="true")
    runner = VerificationRunner()
    trigger = Activity(
        type="cli_verify", change_id="ch", payload={"checks": ["passing"]}
    )
    res = runner.check(trigger, WorkspaceContext(workspace_root=root))
    assert res.details is not None
    assert res.details["checks_run"] == 1
    assert res.status == "pass"


def test_runner_falls_back_to_context_change_id(tmp_path: Path) -> None:
    root = _write_workspace(tmp_path, failing_must_pass="false")
    runner = VerificationRunner()
    # Trigger carries no change_id; context.active_change_id supplies it.
    trigger = Activity(type="cli_verify", change_id=None, payload={})
    res = runner.check(
        trigger,
        WorkspaceContext(workspace_root=root, active_change_id="ctx-change"),
    )
    assert res.details is not None
    assert res.details["change_id"] == "ctx-change"


def test_runner_change_id_none_returns_fail_without_crash(tmp_path: Path) -> None:
    root = _write_workspace(tmp_path)
    runner = VerificationRunner()
    trigger = Activity(type="cli_verify", change_id=None, payload={})
    res = runner.check(trigger, WorkspaceContext(workspace_root=root, active_change_id=None))
    assert res.status == "fail"
    assert "no change_id" in res.summary
    # No crash, no events emitted.
    assert res.emit_events == []


# --- builtin registry -------------------------------------------------------


def test_verification_runner_is_registered_builtin() -> None:
    assert "verification-runner" in list_builtins()
    assert get_builtin("verification-runner") is VerificationRunner


# --------------------------------------------------------------------------- #
# Task 8.5 — baseline checks
# --------------------------------------------------------------------------- #


def _evt(change_id: str, evt_type: str, payload: dict[str, Any] | None = None) -> Event:
    return Event(
        event_id=new_event_id(),
        type=evt_type,
        change_id=change_id,
        timestamp="2026-05-27T10:00:00Z",
        actor=Actor(type="adapter", identifier="test"),
        framework="plain",
        payload=payload or {},
    )


def _seed_events(root: Path, change_id: str, items: list[tuple[str, dict[str, Any]]]) -> None:
    """Append events (bypassing emit-time validation) to root/.harness/events.jsonl."""
    w = EventWriter(events_path(root))
    for evt_type, payload in items:
        w.emit(_evt(change_id, evt_type, payload), skip_validation=True)


def _plan_items(
    *,
    scope_files: list[str] | None = None,
    tier: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """A minimal intent_declared → plan_ready stream carrying scope/tier."""
    plan_payload: dict[str, Any] = {}
    if scope_files is not None:
        plan_payload["scope"] = {"files": scope_files}
    if tier is not None:
        plan_payload["tier_hint"] = tier
    return [
        ("intent_declared", {"description": "x"}),
        ("plan_ready", plan_payload),
    ]


def _harness_root(tmp_path: Path) -> Path:
    (tmp_path / ".harness").mkdir(parents=True, exist_ok=True)
    return tmp_path


# --- lifecycle-ordering -----------------------------------------------------


def test_baseline_lifecycle_clean_passes(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _seed_events(root, "ch", _plan_items(tier="Normal"))
    res = _baseline_lifecycle_ordering("ch", context=_ctx(root), archive=tmp_path / "arch")
    assert res.status == "pass"
    assert res.must_pass is True
    assert res.command == "builtin:lifecycle-ordering"
    assert res.output_path is None


def test_baseline_lifecycle_corrupt_fails_with_report(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    # plan_ready as a first event (no intent_declared) is an ordering violation.
    _seed_events(root, "ch", [("plan_ready", {})])
    res = _baseline_lifecycle_ordering("ch", context=_ctx(root), archive=tmp_path / "arch")
    assert res.status == "fail"
    assert res.must_pass is True
    assert res.output_path is not None
    assert "plan_ready" in Path(res.output_path).read_text()


# --- scope-vs-plan-final ----------------------------------------------------


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True)


def _git_repo_with_main(root: Path) -> None:
    """Initialize a git repo on a `main` branch with one committed file."""
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    (root / "seed.txt").write_text("seed\n")
    _git(root, "add", "seed.txt")
    _git(root, "commit", "-m", "seed")


def test_baseline_scope_no_drift_passes(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _git_repo_with_main(root)
    _seed_events(root, "ch", _plan_items(scope_files=["src/"], tier="Normal"))
    # Change a file WITHIN declared scope on a new branch.
    _git(root, "checkout", "-b", "feature")
    (root / "src").mkdir()
    (root / "src" / "f.py").write_text("x\n")
    _git(root, "add", "src/f.py")
    _git(root, "commit", "-m", "in-scope change")
    res = _baseline_scope_vs_plan("ch", context=_ctx(root), archive=tmp_path / "arch")
    assert res.status == "pass"
    assert res.must_pass is False


def test_baseline_scope_out_of_scope_fails(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _git_repo_with_main(root)
    _seed_events(root, "ch", _plan_items(scope_files=["src/"], tier="Normal"))
    _git(root, "checkout", "-b", "feature")
    (root / "rogue.py").write_text("y\n")  # outside declared scope
    _git(root, "add", "rogue.py")
    _git(root, "commit", "-m", "out-of-scope change")
    res = _baseline_scope_vs_plan("ch", context=_ctx(root), archive=tmp_path / "arch")
    assert res.status == "fail"
    assert res.must_pass is False  # advisory: never fails verdict
    assert res.output_path is not None
    assert "rogue.py" in Path(res.output_path).read_text()


def test_baseline_scope_empty_declared_passes(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    _git_repo_with_main(root)
    _seed_events(root, "ch", _plan_items(tier="Normal"))  # no scope.files
    res = _baseline_scope_vs_plan("ch", context=_ctx(root), archive=tmp_path / "arch")
    assert res.status == "pass"


def test_baseline_scope_git_unavailable_passes_with_note(tmp_path: Path) -> None:
    # No git repo + a `main` that does not exist → CalledProcessError; the check
    # CANNOT assert drift, so it passes with an explanatory note (no crash).
    root = _harness_root(tmp_path)
    _seed_events(root, "ch", _plan_items(scope_files=["src/"], tier="Normal"))
    res = _baseline_scope_vs_plan("ch", context=_ctx(root), archive=tmp_path / "arch")
    assert res.status == "pass"
    assert res.must_pass is False
    assert res.output_path is not None
    assert "skipped" in Path(res.output_path).read_text()


def test_covered_by_scope_prefix_is_segment_aware() -> None:
    # A declared entry `src/foo` (no trailing slash) is treated as a directory:
    # it covers everything UNDER it on a path boundary...
    assert _covered_by_scope("src/foo/x.py", ["src/foo"]) is True
    # ...but NOT a sibling that merely shares the textual prefix (the naive
    # `startswith` false-negative this guards against).
    assert _covered_by_scope("src/foobar.py", ["src/foo"]) is False
    # Exact file-path equality still matches.
    assert _covered_by_scope("src/foo.py", ["src/foo.py"]) is True
    # Trailing-slash directory entry behaves identically on the boundary.
    assert _covered_by_scope("src/foo/x.py", ["src/foo/"]) is True
    assert _covered_by_scope("src/foobar.py", ["src/foo/"]) is False


# --- baseline_check_tasks wiring / only_ids ---------------------------------


def test_baseline_check_tasks_all_two_in_order(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    tasks = baseline_check_tasks(
        _config(),
        context=_ctx(root),
        archive=tmp_path / "arch",
        variables={"CHANGE_ID": "ch", "SLUG": "ch"},
    )
    assert [t.id for t in tasks] == [
        "lifecycle-ordering",
        "scope-vs-plan-final",
    ]


def test_baseline_check_tasks_only_ids_filters(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    tasks = baseline_check_tasks(
        _config(),
        context=_ctx(root),
        archive=tmp_path / "arch",
        variables={"CHANGE_ID": "ch", "SLUG": "ch"},
        only_ids=["lifecycle-ordering"],
    )
    assert [t.id for t in tasks] == ["lifecycle-ordering"]


# --- VerificationRunner.check() exercising baselines end-to-end -------------


_BASELINE_ONLY_YAML = """\
layers:
  baseline: { enabled: true }
  framework_adapter: { enabled: false }
  user_checks: { enabled: false }
defaults:
  timeout_seconds: 30
  must_pass: true
  capture: none
  workdir: .
execution:
  mode: sequential
  max_parallelism: 4
  fail_fast: false
checks: []
"""


def test_runner_check_baselines_pass_end_to_end(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    (root / ".harness" / "verification.yaml").write_text(_BASELINE_ONLY_YAML)
    # Clean stream, no scope declared → both baselines pass.
    _seed_events(root, "ch", _plan_items(tier="Normal"))
    runner = VerificationRunner()
    trigger = Activity(type="cli_verify", change_id="ch", payload={})
    res = runner.check(trigger, WorkspaceContext(workspace_root=root))
    assert res.status == "pass"
    assert res.details is not None
    assert res.details["checks_run"] == 2
    assert res.emit_events[0].type == "verification_passed"


def test_runner_check_baselines_lifecycle_failure_fails_verdict(tmp_path: Path) -> None:
    root = _harness_root(tmp_path)
    (root / ".harness" / "verification.yaml").write_text(_BASELINE_ONLY_YAML)
    # Corrupt the stream: plan_ready first → lifecycle-ordering (must_pass) fails.
    _seed_events(root, "ch", [("plan_ready", {})])
    runner = VerificationRunner()
    trigger = Activity(type="cli_verify", change_id="ch", payload={})
    res = runner.check(trigger, WorkspaceContext(workspace_root=root))
    assert res.status == "fail"
    assert res.emit_events[0].type == "verification_failed"
    failed_ids = [fc["id"] for fc in res.emit_events[0].payload["failed_checks"]]
    assert "lifecycle-ordering" in failed_ids
