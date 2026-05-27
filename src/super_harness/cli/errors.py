"""Error message formatter for super-harness CLI commands.

All CLI commands emit errors via `format_error(...)` to ensure consistent shape:

    super-harness <subcommand>: <one-line error>
      Hint: <actionable suggestion>
      Docs: <link to relevant doc anchor>

Per cli-command-surface §3.3.
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
