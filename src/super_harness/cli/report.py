"""`report` — roll up the event stream into an honest value summary (Stage 1).

Per docs/plans/2026-07-15-value-report-stage1.md (+ Stage 2, PR #83). Reads the
existing event stream plus the gate-block telemetry log
(`.harness/gate-blocks.jsonl`); emits nothing. Mirrors cli/status.py's find-root
+ json-envelope patterns.
"""
from __future__ import annotations

import sys
from dataclasses import asdict
from pathlib import Path

import click

from super_harness.cli.errors import format_error
from super_harness.cli.output import json_envelope
from super_harness.core.parse_ts import parse_ts
from super_harness.core.paths import (
    HarnessNotInitialized,
    events_path,
    find_harness_root,
)
from super_harness.engineering.value_report import (
    AuthorizationRecord,
    CostBreakdownRow,
    ValueReport,
    build_value_report,
)
from super_harness.exit_codes import EXIT_NO_CONFIG, EXIT_OK


def _fmt_tokens(n: int) -> str:
    return f"~{n:,}" if n else "0"


def _fmt_tokens_cell(n: int | None) -> str:
    return "—" if n is None else f"{n:,}"


def _breakdown_lines(r: ValueReport) -> list[str]:
    """Aggregate the per-run breakdown to one row per (role, source) for the
    human view — typically <=4 (2 roles x 2 sources), but legacy/malformed runs
    (``unknown`` bucket) or custom governance sources add rows; every group is
    shown, never capped (per-round detail stays in --json). Unknown tokens
    render '—', never 0."""
    if not r.cost_breakdown:
        return []
    groups: dict[tuple[str, str], list[CostBreakdownRow]] = {}
    for row in r.cost_breakdown:
        groups.setdefault((row.role, row.source), []).append(row)
    lines = [
        "",
        "Review cost breakdown (review-side only, self-reported, partial)",
        "  role/source                tokens  findings  rounds",
    ]
    for (role, source), rows in sorted(groups.items()):
        known = [row.tokens for row in rows if row.tokens is not None]
        tokens_cell = _fmt_tokens_cell(sum(known) if known else None)
        findings = sum(row.findings_raised for row in rows)
        rounds = len({row.round_id for row in rows if row.round_id})
        flags = []
        if any(row.findings_raised == 0 for row in rows):
            flags.append("has 0-finding round")
        if any(row.outcome == "rejected" for row in rows):
            flags.append("has rejected round")
        flag = f"  ! {', '.join(flags)}" if flags else ""
        label = f"{role}/{source}"
        lines.append(f"  {label:<22} {tokens_cell:>10}  {findings:>8}  {rounds:>6}{flag}")
    lines.append("  per-round detail: super-harness --json report -> .cost_breakdown")
    return lines


def _one_line(text: str) -> str:
    """Reduce a field to safe, single-line, single-spaced text.

    One rule for one row: an authorization row is a single line, so nothing
    interpolated into it may break the line, forge column alignment, or steer the
    terminal. Applied per field rather than to the finished row so the separators
    stay intact.

    Non-printables become spaces and are then collapsed with the rest of the
    whitespace. This is a whitelist on purpose (AUTH-008): the two rounds before it
    each excluded one more class of character — newlines, then all whitespace — and
    each missed the next one, because `str.split()` only knows `str.isspace()` and
    `\\x1b` is not whitespace. A reason carrying `\\x1b[1A\\x1b[2K` moved the cursor up
    and erased the row printed above it, so one authorization could delete another
    from the display — understating the count, which is the direction
    `derive_authorizations` exists to prevent. `str.isprintable()` is False for C0
    and C1 controls, bidi overrides, NBSP and the earlier rounds' newlines and tabs
    at once, and True for ordinary text in any script.

    Rendering only. `--json` still carries the recorded bytes.
    """
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())


def _fmt_when(ts: str) -> str:
    """`YYYY-MM-DD HH:MM UTC` when the timestamp parses, else the raw string.

    The zone marker is not decoration. This surface asks a human to falsify the
    record from memory, and time is the field memory keys on: a reviewer in UTC+8
    who authorized at 18:18 local reads an unlabelled `10:18` as somebody else's
    act — the exact misreading the count exists to prevent (AUTH-002).

    Marked rather than converted to local time: `report` also runs in CI and in
    other people's shells, where "local" is a different answer for the same row.

    Never drops the value: an unparseable timestamp still identifies which
    authorization a row is, and the row must appear either way.
    """
    parsed = parse_ts(ts)
    return parsed.strftime("%Y-%m-%d %H:%M UTC") if parsed is not None else ts


def _authorization_lines(r: ValueReport) -> list[str]:
    """The count first, then one row per authorization.

    Unlike the round-budget line, a zero is printed rather than omitted. This is the
    surface the falsify-from-memory check runs against, and a human who authorized
    twice needs to be able to tell "none recorded" from "the section isn't shown".
    """
    lines = [
        "",
        "Human authorizations",
        # What was counted, not what it bought. `derive_authorizations` counts
        # `review_round_authorized` events, and an authorization can be recorded and
        # never consumed — the human authorizes, then the round is retired or never
        # runs. "funded N rounds" would claim more than the derivation measured, which
        # this module's design law forbids, and the falsify-from-memory check keys on
        # authorizing anyway (AUTH-006).
        f"  - {r.authorizations_total} human authorization(s) recorded, each one "
        "permitting a single automated review round",
    ]
    if not r.authorizations:
        return lines
    lines.append(
        "    If you remember authorizing fewer than this, the difference was not you."
    )
    for a in r.authorizations:
        lines.append(_authorization_row(a))
    lines.append("    Reasons are recorded verbatim and verified by nothing.")
    return lines


def _authorization_row(a: AuthorizationRecord) -> str:
    """One authorization on one line: when, which change, which role, who, why.

    The actor is here and not only in `--json` (AUTH-003). With two people on a repo
    — the stated audience — unnamed rows leave neither of them able to falsify the
    ones that are not theirs, and the field was already being derived.

    The reason is the only account of why a round was funded, so it is rendered as
    typed — placeholders included. A relayed `<why>` that nobody replaced is not dirt
    to be cleaned up; it is the recorded state of that authorization, and hiding it
    would make the record read better than the act was.

    Its whitespace is collapsed, though, and never truncated: a reason carrying a
    newline plus this row's leading spaces otherwise prints as two rows that read as
    two authorizations (AUTH-004). Tabs collapse for the same reason — they forge
    column alignment as well as a newline forges a row. This is a rendering rule
    only; `--json` still carries the bytes that were recorded.

    A reason that is nothing but whitespace collapses to empty and is reported as
    absent, not as a blank column. `derive_authorizations` maps `""` to None but
    cannot map `"   "` — that is a string somebody typed, and only this layer knows
    it renders as nothing.

    The collapse applies to EVERY field, not just the reason (AUTH-005). They all
    land on the same single line, so any of them carrying a newline forges a row —
    `actor` most reachably, since `resolve_identity`'s `SUPER_HARNESS_ACTOR` branch
    only strips the ends, and `_fmt_when` passes an unparseable timestamp through
    raw. One authorization prints as one row regardless of which field is hostile.
    """
    reason = _one_line(a.reason) if a.reason is not None else ""
    return "    " + "  ".join((
        _one_line(_fmt_when(a.timestamp)),
        _one_line(a.change_id),
        _one_line(a.reviewer),
        _one_line(a.actor),
        reason or "(no reason recorded)",
    ))


def _reopen_lines(r: ValueReport) -> list[str]:
    """The count first, then one row per reopen. Zero is printed, like authorizations.

    Same discipline as `_authorization_row`, and for the same reasons: every field is
    collapsed to one line so no hostile value can forge a second row, and a reason that
    renders as nothing is reported as absent rather than as a blank column.
    """
    lines = [
        "",
        "Reopened implementations",
        # What was counted, not what happened next. The trailing clause states the
        # merge gate's RULE — a reopen lands in IMPLEMENTATION_IN_PROGRESS, and nothing
        # merges from there without `code_review_passed` and READY_TO_MERGE — which is
        # true of every reopen the moment it is emitted. It deliberately does not say
        # the change WAS reviewed again: a reopened change can be abandoned, and
        # `derive_reopens` counts `implementation_invalidated` events and nothing else.
        # Nor does it say the voided review had PASSED — the verb accepts
        # AWAITING_CODE_REVIEW, where the round is still out.
        f"  - {r.reopens_total} frozen implementation(s) returned to editing, each one "
        "requiring code review to run again before it can merge",
    ]
    if not r.reopens:
        return lines
    lines.append(
        "    If you remember reopening fewer than this, the difference was not you."
    )
    for record in r.reopens:
        reason = _one_line(record.reason) if record.reason is not None else ""
        lines.append("    " + "  ".join((
            _one_line(_fmt_when(record.timestamp)),
            _one_line(record.change_id),
            _one_line(record.actor),
            reason or "(no reason recorded)",
        )))
    lines.append("    Reasons are recorded verbatim and verified by nothing.")
    return lines


def _bottom_line(r: ValueReport) -> str:
    if (
        r.findings_resolved == 0
        and r.undisclosed_bypasses == 0
        and r.edits_blocked == 0
    ):
        return (
            "Bottom line: no measurable catches this window — nothing prevented "
            "that we can prove. On this evidence alone it is not earning its keep here."
        )
    parts = []
    if r.findings_resolved:
        parts.append(f"review earned its keep: {r.findings_resolved} real fix(es)")
    if r.edits_blocked:
        parts.append(f"the gate held {r.edits_blocked} out-of-lifecycle edit target(s)")
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
        f"  - {r.edits_blocked} distinct out-of-lifecycle edit target(s) the gate "
        "held (file x state; a conservative floor - retries collapse)",
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
    ]
    if r.review_reported_cost_usd is not None:
        # Only ever the producers' own figure. Omitted entirely when nobody reported
        # one, because a `$0.00` line would read as "this was free".
        lines.append(
            f"  - producer-reported cost: ${r.review_reported_cost_usd:,.2f} "
            f"(stated by the producer for {r.review_runs_with_reported_cost}/"
            f"{r.review_runs_total} runs; not a harness estimate)"
        )
    if r.review_budget_hits:
        # Only when it fired. A "0 times" line would be noise on every other change.
        lines.append(
            f"  - round budget: held {r.review_budget_hits} automatic round(s) for a "
            "human funding decision (distinct rounds, not retries)"
        )
    lines += [
        f"  - review rework: {r.findings_wontfix} false alarm(s) (wontfix), "
        f"{r.rejected_rounds} rejected round(s)",
        "",
        f"  Note: {r.armed_decisions} locked rule(s), verification and doc-sync also "
        "stand guard in the",
        "  prevention layer - their successful catches still leave no trace (a further "
        "Stage 2 cut).",
    ]
    lines += _breakdown_lines(r)
    lines += _authorization_lines(r)
    lines += _reopen_lines(r)
    lines += [
        "",
        _bottom_line(r),
    ]
    return "\n".join(lines)


def _render_brief(r: ValueReport) -> str:
    window = f"{r.since or 'all'}-{r.until or 'now'}"
    bits = [f"caught {r.findings_resolved}", f"{_fmt_tokens(r.review_tokens)} review tokens"]
    if r.edits_blocked:
        bits.append(f"{r.edits_blocked} distinct target(s) held")
    if r.undisclosed_bypasses:
        bits.append(f"{r.undisclosed_bypasses} undisclosed bypass(es)")
    if r.authorizations_total:
        # The count travels with the one-line form people paste; the reasons do not
        # fit on it and stay in the full view.
        bits.append(f"{r.authorizations_total} human authorization(s)")
    return f"{window}: " + ", ".join(bits) + "."


@click.command("report")
@click.option(
    "--since",
    default=None,
    help="Only count events and gate-block records on/after this ISO date "
    "(e.g. 2026-07-01). Unparseable = no lower bound (never errors).",
)
@click.option(
    "--until",
    default=None,
    help="Only count events and gate-block records on/before this ISO date (a bare "
    "date counts through the end of that day). Unparseable = no upper bound.",
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
