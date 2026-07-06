"""Generate `docs/cli-reference.md` by introspecting the click command tree.

Walks the root click group, recursively visits every command + subgroup, and
emits a markdown reference. This script is a dumb generator: it only emits to
stdout. The `super-harness doc check` gate owns regen-and-diff and is the
single source of truth for keeping the committed doc in sync.

    python -m scripts.gen_cli_reference --emit    # print reference to stdout

The registry behind `super-harness doc check` invokes this generator with
`--emit`, captures stdout, and diffs (or, with `--fix`, writes) the result.
Run `super-harness doc check --fix` to regenerate the committed doc.

The generator is pure stdlib + click introspection. No jinja, no markdown
library — markdown is simple enough to format by hand.

Markdown-injection hardening (Phase 14 brace-injection lesson sibling):
- `|` chars in help text / choice enums must escape to `\\|` so they don't
  break the GFM table cell separator.
- Newlines inside help text collapse to a single space (a `\\n` inside a
  table cell breaks the row).
- We do NOT use `str.format(**vars)` on docstring content (attacker-
  influenced strings + curly braces would crash or worse).

Read-side error-family catch (Phase 14 lesson sibling):
- `run_check` reads the on-disk file with `(OSError, UnicodeDecodeError)`
  catch tuple. `UnicodeDecodeError` is a `ValueError`, NOT an `OSError`, so
  must be listed explicitly.
- Missing file → treat as drift (exit 1), not crash.

Exit codes are sourced from a hand-maintained per-command map below
(`_EXIT_CODES`). Per cli-command-surface §2.3 spec. v0.1 — drift between
spec + map is caught by this generator's CI drift guard.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path
from typing import Any

import click

# ---------------------------------------------------------------------------
# Exit-code reference map (hand-maintained per cli-command-surface §2.3).
# ---------------------------------------------------------------------------
# Keys are space-separated command paths under `super-harness ...` (without
# the `super-harness` prefix). Values are short markdown lines describing
# each exit code the command emits. Commands omitted from this map fall
# back to the generic `0 success / 1 generic` boilerplate.
_EXIT_CODES: dict[str, list[str]] = {
    "init": [
        "`0` success",
        "`1` generic error",
        "`3` already initialized (no `--force`)",
        "`4` `gh` CLI missing (with `--setup-github`)",
        "`5` concurrent init lock contention",
    ],
    "change start": [
        "`0` success",
        "`1` generic error",
        "`2` slug invalid or duplicate",
        "`3` no `.harness/` (run `init` first)",
        "`5` `state.yaml` lock contention",
    ],
    "plan ready": [
        "`0` success",
        "`1` generic error",
        "`2` illegal lifecycle transition or malformed `--scope`",
        "`3` no `.harness/`",
        "`5` `state.yaml` lock contention",
    ],
    # `plan redeclare` shares `plan ready`'s emit path but takes no `--scope` (so no
    # malformed-scope `2`) and cannot reach `5`: the emit path (EventWriter.emit /
    # refresh_state_after_emit) takes state.yaml via blocking fcntl.LOCK_EX, and
    # EXIT_CONCURRENCY is not emitted by any CLI path today. Accurate set: 0/1/2/3.
    "plan redeclare": [
        "`0` success",
        "`1` generic error",
        "`2` illegal lifecycle transition (terminal state / not-yet-started slug)",
        "`3` no `.harness/`",
    ],
    "change abandon": [
        "`0` success",
        "`1` generic error",
        "`3` no `.harness/`",
        "`5` lock contention",
    ],
    "change list": [
        "`0` success",
        "`1` generic error",
        "`3` no `.harness/`",
    ],
    "change resume": [
        "`0` success (or no active change — empty output)",
        "`1` generic error",
        "`2` explicit slug unknown",
        "`3` no `.harness/`",
    ],
    "verify": [
        "`0` verdict pass",
        "`1` sensor crashed or timed out",
        "`2` verdict fail / config validation error / `--pr` malformed metadata",
        "`3` `.harness/verification.yaml` missing",
        "`4` `--pr` resolution failure (gh fetch / no block / missing Change field)",
    ],
    "done": [
        "`0` success (verification passed → `implementation_complete` emitted)",
        "`1` sensor crashed or timed out",
        "`2` verification failed or pre-flight state gate failed",
        "`3` no `.harness/` or missing verification config",
        "`4` `--pr` resolution failure",
    ],
    "status": [
        "`0` success",
        "`1` generic error",
        "`3` no `.harness/`",
    ],
    "gate check": [
        "`0` allow",
        "`1` generic error / unimplemented cold-path gate",
        "`2` deny (gate decision is `deny`)",
        "`3` no `.harness/`",
    ],
    "pr validate": [
        "`0` valid",
        "`1` generic error",
        "`2` invalid metadata or lifecycle violation",
        "`3` no `.harness/`",
        "`4` `gh` CLI failure",
    ],
    "pr emit-opened": [
        "`0` success",
        "`1` generic error",
        "`3` no `.harness/`",
        "`4` `gh` CLI failure",
    ],
    "on-merge": [
        "`0` success",
        "`1` generic error",
        "`3` no `.harness/`",
    ],
    "attest write": [
        "`0` success",
        "`1` no events for the slug (run the lifecycle first)",
        "`3` no `.harness/`",
    ],
    "attest verify": [
        "`0` pass (every changed file covered)",
        "`2` blocker(s): uncovered file / scope drift / incomplete lifecycle",
        "`3` no `.harness/`",
        "`4` `git` failure (fail-closed; e.g. unreachable merge-base)",
    ],
    "sync": [
        "`0` success",
        "`1` generic error",
        "`3` no `.harness/`",
    ],
    "adapter install": [
        "`0` installed",
        "`1` generic error",
        "`2` unknown adapter name",
        "`3` no `.harness/`",
    ],
    "adapter uninstall": [
        "`0` uninstalled",
        "`1` generic error",
        "`2` unknown adapter or not installed",
        "`3` no `.harness/`",
    ],
    "adapter list": [
        "`0` success",
        "`1` generic error",
        "`3` no `.harness/`",
    ],
    "adapter scan-once": [
        "`0` success (zero or more events emitted)",
        "`1` generic error (e.g. AgentAdapter passed)",
        "`2` precondition violation on emitted event",
        "`3` no `.harness/`",
    ],
    "verification register": [
        "`0` registered",
        "`1` generic error",
        "`2` validation error / id conflict",
        "`3` no `.harness/`",
    ],
    "state rebuild": [
        "`0` success",
        "`1` generic error",
        "`3` no `.harness/`",
    ],
    "state verify": [
        "`0` clean",
        "`1` generic error",
        "`2` invariant violation",
        "`3` no `.harness/`",
    ],
    "event log": [
        "`0` success",
        "`1` generic error",
        "`3` no `.harness/`",
    ],
    "sensor list": [
        "`0` success",
        "`1` generic error",
    ],
    "gate list": [
        "`0` success",
        "`1` generic error",
    ],
    "daemon start": [
        "`0` running",
        "`1` failed to start",
    ],
    "daemon stop": [
        "`0` stopped",
        "`1` generic error",
    ],
    "daemon status": [
        "`0` running",
        "`1` stopped or stale PID",
    ],
    "decision new": [
        "`0` success",
        "`2` invalid id, or id already exists (case-folded)",
        "`3` no `.harness/` (run `init` first)",
    ],
    "decision ratify": [
        "`0` success",
        "`2` no such decision, or not in `proposed` state",
        "`3` no `.harness/`",
    ],
    "decision supersede": [
        "`0` success",
        "`2` missing decision, or successor not ratified",
        "`3` no `.harness/`",
    ],
    "decision retire": [
        "`0` success",
        "`2` no such decision",
        "`3` no `.harness/`",
    ],
    "decision list": ["`0` success", "`3` no `.harness/`"],
    "decision show": ["`0` success", "`2` no such decision", "`3` no `.harness/`"],
    "decision reconcile": [
        "`0` success",
        "`2` not a ratified tier-2 decision, or no code anchors",
        "`3` no `.harness/`",
    ],
    "decision betray": [
        "`0` success",
        "`2` not a ratified tier-2 decision (or missing --justification)",
        "`3` no `.harness/`",
    ],
    "decision check": [
        "`0` clean, or only warnings (dangling-down / tier-2 review-needed)",
        "`2` dangling-up / integrity / tier-1 check failure; or (with --gate-reconcile) "
        "a suspect/unreconciled tier-2 decision",
        "`3` record/config error or no `.harness/`",
    ],
    "doc check": [
        "`0` all derived docs in sync (or registry absent)",
        "`2` one or more docs drifted",
        "`3` registry malformed or `.harness/` missing",
        "`4` a generator command failed",
    ],
    "review prepare": [
        "`0` bundle written",
        "`2` validation error (dirty in-scope tree / git failure — fail-closed)",
        "`3` no `.harness/`",
    ],
    "review approve": [
        "`0` verdict recorded (`plan_approved` / `code_review_passed` emitted)",
        "`2` verdict gate failed — code-reviewer: bare / incomplete checklist / stale "
        "digest; ANY reviewer whose verdict has a failing checklist item (use "
        "`review reject`); or malformed `--verdict-file`",
        "`3` no `.harness/`",
    ],
    "review skip": [
        "`0` skip recorded (`code_review_passed` / `plan_approved` emitted, `skipped=True`)",
        "`2` --override without --reason",
        "`3` no `.harness/`",
    ],
}


# ---------------------------------------------------------------------------
# Markdown helpers
# ---------------------------------------------------------------------------

def _md_escape_cell(text: str) -> str:
    """Escape a string for safe embedding inside a GFM table cell.

    Replaces `|` with `\\|` (would otherwise terminate the cell) and collapses
    embedded newlines to a single space (would otherwise break the row).
    Backticks pass through unchanged — they render fine in GFM cells.
    """
    return text.replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _type_repr(param: click.Parameter) -> str:
    """Render a click parameter's type as a short markdown-safe string."""
    t = param.type
    if isinstance(t, click.Choice):
        return "{" + "|".join(t.choices) + "}"
    # click.types names: text / integer / float / boolean / path / directory / ...
    name = getattr(t, "name", None) or type(t).__name__
    if isinstance(param, click.Option) and param.is_flag:
        return "flag"
    if isinstance(param, click.Option) and param.multiple:
        return f"{name} (repeatable)"
    return str(name)


def _default_repr(param: click.Parameter) -> str:
    """Render a click parameter's default in a short, markdown-safe form."""
    if isinstance(param, click.Option) and param.is_flag:
        return "`False`" if not param.default else "`True`"
    if param.required:
        return "*required*"
    default = param.default
    if default is None:
        return "—"
    if callable(default):
        return "*dynamic*"
    if isinstance(default, (list, tuple)) and not default:
        return "—"
    # click 8.4 uses a Sentinel.UNSET singleton for "default not specified" on
    # optional positional args. Render that as em-dash, same as None, to keep
    # the table noise-free.
    default_repr = repr(default)
    if "Sentinel.UNSET" in default_repr or default_repr.startswith("<Sentinel"):
        return "—"
    return f"`{default_repr}`"


def _param_name_repr(param: click.Parameter) -> str:
    """Render the user-visible parameter name(s)."""
    if isinstance(param, click.Option):
        # Sort so the long form comes first for readability
        opts = sorted(param.opts, key=lambda o: (not o.startswith("--"), o))
        return ", ".join(f"`{o}`" for o in opts)
    # Argument
    if isinstance(param, click.Argument):
        return f"`{_arg_metavar(param)}`"
    return f"`{param.name}`"


def _param_help(param: click.Parameter) -> str:
    """Pull a click parameter's help text (options only; arguments have none)."""
    if isinstance(param, click.Option):
        return param.help or ""
    return ""


def _arg_metavar(param: click.Argument) -> str:
    """Return the visible metavar for a click argument (compat across click versions)."""
    # click 8.4 requires a ctx to make_metavar; older versions don't. Build a
    # throwaway context tied to a dummy parent command so we never crash.
    try:
        ctx = click.Context(click.Command(name="_dummy"))
        return param.make_metavar(ctx)
    except TypeError:
        # Older click signature (no ctx) — fallback
        return param.make_metavar()  # type: ignore[call-arg]


def _synopsis_with_name(cmd: click.Command, path: list[str], name: str) -> str:
    """Like _synopsis but uses an explicit final command name (handles root)."""
    parts = [*path, name]
    line = " ".join(p for p in parts if p).strip()
    # Click groups don't take arguments; leaf commands may have args
    has_options = any(
        isinstance(p, click.Option) and p.name != "help" for p in cmd.params
    )
    if has_options:
        line += " [OPTIONS]"
    elif not isinstance(cmd, click.Group):
        # Even leafs with only --help get a placeholder so the synopsis is uniform
        line += " [OPTIONS]"
    # Append positional metavars in order
    for param in cmd.params:
        if isinstance(param, click.Argument):
            line += f" {_arg_metavar(param)}"
    return line


def _brief(cmd: click.Command) -> str:
    """One-paragraph description from click's help/short_help fields.

    `cmd.help` may contain the full callback docstring (multi-paragraph). We
    take the FIRST paragraph (up to the first blank line) so the reference
    stays scannable. Full docstrings are still discoverable via `--help` on
    the live binary.
    """
    text = cmd.short_help or cmd.help or ""
    # Take only the first paragraph (split on a blank line)
    para = text.strip().split("\n\n", 1)[0]
    # Collapse intra-paragraph newlines + whitespace runs
    return " ".join(para.split())


def _iter_params_for_table(cmd: click.Command) -> list[click.Parameter]:
    """Return params worth listing in the table (skip the universal `--help`)."""
    out = []
    for p in cmd.params:
        if isinstance(p, click.Option) and p.name == "help":
            continue
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# Section rendering
# ---------------------------------------------------------------------------

def _render_command_section(
    cmd: click.Command,
    path: list[str],
    root_name: str,
    *,
    is_root: bool,
) -> list[str]:
    """Render a single command's markdown section. Returns a list of lines.

    `path` lists the command names from root DOWN TO this command (inclusive
    of this command's name, exclusive of root_name). `is_root` flags the
    initial call so the root group's heading shows only `root_name`.
    """
    if is_root:
        full_path_parts = [root_name]
        # For root, the synopsis omits the click cmd.name (root_name IS the cmd)
        synopsis_path: list[str] = []
        synopsis_cmd_name = root_name
    else:
        full_path_parts = [root_name, *path]
        synopsis_path = [root_name, *path[:-1]]
        synopsis_cmd_name = path[-1]
    heading = " ".join(full_path_parts).strip()
    lines: list[str] = []
    lines.append(f"## {heading}")
    lines.append("")
    brief = _brief(cmd)
    if brief:
        lines.append(brief)
        lines.append("")
    # Synopsis block (fenced for copy-paste). For groups, hint COMMAND.
    syn = _synopsis_with_name(cmd, synopsis_path, synopsis_cmd_name)
    if isinstance(cmd, click.Group):
        syn += " COMMAND [ARGS...]"
    lines.append("```")
    lines.append(syn)
    lines.append("```")
    lines.append("")

    # Parameter table (skip for empty groups with no flags of their own)
    params = _iter_params_for_table(cmd)
    if params:
        lines.append("| Param | Type | Default | Description |")
        lines.append("|-------|------|---------|-------------|")
        for p in params:
            name_cell = _md_escape_cell(_param_name_repr(p))
            type_cell = _md_escape_cell(_type_repr(p))
            default_cell = _md_escape_cell(_default_repr(p))
            help_cell = _md_escape_cell(_param_help(p))
            lines.append(
                f"| {name_cell} | {type_cell} | {default_cell} | {help_cell} |"
            )
        lines.append("")

    # Exit codes (only for leaves — groups are dispatchers, no exit code of own)
    if not isinstance(cmd, click.Group):
        key = " ".join(path).strip()
        codes = _EXIT_CODES.get(key)
        if codes is None:
            codes = ["`0` success", "`1` generic error"]
        lines.append("**Exit codes:**")
        lines.append("")
        for c in codes:
            lines.append(f"- {c}")
        lines.append("")

    return lines


def _walk(
    cmd: click.Command,
    path: list[str],
    root_name: str,
    out: list[str],
    *,
    is_root: bool,
) -> None:
    """Depth-first walk: render this command, then recurse into subcommands.

    `path` lists the command names from root DOWN TO this command (inclusive
    of this command's name, exclusive of root_name). For the root call,
    `path == []` and `is_root=True`. Subcommands are visited in alphabetical
    order for deterministic output.
    """
    out.extend(_render_command_section(cmd, path, root_name, is_root=is_root))
    if isinstance(cmd, click.Group):
        for sub_name in sorted(cmd.commands.keys()):
            sub_cmd = cmd.commands[sub_name]
            _walk(
                sub_cmd,
                [*path, sub_name],
                root_name,
                out,
                is_root=False,
            )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_HEADER_NOTICE = (
    "<!-- AUTOGENERATED by scripts/gen_cli_reference.py — do not edit by hand. "
    "Regenerate with: super-harness doc check --fix -->"
)


def render_markdown(root: click.Command, *, root_name: str) -> str:
    """Render the full markdown reference for a click root group."""
    lines: list[str] = []
    lines.append(_HEADER_NOTICE)
    lines.append("")
    lines.append(f"# `{root_name}` CLI reference")
    lines.append("")
    lines.append(
        "Generated from the live click command tree. Every leaf command has a "
        "synopsis, param table, and exit-code list. For exit-code semantics see "
        "`cli-command-surface` §2.2."
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    # Render the root group itself, then recurse
    _walk(root, path=[], root_name=root_name, out=lines, is_root=True)
    # Ensure trailing newline + collapse any accidental triple-blank runs
    text = "\n".join(lines).rstrip() + "\n"
    return text


def write_reference(
    root: click.Command, *, root_name: str, target: Path
) -> None:
    """Render + write the reference to `target` (creates parent dirs)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_markdown(root, root_name=root_name), encoding="utf-8")


def run_check(
    root: click.Command, *, root_name: str, target: Path
) -> int:
    """Compare on-disk `target` against freshly generated content.

    Returns 0 if in-sync, 1 on drift (or missing/undecodable target). Prints
    a unified diff to stderr on drift.
    """
    generated = render_markdown(root, root_name=root_name)
    try:
        on_disk = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # Missing file or unreadable bytes → treat as drift, not crash.
        # UnicodeDecodeError is a ValueError (NOT an OSError) so it must be
        # listed explicitly — Phase 14 lesson sibling.
        print(
            f"[gen_cli_reference] cannot read {target}: {exc}; "
            "treating as drift.",
            file=sys.stderr,
        )
        return 1
    if on_disk == generated:
        return 0
    diff = difflib.unified_diff(
        on_disk.splitlines(keepends=True),
        generated.splitlines(keepends=True),
        fromfile=str(target),
        tofile=f"{target} (regenerated)",
        n=3,
    )
    sys.stderr.writelines(diff)
    print(
        "\n[gen_cli_reference] DRIFT: regenerate with "
        "`super-harness doc check --fix`",
        file=sys.stderr,
    )
    return 1


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="gen_cli_reference",
        description=(
            "Emit the CLI reference markdown to stdout. The "
            "`super-harness doc check` gate owns regen-and-diff."
        ),
    )
    parser.add_argument(
        "--emit",
        action="store_true",
        required=True,
        help="Print the rendered CLI reference markdown to stdout.",
    )
    parser.parse_args(argv)

    # Import here so test fixtures can use _build_fixture_group without
    # pulling in the full super-harness CLI at module import time.
    from super_harness.cli import main as cli_main

    root_name = "super-harness"
    # render_markdown already ends with a single trailing newline; do not add
    # another so the emitted bytes match the committed doc exactly.
    sys.stdout.write(render_markdown(cli_main, root_name=root_name))
    return 0


def _entrypoint() -> Any:
    sys.exit(main())


if __name__ == "__main__":
    _entrypoint()
