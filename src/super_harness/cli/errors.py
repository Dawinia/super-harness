"""Error message formatter for super-harness CLI commands.

All CLI commands emit errors via `format_error(...)` to ensure consistent shape:

    super-harness <subcommand>: <one-line error>
      Hint: <actionable suggestion>
      Docs: <link to relevant doc anchor>

Per cli-command-surface §3.3.

Hint text style guide
---------------------
- Start with an imperative verb (Run, Use, Pass, See, Check, Pick).
- Sentence-cased: first letter uppercase, trailing period.
- One sentence; if longer guidance is needed, prefer a `Docs:` link via
  ``docs_anchor=`` instead of running multi-line in the hint.
- Reference subcommands in backticks: ``super-harness <subcommand>``.

Examples (all current call sites follow this shape):

- ``"Run `super-harness init` first."``
- ``"Use `status <slug>` to query one change OR `status --all` to list all."``
- ``"Pass `--force` to overwrite the existing directory."``
- ``"Pick one filter at a time."``
- ``"See cli-command-surface §3.2 for slug rules."``
"""

DOCS_BASE = "https://super-harness.dev/docs"


def format_error(
    *,
    subcommand: str,
    message: str,
    hint: str | None = None,
    docs_anchor: str | None = None,
) -> str:
    """Render a structured error message for stderr.

    Args:
        subcommand: e.g., "verify" / "init" / "change start"
        message: one-line error explanation
        hint: optional actionable next step
        docs_anchor: optional doc anchor (appended to DOCS_BASE)

    Returns:
        Multi-line string ready to write to stderr.
    """
    lines = [f"super-harness {subcommand}: {message}"]
    if hint:
        lines.append(f"  Hint: {hint}")
    if docs_anchor:
        lines.append(f"  Docs: {DOCS_BASE}/{docs_anchor}")
    return "\n".join(lines)
