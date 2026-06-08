"""`decision` subgroup — author, ratify, and check decision records.

See docs/plans/2026-06-08-decision-records-anchors-design.md §6. Each verb does
exactly one thing (no hidden cross-entity side effects).
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.core.clock import utc_now_iso
from super_harness.core.decisions import (
    Decision,
    decisions_dir,
    is_valid_id,
    parse_decision_file,
    write_decision,
)
from super_harness.core.identity import resolve_identity
from super_harness.core.paths import HarnessNotInitialized, find_harness_root
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
