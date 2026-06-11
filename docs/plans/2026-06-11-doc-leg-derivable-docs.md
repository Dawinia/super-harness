# doc-leg (derivable docs) Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (or
> superpowers:subagent-driven-development) to implement this plan task-by-task.

**Goal:** Generalize the one-off `cli-reference` drift check into a declared
`.harness/derived-docs.yaml` registry + a single `super-harness doc check [--fix]
[--json]` regen-and-diff gate (tool owns diff/fix/exit/json; generators just emit to
stdout), then migrate `cli-reference` in and add a code-derived `state-machine.md`.

**Architecture:** A pure core module (`core/doc_check.py`) loads + validates the
registry (fail-closed) and runs each generator as a no-shell, timeout-bounded
subprocess, capturing stdout and diffing against the committed file. A thin CLI
group (`cli/doc.py`) wraps it with the global `--json` envelope and the repo's exit
codes. Generators are dumb stdout emitters: `gen_cli_reference --emit` and a new
`gen_state_machine --emit` that derives the state table by *calling*
`compute_target_state` over every state×event pair. Mirrors the slice-1
`decision check` sibling end-to-end.

**Tech Stack:** Python 3.10+, click, PyYAML, stdlib `subprocess`/`shlex`/`difflib`,
pytest. Design SSOT: `docs/plans/2026-06-11-doc-leg-derivable-docs-design.md`
(umbrella: `docs/plans/2026-06-05-decision-conformance-harness-design.md` §13).

---

## Conventions for every task

- **Env first:** `export PATH="$PWD/.venv/bin:$PATH"` once per shell.
- **Green invariant after every task** (all must pass before commit):
  `.venv/bin/pytest -q` (all-green; count **≥ 1255** — that is the pre-slice
  baseline and it only grows as each task adds tests; never assert `== 1255`),
  `.venv/bin/ruff check .`, `.venv/bin/mypy src` (run BOTH ruff and mypy — slice-1
  let a type error slip by skipping mypy). **`super-harness doc check` (exit 0)
  joins the invariant only from Task 6 onward** — the command + registry do not exist
  before then.
- **TDD:** write the failing test, run it red, implement minimal, run green, commit.
- Mirror the sibling `decision`/`decision_check`/`source_scope` patterns already in
  the tree (exact paths cited per task) — do not invent new shapes.
- Subagent edits bypass the PreToolUse gate; keep scope honest (gate is
  orchestrator-level).
- Commit messages: `feat:` / `test:` / `chore:` / `docs:`; English; end with the
  Co-Authored-By trailer per repo convention. Commit only the files a task names.

---

## Task 1: Registry loader (fail-closed) — `core/doc_check.py` data + `load_derived_docs`

**Files:**
- Create: `src/super_harness/core/doc_check.py`
- Test: `tests/unit/core/test_doc_check_loader.py`

Mirror `core/source_scope.py` (YAML *shape*) but `core/decisions.py`'s `RecordError`
(fail-*closed* error handling — NOT source_scope's fail-open default-return).

**Step 1: Write the failing tests** (`tests/unit/core/test_doc_check_loader.py`)

```python
from pathlib import Path

from super_harness.core.doc_check import DerivedDoc, load_derived_docs


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _reg(root: Path, body: str) -> None:
    _w(root / ".harness/derived-docs.yaml", body)


def test_absent_file_is_clean_not_error(tmp_path):
    docs, errors = load_derived_docs(tmp_path)
    assert docs == [] and errors == []


def test_valid_registry_parses(tmp_path):
    _reg(tmp_path, "derived_docs:\n"
                   "  - path: docs/a.md\n    command: echo hi\n"
                   "  - path: docs/b.md\n    command: python -m x --emit\n")
    docs, errors = load_derived_docs(tmp_path)
    assert errors == []
    assert docs == [DerivedDoc(path="docs/a.md", command="echo hi"),
                    DerivedDoc(path="docs/b.md", command="python -m x --emit")]


def test_unparseable_yaml_is_malformed(tmp_path):
    _reg(tmp_path, "derived_docs: [unclosed\n")
    docs, errors = load_derived_docs(tmp_path)
    assert docs == [] and [e.code for e in errors] == ["malformed_registry"]


def test_top_not_mapping_is_malformed(tmp_path):
    _reg(tmp_path, "- just\n- a\n- list\n")
    _, errors = load_derived_docs(tmp_path)
    assert [e.code for e in errors] == ["malformed_registry"]


def test_derived_docs_not_a_list_is_malformed(tmp_path):
    _reg(tmp_path, "derived_docs: 7\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_entry_not_mapping_is_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - just-a-string\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_missing_or_nonstring_keys_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: docs/a.md\n")  # no command
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_empty_command_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: docs/a.md\n    command: '   '\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["malformed_registry"]


def test_absolute_path_is_escape(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: /etc/x.md\n    command: echo hi\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["path_escape"]


def test_dotdot_escape(tmp_path):
    _reg(tmp_path, "derived_docs:\n  - path: ../x.md\n    command: echo hi\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["path_escape"]


def test_duplicate_path_malformed(tmp_path):
    _reg(tmp_path, "derived_docs:\n"
                   "  - path: docs/a.md\n    command: echo 1\n"
                   "  - path: docs/a.md\n    command: echo 2\n")
    assert [e.code for e in load_derived_docs(tmp_path)[1]] == ["duplicate_path"]
```

**Step 2: Run red:** `pytest tests/unit/core/test_doc_check_loader.py -q` → import error / failures.

**Step 3: Implement** the data + loader in `core/doc_check.py`:

```python
"""Derivable-doc registry + regen-and-diff engine (design 2026-06-11).

Loader mirrors source_scope.py's YAML shape but decisions.py's fail-CLOSED
error handling: a malformed registry blocks (RegistryError), never silently
defaults to "no docs".
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

import yaml

_GENERATOR_TIMEOUT_S = 30


@dataclass(frozen=True)
class DerivedDoc:
    path: str       # repo-relative, validated inside-repo
    command: str    # generator invocation; emits canonical content to stdout


@dataclass(frozen=True)
class RegistryError:
    code: str       # malformed_registry | path_escape | duplicate_path
    message: str
    file: str = ".harness/derived-docs.yaml"


def derived_docs_file(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "derived-docs.yaml"


def _escapes_repo(workspace_root: Path, rel: str) -> bool:
    if Path(rel).is_absolute():
        return True
    resolved = (workspace_root / rel).resolve()
    root = workspace_root.resolve()
    return root != resolved and root not in resolved.parents


def load_derived_docs(
    workspace_root: Path,
) -> tuple[list[DerivedDoc], list[RegistryError]]:
    f = derived_docs_file(workspace_root)
    if not f.is_file():
        return [], []
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError, UnicodeDecodeError) as exc:
        return [], [RegistryError("malformed_registry", f"unparseable YAML: {exc}")]
    if data is None:
        return [], []
    if not isinstance(data, dict):
        return [], [RegistryError("malformed_registry", "top-level must be a mapping")]
    entries = data.get("derived_docs")
    if not isinstance(entries, list):
        return [], [RegistryError("malformed_registry", "`derived_docs` must be a list")]

    docs: list[DerivedDoc] = []
    errors: list[RegistryError] = []
    seen: set[str] = set()
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(RegistryError("malformed_registry", f"entry {i} is not a mapping"))
            continue
        path = entry.get("path")
        command = entry.get("command")
        if not isinstance(path, str) or not isinstance(command, str):
            errors.append(RegistryError("malformed_registry", f"entry {i} needs string path+command"))
            continue
        if not shlex.split(command):
            errors.append(RegistryError("malformed_registry", f"entry {i} has empty command"))
            continue
        if _escapes_repo(workspace_root, path):
            errors.append(RegistryError("path_escape", f"path escapes repo: {path!r}"))
            continue
        if path in seen:
            errors.append(RegistryError("duplicate_path", f"duplicate path: {path!r}"))
            continue
        seen.add(path)
        docs.append(DerivedDoc(path=path, command=command))
    return docs, errors
```

**Step 4: Run green:** `pytest tests/unit/core/test_doc_check_loader.py -q` → PASS.
Then `ruff check . && mypy src`.

**Step 5: Commit**

```bash
git add src/super_harness/core/doc_check.py tests/unit/core/test_doc_check_loader.py
git commit -m "feat(doc-check): fail-closed derived-docs registry loader"
```

---

## Task 2: The regen-and-diff engine — `run_doc_check`

**Files:**
- Modify: `src/super_harness/core/doc_check.py`
- Test: `tests/unit/core/test_doc_check_engine.py`

Use real subprocess generators in tests via tiny inline commands. A clean emitter:
`python -c "print('hello')"` (prints `hello\n`). A failing one: `python -c "import sys;sys.exit(1)"`. A hang is not unit-tested for time (covered by a fast `sleep` with a tiny timeout override only if a hook exists — here keep the constant; instead test `TimeoutExpired` via a generator that sleeps and a monkeypatched constant).

**Step 1: Write failing tests** (`tests/unit/core/test_doc_check_engine.py`)

```python
import sys
from pathlib import Path

from super_harness.core import doc_check
from super_harness.core.doc_check import run_doc_check


def _w(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _reg(root: Path, entries: list[tuple[str, str]]) -> None:
    body = "derived_docs:\n" + "".join(
        f"  - path: {p}\n    command: {c}\n" for p, c in entries
    )
    _w(root / ".harness/derived-docs.yaml", body)


def _emit(text: str) -> str:
    # a generator command that prints exactly `text` (no extra newline mangling)
    return f'{sys.executable} -c "import sys;sys.stdout.write({text!r})"'


def test_in_sync(tmp_path):
    _w(tmp_path / "docs/a.md", "hello\n")
    _reg(tmp_path, [("docs/a.md", _emit("hello\n"))])
    r = run_doc_check(tmp_path)
    assert [d.path for d in r.in_sync] == ["docs/a.md"]
    assert r.drift == [] and r.failed == [] and r.errors == []
    assert r.exit_code == 0


def test_drift(tmp_path):
    _w(tmp_path / "docs/a.md", "stale\n")
    _reg(tmp_path, [("docs/a.md", _emit("fresh\n"))])
    r = run_doc_check(tmp_path)
    assert [d.path for d in r.drift] == ["docs/a.md"]
    assert "fresh" in r.drift[0].diff and r.exit_code == 2


def test_missing_file_is_drift(tmp_path):
    _reg(tmp_path, [("docs/a.md", _emit("x\n"))])
    r = run_doc_check(tmp_path)
    assert [d.path for d in r.drift] == ["docs/a.md"] and r.exit_code == 2


def test_generator_nonzero_is_failed(tmp_path):
    _w(tmp_path / "docs/a.md", "x\n")
    _reg(tmp_path, [("docs/a.md", f'{sys.executable} -c "import sys;sys.exit(3)"')])
    r = run_doc_check(tmp_path)
    assert [f.path for f in r.failed] == ["docs/a.md"] and r.exit_code == 4


def test_malformed_registry_dominates(tmp_path):
    _w(tmp_path / ".harness/derived-docs.yaml", "derived_docs: 7\n")
    r = run_doc_check(tmp_path)
    assert r.exit_code == 3 and r.errors and not r.in_sync and not r.drift


def test_crlf_normalized_not_drift(tmp_path):
    _w(tmp_path / "docs/a.md", "a\nb\n")
    _reg(tmp_path, [("docs/a.md", _emit("a\r\nb\r\n"))])
    r = run_doc_check(tmp_path)
    assert [d.path for d in r.in_sync] == ["docs/a.md"]


def test_coexistence_precedence_4_over_2(tmp_path):
    _w(tmp_path / "docs/a.md", "stale\n")
    _reg(tmp_path, [("docs/a.md", _emit("fresh\n")),
                    ("docs/b.md", f'{sys.executable} -c "import sys;sys.exit(1)"')])
    r = run_doc_check(tmp_path)
    # every entry evaluated; both buckets populated; process code is 4 (most severe)
    assert [d.path for d in r.drift] == ["docs/a.md"]
    assert [f.path for f in r.failed] == ["docs/b.md"]
    assert r.exit_code == 4


def test_fix_writes_drift_resolves_to_zero(tmp_path):
    _w(tmp_path / "docs/a.md", "stale\n")
    _reg(tmp_path, [("docs/a.md", _emit("fresh\n"))])
    r = run_doc_check(tmp_path, fix=True)
    assert (tmp_path / "docs/a.md").read_text() == "fresh\n"
    assert r.exit_code == 0


def test_fix_does_not_write_failed(tmp_path):
    _w(tmp_path / "docs/a.md", "keep\n")
    _reg(tmp_path, [("docs/a.md", f'{sys.executable} -c "import sys;sys.exit(2)"')])
    r = run_doc_check(tmp_path, fix=True)
    assert (tmp_path / "docs/a.md").read_text() == "keep\n"   # untouched
    assert r.exit_code == 4


def test_timeout_is_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(doc_check, "_GENERATOR_TIMEOUT_S", 1)
    _w(tmp_path / "docs/a.md", "x\n")
    _reg(tmp_path, [("docs/a.md", f'{sys.executable} -c "import time;time.sleep(5)"')])
    r = run_doc_check(tmp_path)
    assert [f.path for f in r.failed] == ["docs/a.md"] and r.exit_code == 4
```

**Step 2: Run red.**

**Step 3: Implement** in `core/doc_check.py` (append):

```python
import subprocess
import difflib
from dataclasses import field

from super_harness.exit_codes import (
    EXIT_OK, EXIT_VALIDATION, EXIT_NO_CONFIG, EXIT_EXTERNAL_TOOL,
)

_DIFF_MAX_LINES = 40


@dataclass
class InSync:
    path: str


@dataclass
class Drift:
    path: str
    diff: str   # truncated to _DIFF_MAX_LINES for envelopes; full to stderr


@dataclass
class Failed:
    path: str
    command: str
    error: str


@dataclass
class DocCheckResult:
    in_sync: list[InSync] = field(default_factory=list)
    drift: list[Drift] = field(default_factory=list)
    failed: list[Failed] = field(default_factory=list)
    errors: list[RegistryError] = field(default_factory=list)
    exit_code: int = EXIT_OK


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n")


def _run_generator(workspace_root: Path, command: str) -> tuple[str | None, str]:
    """Return (generated_text, error). text is None on failure."""
    argv = shlex.split(command)
    try:
        proc = subprocess.run(  # noqa: S603 (no shell, argv from tracked config)
            argv, cwd=workspace_root, capture_output=True,
            timeout=_GENERATOR_TIMEOUT_S,
        )
    except FileNotFoundError:
        return None, "command not found"
    except subprocess.TimeoutExpired:
        return None, f"timed out after {_GENERATOR_TIMEOUT_S}s"
    if proc.returncode != 0:
        return None, f"exit {proc.returncode}"
    try:
        return proc.stdout.decode("utf-8"), ""
    except UnicodeDecodeError:
        return None, "invalid UTF-8 stdout"


def _truncate(diff: str) -> str:
    lines = diff.splitlines(keepends=True)
    if len(lines) <= _DIFF_MAX_LINES:
        return diff
    extra = len(lines) - _DIFF_MAX_LINES
    return "".join(lines[:_DIFF_MAX_LINES]) + f"... ({extra} more lines; full diff on stderr)\n"


def run_doc_check(workspace_root: Path, *, fix: bool = False) -> DocCheckResult:
    docs, errors = load_derived_docs(workspace_root)
    if errors:
        return DocCheckResult(errors=errors, exit_code=EXIT_NO_CONFIG)

    result = DocCheckResult()
    for doc in docs:
        generated, err = _run_generator(workspace_root, doc.command)
        if generated is None:
            result.failed.append(Failed(path=doc.path, command=doc.command, error=err))
            continue
        generated = _normalize(generated)
        target = workspace_root / doc.path
        try:
            on_disk = _normalize(target.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            on_disk = None
        if on_disk == generated:
            result.in_sync.append(InSync(path=doc.path))
            continue
        # drift
        diff = "".join(difflib.unified_diff(
            (on_disk or "").splitlines(keepends=True),
            generated.splitlines(keepends=True),
            fromfile=doc.path, tofile=f"{doc.path} (regenerated)",
        ))
        if fix:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(generated, encoding="utf-8")
            result.in_sync.append(InSync(path=doc.path))   # resolved
        else:
            result.drift.append(Drift(path=doc.path, diff=_truncate(diff)))

    # rollup (3 dominates 4 dominates 2; fix resolves drift → never 2 after fix)
    result.in_sync.sort(key=lambda x: x.path)
    result.drift.sort(key=lambda x: x.path)
    result.failed.sort(key=lambda x: x.path)
    if result.failed:
        result.exit_code = EXIT_EXTERNAL_TOOL
    elif result.drift:
        result.exit_code = EXIT_VALIDATION
    else:
        result.exit_code = EXIT_OK
    return result
```

> Note: with `fix=True`, drift entries are written and counted as `in_sync`, so
> `result.drift` is empty → exit `0` unless a generator `failed` (→ `4`). Matches
> design §3.2 "--fix exits 0 when it resolved the gate".

**Step 4: Run green** + `ruff` + `mypy`. (Add `# noqa: S603` only if ruff flags
subprocess; otherwise omit.)

**Step 5: Commit**

```bash
git add src/super_harness/core/doc_check.py tests/unit/core/test_doc_check_engine.py
git commit -m "feat(doc-check): regen-and-diff engine (no-shell, timeout, --fix)"
```

---

## Task 3: CLI `doc` group — `cli/doc.py` + register

**Files:**
- Create: `src/super_harness/cli/doc.py`
- Modify: `src/super_harness/cli/__init__.py` (import + `main.add_command(doc_group)`)
- Test: `tests/unit/cli/test_doc.py`

Mirror `cli/decision.py::check_cmd` exactly (resolve root, global `--json`, exit).

**Step 1: Write failing tests** (`tests/unit/cli/test_doc.py`) — use click's
`CliRunner` against `super_harness.cli.main`, building a tmp workspace with
`.harness/` + a registry. Mirror `tests/unit/cli/test_decision.py`. Assert:
`doc check` exit 0 when in sync; exit 2 on drift (non-json); `--json` emits the
6-key envelope with `data.in_sync/drift/failed`; `doc check --fix` writes + exits 0;
malformed registry → exit 3; no `.harness/` → exit 3 via `find_harness_root`.

```python
import json
from pathlib import Path

from click.testing import CliRunner

from super_harness.cli import main


def _ws(tmp_path: Path, entries: list[tuple[str, str]]) -> Path:
    (tmp_path / ".harness").mkdir()
    body = "derived_docs:\n" + "".join(
        f"  - path: {p}\n    command: {c}\n" for p, c in entries)
    (tmp_path / ".harness/derived-docs.yaml").write_text(body)
    return tmp_path


def test_check_json_envelope(tmp_path):
    import sys
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/a.md").write_text("x\n")
    _ws(tmp_path, [("docs/a.md", f'{sys.executable} -c "print(\'x\')"')])
    r = CliRunner().invoke(main, ["--json", "--workspace", str(tmp_path), "doc", "check"])
    assert r.exit_code == 0
    env = json.loads(r.output)
    assert env["command"] == "doc check" and env["status"] == "pass"
    assert env["data"]["in_sync"] == ["docs/a.md"]
```

> Check how `--workspace`/`ctx.obj` is threaded in `test_decision.py`; match it
> (the global options live in `cli/group_options.py`). If `decision check` tests
> pass `--json`/`--workspace` a particular way, copy that verbatim.

**Step 2: Run red.**

**Step 3: Implement** `cli/doc.py`:

```python
"""`doc` subgroup — regen-and-diff gate for derivable docs (design 2026-06-11)."""
from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import Status, json_envelope
from super_harness.core.doc_check import run_doc_check
from super_harness.core.paths import HarnessNotInitialized, find_harness_root
from super_harness.exit_codes import EXIT_NO_CONFIG


@click.group("doc")
def doc_group() -> None:
    """Check that derivable docs match their generators."""


@doc_group.command("check")
@click.option("--fix", is_flag=True, help="Regenerate drifted docs in place.")
@click.pass_context
def check_cmd(ctx: click.Context, fix: bool) -> None:
    """Regen-and-diff every registered derived doc. Honors global --json."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand="doc check", message=e.message, hint=e.hint), err=True)
        sys.exit(EXIT_NO_CONFIG)

    result = run_doc_check(root, fix=fix)
    status: Status = "fail" if (result.drift or result.failed or result.errors) else "pass"

    if ctx.obj.get("json"):
        click.echo(json_envelope(
            command="doc check",
            status=status,
            exit_code=result.exit_code,
            data={
                "in_sync": [d.path for d in result.in_sync],
                "drift": [{"path": d.path, "diff": d.diff} for d in result.drift],
                "failed": [{"path": f.path, "command": f.command, "error": f.error}
                           for f in result.failed],
            },
            errors=[{"code": e.code, "message": e.message, "file": e.file}
                    for e in result.errors],
        ))
    else:
        for e in result.errors:
            click.echo(f"ERROR [{e.code}] {e.file}: {e.message}", err=True)
        for f in result.failed:
            click.echo(f"FAILED {f.path}: {f.error} ({f.command})", err=True)
        for d in result.drift:
            click.echo(f"DRIFT {d.path}", err=True)
            click.echo(d.diff, err=True)
        if status == "pass":
            click.echo("doc check: clean" if not fix else "doc check: fixed")
    sys.exit(result.exit_code)
```

Register in `cli/__init__.py`: add `from super_harness.cli.doc import doc_group`
(alphabetical with siblings) and `main.add_command(doc_group)` beside the others.

**Step 4: Run green** + ruff + mypy. **Adding the `doc` command changed the live CLI
tree, so the existing test `tests/unit/scripts/test_gen_cli_reference.py::
test_real_cli_reference_is_in_sync` (it calls `run_check` against the committed
`docs/cli-reference.md`) now FAILS red.** Regenerate the golden *in this commit*
using the still-present write mode:

```bash
python -m scripts.gen_cli_reference        # old no-arg write mode still exists pre-Task-4
```

Re-run `pytest -q` → green. (This is the repo's standing rule: a CLI-surface change
and its `cli-reference.md` regen land in the same commit.)

**Step 5: Commit**

```bash
git add src/super_harness/cli/doc.py src/super_harness/cli/__init__.py tests/unit/cli/test_doc.py docs/cli-reference.md
git commit -m "feat(doc-check): super-harness doc check CLI group"
```

---

## Task 4: Migrate `gen_cli_reference` to `--emit`-only

**Files:**
- Modify: `scripts/gen_cli_reference.py` (`_HEADER_NOTICE`, module docstring,
  `main()` argparse, `run_check` stderr string)
- Modify: `tests/unit/scripts/test_gen_cli_reference.py` (drop `--check` CLI
  assumptions; keep function-level tests; update remediation strings)

**Step 1:** Update the test first to express the new contract:
- `main(["--emit"])` prints the rendered markdown to stdout and returns 0.
- `main()` with no args (old write mode) and `main(["--check"])` no longer exist —
  remove/replace those tests. Keep `render_markdown`/`run_check`/`write_reference`
  function tests (they stay callable).
- Assert `_HEADER_NOTICE` references `super-harness doc check --fix`, not the dead
  `python -m scripts.gen_cli_reference`.

**Step 2: Run red.**

**Step 3: Implement** in `scripts/gen_cli_reference.py`:
- `_HEADER_NOTICE` → `"... Regenerate with: super-harness doc check --fix -->"`.
- Module docstring: replace the `--check` / write-mode usage block with `--emit`.
- `main()`: replace `--check` with `--emit`; on `--emit`, `print(render_markdown(
  cli_main, root_name=root_name), end="")` and return 0. Keep `--target` only if a
  test needs it; otherwise drop. `run_check`/`write_reference` remain defined
  (used by the unit tests) but are no longer reachable from `main()`. Update
  `run_check`'s stderr remediation string to `super-harness doc check --fix`.

**Step 4: Run green** (`pytest -q`) + ruff + mypy. **Changing `_HEADER_NOTICE`
changes `--emit` output, so the committed `docs/cli-reference.md` is now stale and
`test_real_cli_reference_is_in_sync` fails red again.** Regenerate it in this commit —
the no-arg write mode is now gone, so use `--emit`:

```bash
python -m scripts.gen_cli_reference --emit > docs/cli-reference.md
```

Re-run `pytest -q` → green.

**Step 5: Commit**

```bash
git add scripts/gen_cli_reference.py tests/unit/scripts/test_gen_cli_reference.py docs/cli-reference.md
git commit -m "refactor(gen-cli-ref): --emit-only; doc check owns the gate"
```

---

## Task 5: `gen_state_machine.py` — derive by calling the state machine

**Files:**
- Create: `scripts/gen_state_machine.py`
- Test: `tests/unit/scripts/test_gen_state_machine.py`

Derive by *calling* `compute_target_state` over `(STATES ∪ {None}) ×
KNOWN_EVENT_TYPES`; filter self-loops (`to != from`) into the transition table;
collect events that never change state into a separate list; sort with a
`None`-tolerant key; render `None` as `(start)`.

**Step 1: Write failing tests** (`tests/unit/scripts/test_gen_state_machine.py`)

```python
from scripts.gen_state_machine import build_rows, render_markdown, NOOP_EVENTS


def test_transition_rows_exclude_self_loops():
    rows = build_rows()
    assert all(r.frm != r.to for r in rows)         # no X --e--> X
    assert any(r.frm is None and r.to == "INTENT_DECLARED" for r in rows)  # start row


def test_rows_are_deterministically_sorted():
    assert build_rows() == build_rows()             # stable
    keys = [(("" if r.frm is None else r.frm), r.event) for r in build_rows()]
    assert keys == sorted(keys)


def test_known_count_is_derived_not_hardcoded():
    # ~48 genuine transitions verified against live code at design time;
    # assert the structural property, not a brittle magic number.
    rows = build_rows()
    assert 40 <= len(rows) <= 60
    assert "verification_passed" in NOOP_EVENTS     # informational = no-op


def test_render_is_deterministic_and_has_header():
    out = render_markdown()
    assert out == render_markdown()
    assert out.endswith("\n") and "(start)" in out
```

**Step 2: Run red.**

**Step 3: Implement** `scripts/gen_state_machine.py`:

```python
"""Generate docs/state-machine.md by CALLING the live state machine.

Derives the reachability table from super_harness.core.transitions — it does
NOT transcribe the imperative branches, so the doc cannot drift from the code.
Emit to stdout: `python -m scripts.gen_state_machine --emit`.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass

from super_harness.core.events import KNOWN_EVENT_TYPES
from super_harness.core.state import STATES, TERMINAL_STATES
from super_harness.core.transitions import INVALID, compute_target_state

_HEADER = (
    "<!-- AUTOGENERATED by scripts/gen_state_machine.py — do not edit by hand. "
    "Regenerate with: super-harness doc check --fix -->"
)


@dataclass(frozen=True)
class Row:
    frm: str | None
    event: str
    to: str


def build_rows() -> list[Row]:
    rows: list[Row] = []
    sources: list[str | None] = [None, *STATES]
    for frm in sources:
        for event in sorted(KNOWN_EVENT_TYPES):
            to = compute_target_state(frm, event)
            if to == INVALID:
                continue
            if to == frm:           # self-loop (informational / re-emit) → not a move
                continue
            rows.append(Row(frm=frm, event=event, to=to))
    rows.sort(key=lambda r: ("" if r.frm is None else r.frm, r.event))
    return rows


def _noop_events() -> list[str]:
    """Events legal on every non-terminal state that leave it unchanged.

    Derived by CALLING the machine (not importing the private _INFORMATIONAL set).
    Two guards verified against live code: (1) exclude `intent_declared` — it returns
    `current` on active states (re-emit = description update) but is NOT informational;
    (2) check only NON-terminal states — terminal states return INVALID for these
    events, which must not disqualify them. Yields exactly the 7 informational events.
    """
    nonterm = [s for s in STATES if s not in TERMINAL_STATES]
    out = []
    for event in sorted(KNOWN_EVENT_TYPES):
        if event == "intent_declared":
            continue
        if all(compute_target_state(s, event) == s for s in nonterm):
            out.append(event)
    return out


NOOP_EVENTS = _noop_events()


def render_markdown() -> str:
    lines = [_HEADER, "", "# Lifecycle state machine", "",
             "Generated from `super_harness.core.transitions`. Each row is a legal "
             "`(from, event) → to` transition; `(start)` is the pre-first-event state.",
             "", "| from | event | to |", "|------|-------|----|"]
    for r in build_rows():
        frm = "(start)" if r.frm is None else r.frm
        lines.append(f"| `{frm}` | `{r.event}` | `{r.to}` |")
    lines += ["", "## Events that never change state", "",
              "These events are legal but leave the state unchanged "
              "(informational sensor signals):", ""]
    lines += [f"- `{e}`" for e in NOOP_EVENTS]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gen_state_machine")
    p.add_argument("--emit", action="store_true", help="Print the doc to stdout.")
    p.parse_args(argv)
    sys.stdout.write(render_markdown())
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

> The predicate above was executed against live code during planning: `build_rows()`
> yields **48** transition rows and `_noop_events()` yields exactly the 7-element
> informational set (`merged_reverted`, `pr_opened`, `scope_drift_detected`,
> `sensor_crashed`, `sensor_timeout_exceeded`, `verification_failed`,
> `verification_passed`) — matching `transitions._INFORMATIONAL` without importing
> it (keeps the doc a true `f(behavior)`). Do NOT simplify to "in (s, INVALID) for
> every state" — that wrongly disqualifies on terminal-state INVALID and wrongly
> includes `intent_declared`.

**Step 4: Run green** + ruff + mypy.

**Step 5: Commit**

```bash
git add scripts/gen_state_machine.py tests/unit/scripts/test_gen_state_machine.py
git commit -m "feat(gen-state-machine): derive state table by calling the machine"
```

---

## Task 6: Self-host registry + generate the docs + getting-started link

**Files:**
- Create: `.harness/derived-docs.yaml`
- Create: `docs/state-machine.md` (generated, committed)
- Modify: `docs/cli-reference.md` (regenerated — header notice + new `doc` command)
- Modify: `docs/getting-started.md` (one-line link only — NO prose rewrite)

**Step 1:** Write `.harness/derived-docs.yaml`:

```yaml
derived_docs:
  - path: docs/cli-reference.md
    command: python -m scripts.gen_cli_reference --emit
  - path: docs/state-machine.md
    command: python -m scripts.gen_state_machine --emit
```

**Step 2:** Generate both docs via the new gate:

```bash
super-harness doc check --fix   # writes state-machine.md (new) + cli-reference.md (regen)
super-harness doc check         # expect: clean, exit 0
```

**Step 3:** Add a single link line to `docs/getting-started.md` near its first state
mention (e.g. after the `INTENT_DECLARED` walkthrough): `> For the full
state/transition reference, see [state-machine.md](state-machine.md).` Do **not**
remove the existing narrative (deferred docs pass — design §5.3).

**Step 4:** Full green invariant: `pytest -q` (1255+), `ruff check .`, `mypy src`,
`super-harness doc check` (exit 0), `super-harness decision check` (exit 0).

**Step 5: Commit**

```bash
git add .harness/derived-docs.yaml docs/state-machine.md docs/cli-reference.md docs/getting-started.md
git commit -m "docs(doc-check): self-host registry + generated state-machine.md + cli-reference regen"
```

---

## Task 7: `init` ships a discoverable skeleton registry

**Files:**
- Create: `src/super_harness/templates/derived_docs_defaults.yaml`
- Modify: `src/super_harness/cli/init.py` (`_derived_docs_default()` + add
  `"derived-docs.yaml": _derived_docs_default()` to the dict returned by
  `_skeleton_files()` — there is no module-level `_FILES`; the skeleton is built by
  that function, beside `"source-paths.yaml"` at ~line 132)
- Modify: `docs/cli-reference.md` if `init`'s surface changed (it doesn't — skip)
- Test: extend `tests/unit/cli/test_init.py` (assert `.harness/derived-docs.yaml`
  is written and is a commented, valid-empty skeleton that `load_derived_docs`
  treats as "no docs", i.e. `docs == []`)

**Step 1:** Template `src/super_harness/templates/derived_docs_defaults.yaml`:

```yaml
# Register docs your generator can re-emit, so `super-harness doc check` guards
# them against drift (and `--fix` regenerates them). Each entry: a repo-relative
# `path` + a `command` that prints the doc's canonical content to stdout.
#
# derived_docs:
#   - path: docs/api-reference.md
#     command: python -m my_pkg.gen_api --emit
derived_docs: []
```

**Step 2:** Write the failing init test, then mirror `_source_paths_default()`:

```python
def _derived_docs_default() -> str:
    src = _TEMPLATES.joinpath("derived_docs_defaults.yaml")
    try:
        return src.read_text(encoding="utf-8")
    except OSError:
        return "derived_docs: []\n"
```

Add `"derived-docs.yaml": _derived_docs_default(),` to the dict returned by
`_skeleton_files()` (beside `"source-paths.yaml"`).

**Step 3: Run green** (init test + `load_derived_docs(skeleton) == ([], [])`) + ruff
+ mypy.

**Step 4:** `super-harness doc check --fix && super-harness doc check` (cli-reference
unaffected by init; should stay clean).

**Step 5: Commit**

```bash
git add src/super_harness/templates/derived_docs_defaults.yaml src/super_harness/cli/init.py tests/unit/cli/test_init.py
git commit -m "feat(init): ship discoverable skeleton derived-docs.yaml"
```

---

## Task 8: CI wiring — standalone `doc-check.yml`, drop `cli-reference-drift`, adopter template

**Files:**
- Create: `.github/workflows/doc-check.yml`
- Modify: `.github/workflows/test.yml` (delete the `cli-reference-drift` job)
- Modify: `src/super_harness/templates/super_harness_workflow.yml` (add a
  `doc-conformance` job mirroring the `decision-conformance` job)
- Test: extend `tests/unit/templates/test_super_harness_workflow.py` (assert the
  template now contains a `doc check` step)

**Step 1:** `.github/workflows/doc-check.yml` (mirror `decision-check.yml`):

```yaml
# .github/workflows/doc-check.yml
name: doc-check
on:
  pull_request: {}

jobs:
  doc-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - name: Install package
        run: pip install -e ".[dev]"
      - name: Derivable-doc conformance (regen-and-diff)
        run: super-harness doc check
```

**Step 2:** Delete the entire `cli-reference-drift:` job block from
`.github/workflows/test.yml`.

**Step 3:** In `super_harness_workflow.yml`, add a job keyed **`doc-check`** by
copying the existing **`decision-check`** job block (key at ~line 167) *verbatim* —
including its `if: github.event_name == 'pull_request'`, `permissions: { contents:
read }`, and `pipx install super-harness==0.1.0` lines (the adopter template installs
via `pipx`, NOT the `pip install -e ".[dev]"` form used by the own-repo standalone
workflow) — changing only the job name and the final run to `super-harness doc check`.

**Step 4:** Write a FRESH assertion in `tests/unit/templates/test_super_harness_workflow.py`
(there is no existing per-step assertion to mirror — the current tests are
security-shape checks): parse the template YAML and assert a `doc-check` job whose
step runs `super-harness doc check`. Leave the stale on-merge docstring for the
template-redesign slice (editing it standalone needs a fresh attestation for a 1-line
comment — disproportionate; OPEN-ITEMS). Run green + ruff + mypy + `super-harness
doc check`.

**Step 5: Commit**

```bash
git add .github/workflows/doc-check.yml .github/workflows/test.yml src/super_harness/templates/super_harness_workflow.yml tests/unit/templates/test_super_harness_workflow.py
git commit -m "ci(doc-check): standalone doc-check workflow; retire cli-reference-drift job"
```

---

## Task 9: Final validation + ruleset swap (ops) + regen sweep

**Step 1:** Full green invariant one more time from a clean shell:
`export PATH="$PWD/.venv/bin:$PATH"` then `pytest -q`, `ruff check .`, `mypy src`,
`super-harness doc check` (exit 0), `super-harness decision check` (exit 0).

**Step 2:** Confirm no derived doc is stale: `super-harness doc check` clean; if not,
`super-harness doc check --fix` and amend the Task 6 commit / add a regen commit.

**Step 3 (ops — MERGE-BLOCKING, do not treat as optional).** Task 8 deletes the
`cli-reference-drift` job, but ruleset `17229037` still *requires* it — a
deleted-but-required check **never reports → this PR's own branch is permanently
un-mergeable** until the ruleset is edited. Single-commit atomicity does NOT fix this
(the ruleset is repo-level, edited via `gh api`, not via PR commits). So treat this as
a verified pre-merge gate:

1. **`doc-check` must report at least once first.** GitHub only lets you add a check
   to the required set after it has been *seen* on a run. So open the PR, let
   `doc-check.yml` run once on it, confirm `doc-check` appears in the available
   checks, THEN edit the ruleset.
2. **Swap, then verify:** via `gh api repos/<owner>/<repo>/rulesets/17229037` (or the
   Web UI), **remove `cli-reference-drift` and add `doc-check`** to required checks.
   Re-read the ruleset and confirm the swap actually applied before merging.
3. **Fallback (token may lack scope — memory `reference-gh-pr-edit-needs-read-org`):**
   if the local token cannot edit the ruleset, the PR body MUST instruct the repo
   owner to do the swap in the Web UI, and **merge is blocked until the swap is
   confirmed applied** — not merely "documented". Do not merge on the assumption it
   will be done later.

**Step 4:** Update the repo's green-invariant discipline note (wherever the
`python -m scripts.gen_cli_reference --check` line lives — session docs / any
CONTRIBUTING) → `super-harness doc check`.

**Step 5:** Dogfood close-out (design + memory `project-self-host-pr-attest-scope`):
`super-harness change start 2026-06-11-doc-leg` → `plan ready --scope <every changed
file>` (MUST declare scope or merge-gate attest-verify covers 0 files) → review
approve → implementation start → done → review approve code-reviewer → `attest`
(commit `.harness/attestations/<slug>.jsonl`). Then open ONE PR to `main`.

---

## Deferred (do NOT do here — design §8)

Prose `@decision:` anchors in docs; the change→re-review proxy (teeth);
decision-link field on a derived doc; `doc check [PATH]` single-doc filter; per-entry
/ CLI timeout override; the getting-started state-prose de-duplication + 10-vs-11
reconciliation (separate docs pass with OPEN-ITEMS #6); sedimentation arm.
