"""`report` — roll up the event stream into an honest value summary (Stage 1).

Per docs/plans/2026-07-15-value-report-stage1.md. Reads only existing events;
emits nothing. Mirrors cli/status.py's find-root + json-envelope patterns.
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.engineering.value_report import ValueReport, build_value_report
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK


def _fmt_tokens(n: int) -> str:
    return f"~{n:,}" if n else "0"


def _bottom_line(r: ValueReport) -> str:
    if r.findings_resolved == 0 and r.undisclosed_bypasses == 0:
        return (
            "Bottom line: no measurable catches this window — nothing prevented "
            "that we can prove. On this evidence alone it is not earning its keep here."
        )
    parts = []
    if r.findings_resolved:
        parts.append(f"review earned its keep: {r.findings_resolved} real fix(es)")
    if r.undisclosed_bypasses:
        parts.append(f"{r.undisclosed_bypasses} undisclosed bypass(es) to investigate")
    return "Bottom line: " + "; ".join(parts) + "."


def _render_human(r: ValueReport) -> str:
    window = f"{r.since or 'all'} - {r.until or 'now'}"
    lines = [
        "super-harness - what it did for you",
        f"  window: {window} - {r.changes_touched} change(s)",
        "",
        "Caught for you",
        f"  - {r.findings_resolved} problem(s) review found and you fixed",
        f"  - {r.findings_open_undisposed} more review raised that are still open "
        "(no fix or waiver recorded)",
    ]
    if r.undisclosed_bypasses:
        lines.append(
            f"  - WARNING {r.undisclosed_bypasses} gate bypass(es) went undisclosed "
            "(the gate was defeated - worth a look)"
        )
    lines += [
        "",
        "Cost",
        f"  - review tokens: {_fmt_tokens(r.review_tokens)} "
        f"(review side only, self-reported; data for {r.review_runs_with_usage}/"
        f"{r.review_runs_total} runs; main coding-agent cost not captured)",
        f"  - review rework: {r.findings_wontfix} false alarm(s) (wontfix), "
        f"{r.rejected_rounds} rejected round(s)",
        "",
        f"  Note: the lifecycle gate, {r.armed_decisions} locked rule(s), verification "
        "and doc-sync also",
        "  stand guard in the prevention layer - their successful catches leave no "
        "trace yet (see Stage 2).",
        "",
        _bottom_line(r),
    ]
    return "\n".join(lines)


def _render_brief(r: ValueReport) -> str:
    window = f"{r.since or 'all'}-{r.until or 'now'}"
    bits = [f"caught {r.findings_resolved}", f"{_fmt_tokens(r.review_tokens)} review tokens"]
    if r.undisclosed_bypasses:
        bits.append(f"{r.undisclosed_bypasses} undisclosed bypass(es)")
    return f"{window}: " + ", ".join(bits) + "."


@click.command("report")
@click.option(
    "--since",
    default=None,
    help="Only count events on/after this ISO date (e.g. 2026-07-01). "
    "Unparseable = no lower bound (never errors).",
)
@click.option(
    "--until",
    default=None,
    help="Only count events on/before this ISO date. Unparseable = no upper bound.",
)
@click.option("--brief", is_flag=True, help="One-line summary only.")
@click.pass_context
def report_cmd(ctx: click.Context, since: str | None, until: str | None, brief: bool) -> None:
    """Show what the harness measurably did for you over a repo/time-window."""
    try:
        root = find_harness_root(Path(ctx.obj.get("workspace") or "."))
    except HarnessNotInitialized as e:
        click.echo(
            format_error(subcommand="report", message=e.message, hint=e.hint),
            err=True,
        )
        sys.exit(EXIT_NO_CONFIG)
    r = build_value_report(
        events_path(root), since=since, until=until, workspace_root=root
    )
    if ctx.obj.get("json"):
        click.echo(
            json_envelope(
                command="report", status="pass", exit_code=EXIT_OK, data=asdict(r)
            )
        )
    elif brief:
        click.echo(_render_brief(r))
    else:
        click.echo(_render_human(r))
    sys.exit(EXIT_OK)
