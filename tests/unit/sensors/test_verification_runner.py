from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path

import pytest

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
    CheckResult,
    CheckTask,
    VerificationRunner,
    build_variables,
    collect_checks,
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
    v = build_variables("my-change", _ctx(tmp_path))
    assert v == {
        "SLUG": "my-change",
        "CHANGE_ID": "my-change",
        "SPEC_PATH": "",
        "PLAN_PATH": "",
    }


# --- collect_checks ---------------------------------------------------------


def test_collect_checks_baseline_layer_is_empty_stub(tmp_path: Path) -> None:
    # Even with the baseline layer enabled, Task 8.4 ships a [] stub.
    cfg = _config(layers=Layers(baseline=True, framework_adapter=False, user_checks=False))
    tasks = collect_checks(
        cfg, context=_ctx(tmp_path), archive=tmp_path / "a", variables={}, layer="baseline"
    )
    assert tasks == []


def test_collect_checks_includes_adapter_and_user_in_order(tmp_path: Path) -> None:
    cfg = _config(
        adapter_provided=[_spec(check_id="adapter-c", command="true")],
        checks=[_spec(check_id="user-c", command="true")],
    )
    tasks = collect_checks(cfg, context=_ctx(tmp_path), archive=tmp_path / "a", variables={})
    # baseline (stub, []) → adapter → user.
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
    cfg = _config(
        layers=Layers(baseline=True, framework_adapter=False, user_checks=True),
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


def test_collect_checks_late_binding_closures_are_per_spec(tmp_path: Path) -> None:
    # Each task must run ITS OWN spec, not the loop's last one (late-binding trap).
    archive = tmp_path / "arch"
    cfg = _config(
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
    # os.environ) must survive the merge so `echo` resolves.
    cfg = _config(
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


# --- verify_data_block (FROZEN keys) ---------------------------------------


def test_verify_data_block_exact_keys(tmp_path: Path) -> None:
    archive = tmp_path / "arch"
    results = [
        CheckResult("a", "pass", 0, 5, True, "echo a", str(archive / "a.stdout")),
        CheckResult("b", "fail", 1, 7, False, "echo b", None),
    ]
    block = verify_data_block("ch", results, archive)
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
    assert block["summary_path"] == str(archive / "summary.json")
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
    block = verify_data_block("ch", results, archive)
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


_VERIFY_YAML = """\
layers:
  baseline: {{ enabled: true }}
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
    # summary.json written under the archive dir referenced by details.
    summary_path = Path(res.details["summary_path"])
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
    # Restrict to a layer with no checks → 0 checks → passes vacuously.
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
