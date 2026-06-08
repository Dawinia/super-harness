"""`decision` subgroup — author, ratify, and check decision records.

See docs/plans/2026-06-08-decision-records-anchors-design.md §6. Each verb does
exactly one thing (no hidden cross-entity side effects).
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.anchor_scanner import scan_sentinel_locations
from super_harness.core.clock import utc_now_iso
from super_harness.core.decision_check import ALWAYS_EXCLUDE, ANCHOR_KEYWORD, run_check
from super_harness.core.decisions import (
    Decision,
    decisions_dir,
    is_valid_id,
    load_decisions,
    parse_decision_file,
    write_decision,
)
from super_harness.core.identity import resolve_identity
from super_harness.core.paths import HarnessNotInitialized, find_harness_root
from super_harness.core.source_scope import load_source_scope
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK, EXIT_VALIDATION


def _resolve(ctx: click.Context, sub: str) -> Path:
    try:
        return find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(format_error(subcommand=sub, message=e.message, hint=e.hint), err=True)
        sys.exit(EXIT_NO_CONFIG)


def _casefold_exists(root: Path, decision_id: str) -> bool:
    ddir = decisions_dir(root)
    if not ddir.is_dir():
        return False
    target = decision_id.casefold()
    return any(p.stem.casefold() == target for p in ddir.glob("*.md"))


@click.group("decision")
def decision_group() -> None:
    """Author, ratify, and check decision records."""


@decision_group.command("new")
@click.argument("decision_id")
@click.option("--text", "text", required=True, help="One-line decision.")
@click.pass_context
def new_cmd(ctx: click.Context, decision_id: str, text: str) -> None:
    """Create a `proposed` decision at docs/decisions/<id>.md."""
    root = _resolve(ctx, "decision new")
    if not is_valid_id(decision_id):
        click.echo(
            format_error(
                subcommand="decision new",
                message=f"invalid id {decision_id!r}",
                hint="Use lowercase [a-z0-9_-].",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    if _casefold_exists(root, decision_id):
        click.echo(
            format_error(
                subcommand="decision new",
                message=f"decision {decision_id!r} already exists",
                hint="Pick a different id or edit the existing record.",
            ),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    d = Decision(
        id=decision_id,
        status="proposed",
        body=text,
        path=decisions_dir(root) / f"{decision_id}.md",
    )
    write_decision(d)
    click.echo(f"created {d.path.relative_to(root)} (proposed)")
    sys.exit(EXIT_OK)


def _load_one(root: Path, sub: str, decision_id: str) -> Decision:
    path = decisions_dir(root) / f"{decision_id}.md"
    if not path.is_file():
        click.echo(
            format_error(subcommand=sub, message=f"no decision {decision_id!r}",
                         hint="Run `decision list` to see ids."),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    return parse_decision_file(path)


@decision_group.command("ratify")
@click.argument("decision_id")
@click.pass_context
def ratify_cmd(ctx: click.Context, decision_id: str) -> None:
    """Mark a proposed decision ratified (stamps who/when). Ratifies only this one."""
    root = _resolve(ctx, "decision ratify")
    d = _load_one(root, "decision ratify", decision_id)
    if d.status != "proposed":
        click.echo(
            format_error(subcommand="decision ratify",
                         message=f"{decision_id!r} is {d.status}, not proposed",
                         hint="Only a proposed decision can be ratified."),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    d.status = "ratified"
    d.ratified_by = resolve_identity(root)
    d.ratified_at = utc_now_iso()
    write_decision(d)
    click.echo(f"ratified {decision_id} (by {d.ratified_by})")
    sys.exit(EXIT_OK)


@decision_group.command("supersede")
@click.argument("old_id")
@click.option("--by", "new_id", required=True, help="The ratified successor id.")
@click.pass_context
def supersede_cmd(ctx: click.Context, old_id: str, new_id: str) -> None:
    """Retire <old_id> in favor of a ratified <new_id>; link both directions."""
    root = _resolve(ctx, "decision supersede")
    old = _load_one(root, "decision supersede", old_id)
    new = _load_one(root, "decision supersede", new_id)
    if new.status != "ratified":
        click.echo(
            format_error(subcommand="decision supersede",
                         message=f"successor {new_id!r} is {new.status}, not ratified",
                         hint="Ratify the successor first."),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)
    old.status = "superseded"
    old.superseded_by = new_id
    new.supersedes = old_id
    write_decision(old)
    write_decision(new)
    click.echo(f"superseded {old_id} by {new_id}")
    sys.exit(EXIT_OK)


@decision_group.command("retire")
@click.argument("decision_id")
@click.pass_context
def retire_cmd(ctx: click.Context, decision_id: str) -> None:
    """Retire a decision (tombstone): no successor, not anchorable, not dangling-down."""
    root = _resolve(ctx, "decision retire")
    d = _load_one(root, "decision retire", decision_id)
    d.status = "retired"
    write_decision(d)
    click.echo(f"retired {decision_id}")
    sys.exit(EXIT_OK)


@decision_group.command("list")
@click.option("--status", "status_filter", default=None,
              type=click.Choice(["proposed", "ratified", "superseded", "retired"]))
@click.option("--dangling", is_flag=True, help="Show ratified decisions with no code anchor.")
@click.pass_context
def list_cmd(ctx: click.Context, status_filter: str | None, dangling: bool) -> None:
    """List decisions (optionally filtered); --dangling shows the down set."""
    root = _resolve(ctx, "decision list")
    if dangling:
        for did in run_check(root).dangling_down:
            click.echo(f"{did}\tdangling-down")
        sys.exit(EXIT_OK)
    decisions, _ = load_decisions(root)
    for d in sorted(decisions, key=lambda x: x.id):
        if status_filter and d.status != status_filter:
            continue
        click.echo(f"{d.id}\t{d.status}")
    sys.exit(EXIT_OK)


@decision_group.command("show")
@click.argument("decision_id")
@click.pass_context
def show_cmd(ctx: click.Context, decision_id: str) -> None:
    """Show a decision's fields + the code anchors currently pointing at it."""
    root = _resolve(ctx, "decision show")
    d = _load_one(root, "decision show", decision_id)
    click.echo(f"id:     {d.id}")
    click.echo(f"status: {d.status}")
    if d.ratified_by:
        click.echo(f"ratified_by: {d.ratified_by}")
    if d.ratified_at:
        click.echo(f"ratified_at: {d.ratified_at}")
    if d.supersedes:
        click.echo(f"supersedes: {d.supersedes}")
    if d.superseded_by:
        click.echo(f"superseded_by: {d.superseded_by}")
    include, exclude = load_source_scope(root)
    locs = scan_sentinel_locations(
        root, file_globs=include, keyword=ANCHOR_KEYWORD,
        exclude_globs=exclude + ALWAYS_EXCLUDE,
    ).get(decision_id, [])
    click.echo("anchors:")
    for f, ln in sorted(locs):
        click.echo(f"  {f}:{ln}")
    sys.exit(EXIT_OK)


@decision_group.command("check")
@click.pass_context
def check_cmd(ctx: click.Context) -> None:
    """Whole-repo dangling check: up=block(2) / down=warn / record error=3.

    Honors the GLOBAL --json flag (ctx.obj["json"]) → frozen json_envelope shape.
    """
    root = _resolve(ctx, "decision check")
    result = run_check(root)
    if result.errors:
        exit_code, status = EXIT_NO_CONFIG, "fail"
    elif result.dangling_up:
        exit_code, status = EXIT_VALIDATION, "fail"
    elif result.dangling_down:
        exit_code, status = EXIT_OK, "warning"
    else:
        exit_code, status = EXIT_OK, "pass"

    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="decision check",
                status=status,
                exit_code=exit_code,
                data={
                    "dangling_up": [
                        {"id": d.id, "file": d.file, "line": d.line}
                        for d in result.dangling_up
                    ],
                    "dangling_down": list(result.dangling_down),
                },
                errors=[
                    {"code": e.kind, "message": e.detail, "file": e.file}
                    for e in result.errors
                ],
            )
        )
    else:
        for e in result.errors:
            click.echo(f"ERROR [{e.kind}] {e.file}: {e.detail}", err=True)
        for d in result.dangling_up:
            click.echo(
                f"DANGLING-UP {d.file}:{d.line} @decision:{d.id} (no ratified decision)",
                err=True,
            )
        for did in result.dangling_down:
            click.echo(f"warning: dangling-down {did} (ratified, no code anchor)")
        if status == "pass":
            click.echo("decision check: clean")
    sys.exit(exit_code)
