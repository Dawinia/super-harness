"""AGENTS.md inject/remove primitives (engineering-integration spec §2.2 / §3.2).

Pure functions that maintain the marked "super-harness section" inside a repo's
root ``AGENTS.md`` so AI coding agents learn the project conventions. These are
the building blocks `super-harness init` and `adapter install/uninstall` compose;
nothing in this module reaches the network or shells out.

Marker grammar (§2.2):

- Outer section: begin marker starts ``<!-- super-harness section begin `` (the
  rest, e.g. ``· v0.1.0 · DO NOT EDIT MANUALLY``, is version-stamped by `init`),
  end marker is exactly ``<!-- super-harness section end -->``. `inject_section`
  matches the begin generically (``begin .*?section end`` with DOTALL, §3.2).
- Framework block: ``<!-- super-harness framework: <name> -->`` …
  ``<!-- /super-harness framework: <name> -->``.
- Agent block: ``<!-- super-harness agent: <name> -->`` …
  ``<!-- /super-harness agent: <name> -->``.
- Placeholders consumed at init: ``[FRAMEWORK_SECTION_AUTO_INSERTED]`` and
  ``[AGENT_SECTION_AUTO_INSERTED]``.
- No-agent placeholder (written by init when no agent adapter is installed):
  ``<!-- super-harness no-agent-adapter-installed -->`` — it doubles as the
  anchor a later first agent install replaces.

**`content` contract** (subsection injectors): ``content`` is the FULL
marker-wrapped block string — ``<!-- super-harness <kind>: <name> -->\\n …
\\n<!-- /super-harness <kind>: <name> -->`` — exactly as an adapter's
``agents_md_subsection()`` returns it. The ``framework`` / ``agent`` name
argument is used ONLY to locate the existing block (for by-name replace) and is
assumed consistent with the markers inside ``content``. (This is the operational
reading of §3.2, whose pseudocode builds the block from inner text; the real
callers pass a pre-wrapped block, so we accept it whole rather than re-wrap.)

**Line endings** (§3.2 / AC-2 — "content outside begin/end is never changed"):
the §3.2 regexes assume ``\\n``. A Windows-authored AGENTS.md may be CRLF. We
detect the file's dominant newline on read, normalize a working copy to ``\\n``
for all matching + block construction, then on write convert back to ``\\r\\n``
if that was dominant — so the injected block matches the surrounding style. For
a file with UNIFORM line endings, untouched regions round-trip byte-identical.
A file with MIXED line endings is NOT preserved verbatim: it is normalized to
its dominant style (CRLF if ANY CRLF is present, else LF) — every line ending in
the written file then matches that style. New files use ``\\n``.

**Atomic writes**: every write goes through `_atomic_write`, which writes a
sibling temp file then ``os.replace`` — no torn writes if interrupted.

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

__all__ = [
    "AgentsMdInjectionError",
    "inject_agent_subsection",
    "inject_framework_subsection",
    "inject_section",
    "remove_subsection",
    "section_present",
]

# Outer section. `inject_section` matches the begin generically (the begin
# marker carries a version stamp written by init) per §3.2 line 613.
_SECTION_BEGIN_PREFIX = "<!-- super-harness section begin "
_SECTION_END = "<!-- super-harness section end -->"
_SECTION_PATTERN = re.compile(
    r"<!-- super-harness section begin .*?super-harness section end -->",
    re.DOTALL,
)

# Placeholders consumed at init.
_FRAMEWORK_PLACEHOLDER = "[FRAMEWORK_SECTION_AUTO_INSERTED]"
_AGENT_PLACEHOLDER = "[AGENT_SECTION_AUTO_INSERTED]"
_NO_AGENT_PLACEHOLDER = "<!-- super-harness no-agent-adapter-installed -->"

_CRLF = "\r\n"
_LF = "\n"


class AgentsMdInjectionError(Exception):
    """Raised when AGENTS.md is in an ambiguous state we refuse to guess about.

    Currently: more than one outer super-harness section block is present
    (user mis-edit or duplicated markers) — manual cleanup is required (§3.2).
    """


def inject_section(path: Path, content: str) -> None:
    """Inject / replace the outer super-harness section in ``path`` (§3.2).

    - File absent → write ``content + "\\n"``.
    - Exactly one existing section block → replace it (``count=1``).
    - No existing block → append after the existing content (blank-line separated).
    - More than one existing block → raise `AgentsMdInjectionError`.

    The file's dominant newline style is preserved (see module docstring).
    """
    if not path.exists():
        # New file: canonical LF, content + trailing newline.
        _atomic_write(path, content + _LF)
        return

    existing, newline = _read_normalized(path)
    blocks = _SECTION_PATTERN.findall(existing)

    if len(blocks) > 1:
        raise AgentsMdInjectionError(
            f"{path} has {len(blocks)} super-harness section blocks; manual "
            f"cleanup required (only 1 expected)."
        )
    if len(blocks) == 1:
        new = _SECTION_PATTERN.sub(lambda _m: content, existing, count=1)
    else:
        new = existing.rstrip() + _LF + _LF + content + _LF

    _write_normalized(path, new, newline)


def section_present(path: Path) -> bool:
    """Return True iff ``path`` contains at least one outer super-harness section.

    The overwrite-confirm predicate `super-harness sync` uses to decide whether a
    re-render would clobber an existing section (and thus warrants a confirm). A
    read-only counterpart to `inject_section`: it normalizes line endings the same
    way and matches the same generic begin-marker pattern. AGENTS.md absent → no
    section exists → False (a fresh render carries no overwrite risk).
    """
    if not path.exists():
        return False
    existing, _newline = _read_normalized(path)
    return _SECTION_PATTERN.search(existing) is not None


def inject_framework_subsection(path: Path, framework: str, content: str) -> None:
    """Inject / replace the ``framework: <name>`` subsection (§3.2 lines 633-666).

    ``content`` is the FULL marker-wrapped block (see module docstring). Branch
    order:
      1. ``[FRAMEWORK_SECTION_AUTO_INSERTED]`` present → replace it (first inject).
      2. an existing ``framework: <framework>`` block → replace it by name.
      3. else → append after the LAST framework block (lookahead, ``count=1``).
    """
    _inject_subsection(path, "framework", framework, content, _FRAMEWORK_PLACEHOLDER)


def inject_agent_subsection(path: Path, agent: str, content: str) -> None:
    """Inject / replace the ``agent: <name>`` subsection (4-branch refinement).

    ``content`` is the FULL marker-wrapped block (see module docstring). Branch
    order (a deliberate refinement over §3.2's symmetric description, honoring the
    §3.2 line-672 intent that a first install replaces the no-agent placeholder):
      1. ``[AGENT_SECTION_AUTO_INSERTED]`` present → replace it.
      2. ``<!-- super-harness no-agent-adapter-installed -->`` present → replace it
         (the FIRST real agent install's anchor after init consumed the
         placeholder into this marker — without this branch nothing matches and
         injection would silently no-op).
      3. an existing ``agent: <agent>`` block → replace it by name.
      4. else → append after the LAST agent block (lookahead, ``count=1``).
    """
    _inject_subsection(
        path,
        "agent",
        agent,
        content,
        _AGENT_PLACEHOLDER,
        no_agent_placeholder=_NO_AGENT_PLACEHOLDER,
    )


def remove_subsection(path: Path, kind: str, name: str) -> None:
    """Remove the ``<!-- super-harness {kind}: {name} -->`` … block from ``path``.

    ``kind`` is ``"framework"`` or ``"agent"``. The rest of the file is left
    byte-identical except for the removed block and at most one surrounding blank
    line (so no double blank is left behind). AGENTS.md absent, or the block
    absent → no-op (no error).

    Special case: when ``kind == "agent"`` and removing the block leaves ZERO
    agent blocks, the ``<!-- super-harness no-agent-adapter-installed -->``
    placeholder is re-inserted where the block was, so a later agent install has
    an anchor (`inject_agent_subsection` branch 2). For ``kind == "framework"``
    no placeholder is restored (a plain framework block is always present, so
    framework never reaches zero).
    """
    if kind not in ("framework", "agent"):
        raise ValueError(f"kind must be 'framework' or 'agent', got {kind!r}")
    if not path.exists():
        return

    existing, newline = _read_normalized(path)
    block_pattern = re.compile(
        rf"<!-- super-harness {kind}: {re.escape(name)} -->"
        rf".*?<!-- /super-harness {kind}: {re.escape(name)} -->",
        re.DOTALL,
    )
    if not block_pattern.search(existing):
        return  # block absent → no-op

    if kind == "agent" and _is_last_agent_block(existing, name):
        # Restore the no-agent anchor in place of the removed block.
        new = block_pattern.sub(lambda _m: _NO_AGENT_PLACEHOLDER, existing, count=1)
    else:
        # Drop the block plus exactly one surrounding blank-line separator: a
        # trailing "\n\n" collapses to "\n"; failing that a leading one does.
        new = re.sub(
            block_pattern.pattern + r"\n\n",
            "",
            existing,
            count=1,
            flags=re.DOTALL,
        )
        if new == existing:
            new = re.sub(
                r"\n\n" + block_pattern.pattern,
                "",
                existing,
                count=1,
                flags=re.DOTALL,
            )
        if new == existing:
            new = block_pattern.sub(lambda _m: "", existing, count=1)

    _write_normalized(path, new, newline)


# --------------------------------------------------------------------------- #
# internals
# --------------------------------------------------------------------------- #


def _inject_subsection(
    path: Path,
    kind: str,
    name: str,
    content: str,
    placeholder: str,
    *,
    no_agent_placeholder: str | None = None,
) -> None:
    """Shared inject logic for framework / agent subsections.

    ``content`` is the full marker-wrapped block; ``name`` locates an existing
    block by name. ``no_agent_placeholder`` (agent only) adds the branch-2 anchor.
    Reads/writes go through the newline-preserving helpers.

    Invariant: adapter block NAMES are assumed to be space-free slugs (e.g.
    ``claude-code``, ``openspec``). The append-fallback / last-block-detection
    regexes (``[^ ]+``) rely on this; only the by-name replace path is space-safe
    via ``re.escape``.
    """
    existing, newline = _read_normalized(path)
    block = content.rstrip(_LF)

    name_pattern = re.compile(
        rf"<!-- super-harness {kind}: {re.escape(name)} -->"
        rf".*?<!-- /super-harness {kind}: {re.escape(name)} -->",
        re.DOTALL,
    )

    if placeholder in existing:
        new = existing.replace(placeholder, block, 1)
    elif no_agent_placeholder is not None and no_agent_placeholder in existing:
        new = existing.replace(no_agent_placeholder, block, 1)
    elif name_pattern.search(existing):
        new = name_pattern.sub(lambda _m: block, existing, count=1)
    else:
        # Append after the LAST same-kind block. The negative lookahead ensures
        # we match the final close marker; count=1 is belt-and-braces.
        # The appended block inherits the file's existing trailing-newline state
        # (we don't add one). In the real flow this is benign: subsection blocks
        # always live inside the outer section that `inject_section` writes with a
        # trailing newline, so a trailing newline is already present.
        append_pattern = (
            rf"(<!-- /super-harness {kind}: [^ ]+ -->)"
            rf"(?!.*<!-- /super-harness {kind}:)"
        )
        new = re.sub(
            append_pattern,
            lambda m: m.group(1) + _LF + _LF + block,
            existing,
            count=1,
            flags=re.DOTALL,
        )

    _write_normalized(path, new, newline)


def _is_last_agent_block(text: str, name: str) -> bool:
    """Whether ``name``'s agent block is the only agent block in ``text``.

    The ``[^ ]+`` capture relies on the space-free-slug name invariant (see
    `_inject_subsection`).
    """
    names = re.findall(r"<!-- super-harness agent: ([^ ]+) -->", text)
    return names == [name]


def _detect_newline(text: str) -> str:
    """Return the dominant newline of ``text``: ``\\r\\n`` if any CRLF, else ``\\n``."""
    return _CRLF if _CRLF in text else _LF


def _read_normalized(path: Path) -> tuple[str, str]:
    """Read ``path`` and return (LF-normalized text, dominant newline style).

    Decodes bytes ourselves (no universal-newline translation) so we observe the
    file's real line endings and never accidentally rewrite untouched regions.
    """
    raw = path.read_bytes().decode("utf-8")
    newline = _detect_newline(raw)
    if newline == _CRLF:
        return raw.replace(_CRLF, _LF), newline
    return raw, newline


def _write_normalized(path: Path, text: str, newline: str) -> None:
    """Write LF-normalized ``text`` back to ``path`` in ``newline`` style, atomically."""
    if newline == _CRLF:
        text = text.replace(_LF, _CRLF)
    _atomic_write(path, text)


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically (temp sibling + ``os.replace``).

    Writes raw UTF-8 bytes (no platform newline translation — ``text`` already
    carries the caller's chosen line endings); the temp file lives in the SAME
    directory so ``os.replace`` is a same-filesystem rename (atomic on POSIX and
    Windows). The temp file is cleaned up on error.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(text.encode("utf-8"))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
