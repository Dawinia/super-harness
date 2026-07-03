"""Shared fixtures + helpers for the E2E test suite.

Two fixtures + a small helper API:

- ``demo_repo`` — pytest fixture: a fresh ``git init``-ed tmp dir with one
  empty initial commit and isolated git identity. The fixture does NOT call
  ``super-harness init`` — each test does that itself so it can assert on the
  bootstrap output.
- ``mock_gh`` — pytest fixture: installs a PATH-shimmed fake ``gh``
  executable that records every invocation to ``calls.jsonl`` and emits
  canned stdout/stderr for the small set of subcommands the engineering
  package calls. Returns a ``MockGh`` wrapper exposing ``.calls`` (lazily
  re-parsed list of argv lists).

Helpers (top-level functions, not fixtures — imported via pytest's
``conftest.py`` magic; tests reference them by name from their own
modules thanks to the fixture-style ``yield``-and-inject pattern below):

- ``_run`` — strict ``subprocess.run(check=True, capture_output=True)``
- ``_last_event_type`` — read last line of ``events.jsonl``
- ``_events_contain_type`` — scan all lines of ``events.jsonl``
- ``_derive_and_read`` — parse ``state.yaml`` directly (no rebuild subprocess)
- ``_emit_via_writer`` — emit a single event with
  ``skip_validation=True`` and refresh ``state.yaml`` synchronously
- ``_wait_for_returncode`` — lifted verbatim from
  ``test_pre_tool_use_claude_code.py`` (the existing race-absorbing poll)
- ``_wait_for_completed_process`` — sibling variant that returns the full
  ``CompletedProcess`` so callers can assert on stderr

Both polling helpers share one internal primitive (``_poll_until``) for
maintenance simplicity.

The test module imports helpers directly via ``from tests.e2e.conftest
import _foo`` because plan §16 writes the test using bare function calls.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers (importable from test modules; not pytest fixtures)
#
# Post-demotion (design 2026-07-03) the gate decides in-process, so there is no
# resident process on the hot path and nothing to widen a query timeout for —
# the old hot-path query-timeout / start-timeout autouse fixture and the
# observer-stop teardown are gone.
# ---------------------------------------------------------------------------


def _run(argv: list[str], *, cwd: Path) -> subprocess.CompletedProcess[bytes]:
    """Strict subprocess runner: capture both streams, raise on non-zero.

    Non-zero exit raises ``CalledProcessError``; pytest will surface
    ``e.stderr`` in the failure output (already bytes, easy to read).
    """
    return subprocess.run(argv, cwd=str(cwd), capture_output=True, check=True)


def _last_event_type(demo_repo: Path) -> str:
    """Return the ``type`` field of the last non-blank line in events.jsonl.

    Raises ``AssertionError`` if events.jsonl is missing or empty — the
    callers always run after at least one emit, so absence is a real test
    failure (not an "events not flushed yet" race; emits are fsynced).
    """
    events_file = demo_repo / ".harness" / "events.jsonl"
    assert events_file.is_file(), f"events.jsonl missing at {events_file}"
    last_obj: dict[str, Any] | None = None
    for line in events_file.read_text(encoding="utf-8").splitlines():
        if line.strip():
            last_obj = json.loads(line)
    assert last_obj is not None, f"events.jsonl is empty at {events_file}"
    event_type = last_obj.get("type")
    assert isinstance(event_type, str), (
        f"last events.jsonl line missing/non-str 'type': {last_obj!r}"
    )
    return event_type


def _events_contain_type(demo_repo: Path, event_type: str) -> bool:
    """True iff any line of events.jsonl parses to a JSON object with the given type."""
    events_file = demo_repo / ".harness" / "events.jsonl"
    if not events_file.is_file():
        return False
    for line in events_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == event_type:
            return True
    return False


def _derive_and_read(demo_repo: Path, slug: str) -> str:
    """Read state.yaml directly and return ``changes[slug].current_state``.

    Does NOT spawn ``super-harness state rebuild`` — every shipped emit
    site already calls ``refresh_state_after_emit``, and so does
    ``_emit_via_writer`` below. A subprocess per assertion would add
    ~100-200 ms each (Phase 14 cold-start measurement).
    """
    state_file = demo_repo / ".harness" / "state.yaml"
    assert state_file.is_file(), f"state.yaml missing at {state_file}"
    data = yaml.safe_load(state_file.read_text(encoding="utf-8")) or {}
    changes = data.get("changes") or {}
    entry = changes.get(slug)
    assert entry is not None, (
        f"state.yaml has no entry for change {slug!r}; known changes: "
        f"{sorted(changes.keys())}"
    )
    current_state = entry.get("current_state")
    assert isinstance(current_state, str), (
        f"state.yaml entry for {slug!r} missing/non-str 'current_state': {entry!r}"
    )
    return current_state


def _emit_via_writer(
    demo_repo: Path,
    *,
    event_type: str,
    change_id: str,
    actor_type: str = "human",
    actor_identifier: str = "e2e-seed",
) -> str:
    """Append one event with ``skip_validation=True`` and refresh state.yaml.

    Used to bridge the three v0.1 lifecycle gaps (``plan_approved`` /
    ``implementation_started`` / ``code_review_passed``) that have no
    production emitter yet — see plan §16 reconcile item #7.

    Returns the event_id (mostly for debug — callers usually ignore it).
    """
    # Imported lazily so a stale ``super_harness`` install during test
    # collection doesn't poison conftest import (the test process and
    # the installed CLI both come from the same venv, but being defensive
    # here costs nothing).
    from super_harness.core.events import Actor, Event
    from super_harness.core.post_emit import refresh_state_after_emit
    from super_harness.core.ulid import new_event_id
    from super_harness.core.writer import EventWriter

    event_id = new_event_id()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    event = Event(
        event_id=event_id,
        type=event_type,
        change_id=change_id,
        timestamp=timestamp,
        actor=Actor(type=actor_type, identifier=actor_identifier),  # type: ignore[arg-type]
        framework="openspec",
        framework_state=None,
        payload={},
    )
    EventWriter(demo_repo / ".harness" / "events.jsonl").emit(event, skip_validation=True)
    refresh_state_after_emit(demo_repo)
    return event_id


def _poll_until(
    run: Callable[[], subprocess.CompletedProcess[Any]],
    expected: int,
    *,
    timeout: float,
    interval: float,
) -> subprocess.CompletedProcess[Any]:
    """Shared polling primitive for the two wait-for-* helpers below.

    Absorbs residual filesystem-visibility lag: a state.yaml write followed by
    an immediate hook subprocess can, on a loaded runner, race the write's
    durability. Re-polls every ``interval`` seconds until either
    ``run().returncode == expected`` or ``timeout`` elapses, then returns the
    last attempt's CompletedProcess (matched-or-final) so the caller's assertion
    message has honest data to display.
    """
    deadline = time.monotonic() + timeout
    last = run()
    while last.returncode != expected and time.monotonic() < deadline:
        time.sleep(interval)
        last = run()
    return last


def _wait_for_returncode(
    run: Callable[[], subprocess.CompletedProcess[Any]],
    expected: int,
    *,
    timeout: float = 3.0,
    interval: float = 0.05,
) -> int:
    """Poll ``run()`` until it returns ``expected`` or the timeout elapses.

    Verbatim lift of the helper in
    ``tests/e2e/test_pre_tool_use_claude_code.py:72-94`` (Phase 5),
    promoted to conftest by Task 16.1 so the new full-lifecycle test
    and the existing pre-tool-use test share one implementation.
    Returns the integer returncode (matched on success, or the final
    attempt's code on timeout so the caller's assert message is honest).
    """
    return _poll_until(run, expected, timeout=timeout, interval=interval).returncode


def _wait_for_completed_process(
    run: Callable[[], subprocess.CompletedProcess[Any]],
    expected: int,
    *,
    timeout: float = 5.0,
    interval: float = 0.05,
) -> subprocess.CompletedProcess[Any]:
    """Polling variant that returns the full CompletedProcess.

    Same race-absorption contract as ``_wait_for_returncode`` but returns
    the full process result so callers can inspect ``.stderr`` (e.g. to
    assert the human-readable BLOCK reason in the E2E test's Phase D).
    """
    return _poll_until(run, expected, timeout=timeout, interval=interval)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def demo_repo(tmp_path: Path) -> Any:
    """A fresh ``git init``-ed tmp dir with one initial empty commit.

    Why an initial commit? Several adapters and engineering helpers do
    ``git rev-parse HEAD`` during setup; a brand-new repo with no commits
    has no HEAD and would crash those paths. One empty commit costs
    nothing and lets the test author make a real second commit later.

    Teardown: none needed — the gate decides in-process (design 2026-07-03),
    so no test spawns an observer host. pytest deletes ``tmp_path`` for us.
    """
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    # Isolated git config — never inherit ~/.gitconfig (could break
    # init.defaultBranch / user.email in unpredictable ways).
    subprocess.run(
        ["git", "init", "--initial-branch=main"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )
    for key, value in (
        ("user.email", "t@t.t"),
        ("user.name", "t"),
        ("commit.gpgsign", "false"),
    ):
        subprocess.run(
            ["git", "config", "--local", key, value],
            cwd=str(repo),
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "initial"],
        cwd=str(repo),
        check=True,
        capture_output=True,
    )

    yield repo


@dataclass
class MockGh:
    """Wrapper over the PATH-shim ``gh`` recorder.

    Attributes:
        shim_dir: tmp dir containing the fake ``gh`` script + calls.jsonl.
        calls_path: sidecar JSONL — one ``{"argv": [...], "stdin": "..."}``
            line per shim invocation, written by the fake gh itself.
    """

    shim_dir: Path
    calls_path: Path

    @property
    def calls(self) -> list[list[str]]:
        """All ``argv`` lists in invocation order. Lazily re-parsed."""
        if not self.calls_path.is_file():
            return []
        argvs: list[list[str]] = []
        for line in self.calls_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            obj = json.loads(line)
            argvs.append(list(obj["argv"]))
        return argvs


# Fake ``gh`` script body. Written as a Python script (not bash) so the
# behaviour is identical across macOS/Linux and the JSON serialization is
# done by stdlib rather than fragile shell quoting.
_GH_SHIM_SOURCE = '''#!/usr/bin/env python3
"""PATH-shim fake `gh` for the super-harness E2E tests.

Records every invocation to $SUPER_HARNESS_E2E_GH_LOG as one JSON line:
    {"argv": [...], "stdin": "..."}

Emits canned stdout for the small set of subcommands the engineering
package actually invokes (gh --version, gh auth status, gh pr create,
gh pr edit, gh pr merge, gh api). Everything else exits 0 with empty
stdout.
"""
import json
import os
import select
import sys


def _read_stdin_nonblocking() -> str:
    # stdin is a tty when invoked by a human — never block waiting for
    # a non-existent body. The engineering package always passes
    # --body-file (not stdin) when it has a body, so this is defensive.
    if sys.stdin.isatty():
        return ""
    try:
        ready, _, _ = select.select([sys.stdin], [], [], 0.0)
    except (OSError, ValueError):
        return ""
    if not ready:
        return ""
    try:
        return sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        return ""


def main() -> int:
    argv = ["gh", *sys.argv[1:]]
    stdin_text = _read_stdin_nonblocking()
    log_path = os.environ.get("SUPER_HARNESS_E2E_GH_LOG")
    if log_path:
        record = {"argv": argv, "stdin": stdin_text}
        # Append a single newline-terminated JSON line. open(..., "a")
        # is fine — calls.jsonl is single-writer per test.
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record) + "\\n")

    sub = argv[1:2]
    sub2 = argv[1:3]
    if sub == ["--version"]:
        sys.stdout.write("gh version 2.40.0 (2024-01-01)\\n")
        sys.stdout.write("https://github.com/cli/cli/releases/tag/v2.40.0\\n")
        return 0
    if sub2 == ["auth", "status"]:
        sys.stdout.write("Logged in to github.com as e2e-shim\\n")
        return 0
    if sub2 == ["pr", "create"]:
        sys.stdout.write("https://example.invalid/owner/repo/pull/1\\n")
        return 0
    if sub2 == ["pr", "edit"]:
        return 0
    if sub2 == ["pr", "merge"]:
        return 0
    # Unrecognized subcommand: exit 0 silently. The recorder still
    # captured argv, so an asserting test can detect "unexpected gh call"
    # via mock_gh.calls.
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


@pytest.fixture
def mock_gh(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> MockGh:
    """Install a fake ``gh`` at the front of PATH for the test process tree.

    ``monkeypatch.setenv("PATH", ...)`` is inherited by every subprocess the
    test spawns (the engineering package invokes ``gh`` via ``subprocess`` with
    the inherited environment, so the shim wins).

    The fake ``gh`` writes one JSON line per call to
    ``calls.jsonl`` next to the shim. The MockGh wrapper re-parses it
    lazily on each ``.calls`` access.
    """
    shim_dir = tmp_path / "gh-shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    gh_path = shim_dir / "gh"
    gh_path.write_text(_GH_SHIM_SOURCE, encoding="utf-8")
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    calls_path = shim_dir / "calls.jsonl"
    # Start clean — pytest's tmp_path is per-test but be explicit.
    if calls_path.exists():
        calls_path.unlink()

    # Belt-and-suspenders: if anyone managed to drop a real `gh` symlink
    # earlier in PATH, we wouldn't win. Verify our shim wins by checking
    # the system `gh` resolution will be ours after monkeypatch.
    old_path = os.environ.get("PATH", "")
    monkeypatch.setenv("PATH", f"{shim_dir}{os.pathsep}{old_path}")
    monkeypatch.setenv("SUPER_HARNESS_E2E_GH_LOG", str(calls_path))

    # Defensive sanity-check: `shutil.which` should resolve to our shim.
    # If it doesn't, fail loudly here instead of surfacing as a confusing
    # downstream "gh auth status returned unexpected output" later.
    resolved = shutil.which("gh")
    assert resolved is not None, "PATH-shim install failed: no gh on PATH"
    assert Path(resolved).resolve() == gh_path.resolve(), (
        f"PATH-shim install failed: which('gh') resolves to {resolved!r}, "
        f"expected our shim at {gh_path!r}"
    )

    return MockGh(shim_dir=shim_dir, calls_path=calls_path)
