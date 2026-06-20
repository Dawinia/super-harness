"""`decision` subgroup — author, ratify, and check decision records.

See docs/plans/2026-06-08-decision-records-anchors-design.md §6. Each verb does
exactly one thing (no hidden cross-entity side effects).
"""
from __future__ import annotations

import sys
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import Status, json_envelope
from super_harness.core.anchor_scanner import scan_sentinel_locations
from super_harness.core.check_runner import (
    bite_test,
    changed_files,
    run_executable_checks,
    select_changed,
)
from super_harness.core.clock import utc_now_iso
from super_harness.core.decision_check import (
    ALWAYS_EXCLUDE,
    ANCHOR_KEYWORD,
    fingerprint_file,
    run_check,
)
from super_harness.core.decisions import (
    Decision,
    compute_body_hash,
    decision_tier,
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
    path = decisions_dir(root) / f"{decision_id}.md"
    write_decision(Decision(id=decision_id, status="proposed", body=text, path=path))
    click.echo(f"created {path.relative_to(root)} (proposed)")
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
    try:
        return parse_decision_file(path)
    except ValueError as e:
        click.echo(
            format_error(subcommand=sub, message=f"{decision_id!r} is malformed: {e}",
                         hint="Fix the decision file (frontmatter or check/counterexample block)."),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)


@decision_group.command("ratify")
@click.argument("decision_id")
@click.option("--dry-run", is_flag=True, help="Run the bite-test only; do not ratify.")
@click.pass_context
def ratify_cmd(ctx: click.Context, decision_id: str, dry_run: bool) -> None:
    """Mark a proposed decision ratified (stamps who/when + bite-tests its check)."""
    root = _resolve(ctx, "decision ratify")
    d = _load_one(root, "decision ratify", decision_id)
    if d.status not in ("proposed", "ratified"):
        click.echo(
            format_error(subcommand="decision ratify",
                         message=f"{decision_id!r} is {d.status}, not proposed/ratified",
                         hint="Only a proposed or already-ratified decision can be ratified."),
            err=True,
        )
        sys.exit(EXIT_VALIDATION)

    # Re-ratify re-runs the bite-test on purpose: a tier-1 decision cannot be
    # (re-)ratified while its current code violates the check (ratification attests
    # the decision currently holds in full, so the pass side must pass right now).
    if d.check is not None:                       # tier-1 -> must prove it bites
        if d.counterexample is None:
            click.echo(format_error(subcommand="decision ratify",
                       message=f"{decision_id!r} has a check but no counterexample",
                       hint="Add a ```counterexample path=<rel> block, or remove the check."),
                       err=True)
            sys.exit(EXIT_VALIDATION)
        try:
            verdict = bite_test(root, d.check, d.counterexample)
        except (ValueError, OSError) as e:        # malformed counterexample / residual fs error
            click.echo(format_error(subcommand="decision ratify",
                       message=f"bite-test could not run: {e}",
                       hint="Fix the counterexample block."), err=True)
            sys.exit(EXIT_VALIDATION)
        if not verdict.ok:
            click.echo(f"BITE-TEST FAILED: {verdict.reason}", err=True)
            sys.exit(EXIT_VALIDATION)
        click.echo(f"bite-test: {verdict.reason}")
        if dry_run:
            sys.exit(EXIT_OK)
    elif dry_run:
        click.echo("no check block (tier-3 context) - nothing to bite-test")
        sys.exit(EXIT_OK)

    d.status = "ratified"
    d.ratified_by = resolve_identity(root)
    d.ratified_at = utc_now_iso()
    d.ratified_text_hash = compute_body_hash(d.body)
    write_decision(d)
    click.echo(f"ratified {decision_id} (by {d.ratified_by})")
    sys.exit(EXIT_OK)


@decision_group.command("reconcile")
@click.argument("decision_id")
@click.option("--justification", default="", help="Why the code still satisfies the criterion.")
@click.option("--kind", type=click.Choice(["self", "independent"]), default="self",
              help="Disclosure: self-review (same actor as the change) or independent reviewer.")
@click.pass_context
def reconcile_cmd(ctx: click.Context, decision_id: str, justification: str, kind: str) -> None:
    """Record a tier-2 re-review verdict (code still satisfies D); re-stamp the baseline."""
    root = _resolve(ctx, "decision reconcile")
    d = _load_one(root, "decision reconcile", decision_id)
    if d.status != "ratified" or decision_tier(d) != 2:
        click.echo(format_error(subcommand="decision reconcile",
                   message=f"{decision_id!r} is not a ratified tier-2 (reviewable) decision",
                   hint="reconcile applies only to a ratified decision with a ```review block."),
                   err=True)
        sys.exit(EXIT_VALIDATION)
    include, exclude = load_source_scope(root)
    locs = scan_sentinel_locations(root, file_globs=include, keyword=ANCHOR_KEYWORD,
                                   exclude_globs=exclude + ALWAYS_EXCLUDE).get(decision_id, [])
    anchored = sorted({f for f, _ln in locs})
    if not anchored:
        click.echo(format_error(subcommand="decision reconcile",
                   message=f"{decision_id!r} has no code anchors to reconcile",
                   hint=f"Anchor the code with `# @decision:{decision_id}` first."), err=True)
        sys.exit(EXIT_VALIDATION)
    d.reconciled_anchors = {f: fingerprint_file(root, f) for f in anchored}
    d.last_reconciled_by = resolve_identity(root)
    d.last_reconciled_at = utc_now_iso()
    d.last_reconcile_kind = kind
    d.last_betrayed_by = d.last_betrayed_at = d.last_betray_justification = None
    write_decision(d)
    click.echo(f"reconciled {decision_id} ({len(anchored)} file(s), kind={kind}, "
               f"by {d.last_reconciled_by})")
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
@click.option("--changed", is_flag=True, help="Only run checks whose anchored files moved.")
@click.option("--gate-reconcile", is_flag=True,
              help="Merge-boundary teeth: exit 2 on any suspect/unreconciled tier-2 "
                   "decision (default mode only warns).")
@click.pass_context
def check_cmd(ctx: click.Context, changed: bool, gate_reconcile: bool) -> None:
    """Whole-repo dangling check + executable checks: up=block(2) / down=warn / record error=3.

    Honors the GLOBAL --json flag (ctx.obj["json"]) → frozen json_envelope shape.
    """
    root = _resolve(ctx, "decision check")
    result = run_check(root)                       # pure layer, unchanged

    decisions, _ = load_decisions(root)
    violated = {v.id for v in result.integrity_violations}
    ratified_tier1 = [d for d in decisions
                      if d.status == "ratified" and d.check and d.id not in violated]
    to_run = ratified_tier1
    if changed:
        cf = changed_files(root)
        if cf is not None:                        # None -> not a git repo -> FULL (never under-run)
            include, exclude = load_source_scope(root)
            amap = scan_sentinel_locations(root, file_globs=include, keyword=ANCHOR_KEYWORD,
                                           exclude_globs=exclude + ALWAYS_EXCLUDE)
            to_run = select_changed(ratified_tier1, amap, cf)
    check_failures = run_executable_checks(root, to_run) if not result.errors else []

    hard = len(ratified_tier1)
    context = sum(1 for d in decisions if d.status == "ratified" and not d.check)

    status: Status
    if result.errors:
        exit_code, status = EXIT_NO_CONFIG, "fail"
    elif result.integrity_violations or check_failures or result.dangling_up:
        exit_code, status = EXIT_VALIDATION, "fail"
    elif gate_reconcile and (result.suspect_tier2 or result.unreconciled_tier2):
        exit_code, status = EXIT_VALIDATION, "fail"
    elif (result.dangling_down or result.unhashed_ratified
          or result.suspect_tier2 or result.unreconciled_tier2):
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
                    "integrity_violations": [
                        {"id": v.id, "file": v.file}
                        for v in result.integrity_violations
                    ],
                    "unhashed_ratified": list(result.unhashed_ratified),
                    "check_failures": [
                        {"id": f.id, "exit_code": f.exit_code, "detail": f.detail}
                        for f in check_failures
                    ],
                    "suspect_tier2": [
                        {"id": s.id, "changed_files": s.changed_files}
                        for s in result.suspect_tier2
                    ],
                    "unreconciled_tier2": list(result.unreconciled_tier2),
                    "hard_context": {"hard": hard, "context": context},
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
        for v in result.integrity_violations:
            click.echo(
                f"INTEGRITY-LOCK {v.file} @decision:{v.id} "
                f"(ratified body changed without re-ratification → re-ratify)",
                err=True,
            )
        for f in check_failures:
            click.echo(f"CHECK-FAILED @decision:{f.id} (exit {f.exit_code}: {f.detail})",
                       err=True)
        for did in result.unhashed_ratified:
            click.echo(f"warning: {did} ratified before text-lock (no hash; "
                       f"re-ratify to lock)")
        for d in result.dangling_up:
            click.echo(
                f"DANGLING-UP {d.file}:{d.line} @decision:{d.id} (no ratified decision)",
                err=True,
            )
        for did in result.dangling_down:
            click.echo(f"warning: dangling-down {did} (ratified, no code anchor)")
        for did in result.unreconciled_tier2:
            click.echo(f"REVIEW-NEEDED {did} (tier-2, never reconciled — run "
                       f"`decision reconcile {did}`)")
        for s in result.suspect_tier2:
            files = ", ".join(s.changed_files)
            click.echo(f"REVIEW-NEEDED {s.id} (tier-2, anchored code changed: {files} — "
                       f"re-review then `decision reconcile {s.id}` / `decision betray {s.id}`)")
        if gate_reconcile:
            for did in result.unreconciled_tier2:
                click.echo(f"GATE-RECONCILE {did}: tier-2 never reconciled", err=True)
            for s in result.suspect_tier2:
                click.echo(f"GATE-RECONCILE {s.id}: tier-2 anchored code changed, no reconcile",
                           err=True)
        ratio = f" ({round(100 * hard / (hard + context))}% hard)" if hard + context else ""
        click.echo(f"hard:context = {hard}:{context}{ratio}")
        if status == "pass":
            click.echo("decision check: clean")
    sys.exit(exit_code)
