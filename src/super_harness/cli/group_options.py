"""Custom Click Command/Group classes that emit a corrective hint when a user
puts a top-level flag (e.g. `--json`, `--quiet`, `--workspace`) at a
subcommand position.

Without this, Click's default `NoSuchOption` error tells the user to try
``<subcommand> --help`` for help — but the rejected option is group-level
and won't appear there, so the suggestion is misleading.

`GroupAwareCommand` catches `NoSuchOption` for a known set of top-level
flag tokens (long forms AND short aliases) and reraises with a
`UsageError` redirect pointing at the right slot:

    Error: '--json' is a top-level flag of `super-harness`, not a
           subcommand flag. Try: `super-harness --json verify ...`

`GroupAwareGroup` is the matching `click.Group` whose `command_class` and
`group_class` propagate `GroupAwareCommand` to every (nested) subcommand
so per-command wiring is not needed.

Trade-off: the redirect hint is direction-pointing, not copy-paste-ready.
If the user mixes valid group-level args with a mispositioned flag (e.g.
``super-harness --workspace /tmp verify --json``), the example drops the
``--workspace /tmp`` from its reconstruction. That is acceptable; the
hint exists to teach correct slot, not to reproduce the full invocation.

Refs: private/OPEN-ITEMS.md #6 S8-misleading.
"""
from __future__ import annotations

import click
from click.exceptions import NoSuchOption, UsageError

# Top-level flag tokens of the `cli` group. Includes both long forms and
# the short aliases declared in `cli/__init__.py::main`. Keep in sync with
# the click.option decorators on the root group.
_TOP_LEVEL_FLAGS: frozenset[str] = frozenset(
    {
        "--workspace",
        "--json",
        "--quiet",
        "-q",
        "--verbose",
        "-v",
        "--version",
        "-V",
    }
)


def _redirect_hint(option_name: str, command_path: str) -> str:
    """Build the redirect message.

    Args:
        option_name: the rejected option token, e.g. ``"--json"``.
        command_path: full subcommand chain as ``ctx.command_path`` reports
            it, e.g. ``"super-harness change start"``.

    Returns:
        Single-line error text (without trailing newline). Safe against
        brace injection because we use f-strings, not ``str.format``.
    """
    parts = command_path.split()
    program = parts[0] if parts else "super-harness"
    subchain = " ".join(parts[1:]) if len(parts) > 1 else ""
    if subchain:
        example = f"`{program} {option_name} {subchain} ...`"
    else:
        example = f"`{program} {option_name} <subcommand> ...`"
    return (
        f"'{option_name}' is a top-level flag of `{program}`, not a "
        f"subcommand flag. Try: {example}"
    )


class GroupAwareCommand(click.Command):
    """A `click.Command` that detects mispositioned top-level flags.

    Catches `NoSuchOption` for tokens in `_TOP_LEVEL_FLAGS` and reraises a
    `UsageError` with a corrective hint. All other `NoSuchOption` errors
    propagate unchanged so Click's built-in "Did you mean" suggestions
    for typos still work.
    """

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        try:
            return super().parse_args(ctx, args)
        except NoSuchOption as err:
            if err.option_name in _TOP_LEVEL_FLAGS:
                raise UsageError(
                    _redirect_hint(err.option_name, ctx.command_path),
                    ctx=ctx,
                ) from err
            raise


class GroupAwareGroup(click.Group):
    """A `click.Group` whose subcommands and subgroups inherit
    `GroupAwareCommand` automatically.

    Setting `command_class` makes ``@group.command(...)`` use
    `GroupAwareCommand`. Setting `group_class` (assigned after the class
    body via the self-reference dance below) makes ``@group.group(...)``
    also propagate, so commands nested under sub-subgroups (e.g.
    ``change start``) inherit the behavior.
    """

    command_class: type[click.Command] = GroupAwareCommand
    # group_class is assigned to GroupAwareGroup itself just below the
    # class body — we can't reference the class inside its own body.


# Self-reference: every subgroup created via ``@group.group(...)`` should
# also be a `GroupAwareGroup` so the redirect propagates to grandchildren.
GroupAwareGroup.group_class = GroupAwareGroup


def rewrap_subtree(group: click.Group) -> None:
    """Rewrite every descendant command/group of ``group`` so they use
    `GroupAwareCommand` / `GroupAwareGroup`.

    Why this helper exists: subgroups in `super_harness.cli.*` are defined
    with plain ``@click.group(...)`` decorators in their own modules. When
    they are attached to ``main`` via ``main.add_command(...)``, the root
    group's ``command_class`` / ``group_class`` propagation does NOT apply
    retroactively — it only applies to subcommands created VIA the parent's
    decorators (``@parent.command(...)`` / ``@parent.group(...)``).

    Solution: walk the registered subcommand tree once at startup and swap
    the ``__class__`` of every node. ``__class__`` reassignment is safe
    here because `GroupAwareCommand` / `GroupAwareGroup` add no new
    instance attributes — they only override `parse_args` (Command) /
    propagate ``command_class`` (Group).

    The root ``group`` itself is intentionally NOT rewrapped: it owns the
    top-level flags, so by definition it never raises `NoSuchOption` for
    them and would not benefit from the override. Rewrapping it would also
    risk masking legitimate root-level option errors.
    """
    for child in group.commands.values():
        if isinstance(child, click.Group):
            # Preserve any custom Group subclass while still ensuring the
            # override is in place — but in practice all subgroups here are
            # plain `click.Group`, so a straight swap is correct.
            child.__class__ = GroupAwareGroup
            rewrap_subtree(child)
        else:
            child.__class__ = GroupAwareCommand
