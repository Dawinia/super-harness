"""Shared AGENTS.md "super-harness section" renderer (init + sync SSOT).

`super-harness init` and (later) `super-harness sync` must re-render the §2.2
outer super-harness section the EXACT same way. Keeping that render logic in one
place avoids the §2.2 template drifting between two copies and a smelly private
cross-CLI import. This module is that single source of truth:

- ``_AGENTS_MD_SECTION_TEMPLATE`` — the §2.2 outer-section markdown template
  (version-stamped in the begin marker).
- ``render_super_harness_section`` — the public 3-step renderer both CLIs call.
- ``_reinject_installed_adapters`` — the ``--force`` / re-render loop closure
  that restores every adapter still registered in ``.harness/adapters.yaml``.

Error contract: ``render_super_harness_section`` lets ``OSError`` and
``AgentsMdInjectionError`` propagate to the caller. Each CLI keeps its OWN
``try/except`` + ``format_error`` envelope (init and sync have distinct error
messages / exit codes), so this module deliberately does not wrap them.

The pure inject/remove primitives live in
``super_harness.engineering.agents_md`` (zero adapter knowledge); this module
composes them with the adapter registry. Per engineering-integration §2.2 / §3.2.

API stability: **experimental** (v0.1).
"""

from __future__ import annotations

from pathlib import Path

import click
import yaml

from super_harness.adapters.framework.plain import PlainAdapter
from super_harness.adapters.registry import load_adapters
from super_harness.core.paths import adapters_yaml_path
from super_harness.engineering.agents_md import (
    inject_agent_subsection,
    inject_framework_subsection,
    inject_section,
)

__all__ = [
    "render_super_harness_section",
]

# The §2.2 outer-section template. The framework placeholder is kept literal
# (inject_framework_subsection replaces it with the plain block on render); the
# agent slot carries the no-agent anchor directly rather than the
# [AGENT_SECTION_AUTO_INSERTED] literal — a base render knows there is no agent
# adapter yet, and the anchor is what a later `adapter install <agent>` replaces.
# This leaves NO [*_SECTION_AUTO_INSERTED] literal after a render (§3.2 line 676).
_AGENTS_MD_SECTION_TEMPLATE = """\
<!-- super-harness section begin · v{version} · DO NOT EDIT MANUALLY -->
## Super-harness conventions

This project uses super-harness to ensure AI coding reliability.

### Branch naming

Branch naming is YOURS — keep whatever convention your team already uses.
super-harness identifies a change by its **slug**, which it carries explicitly
in the PR metadata block (and, for framework adapters, the artifact frontmatter)
— NOT in the branch name. Naming a branch after the slug
(e.g. `2026-05-26-add-l1-anchors`) is a convenient default that lets CI resolve
the change with zero config, but it is optional, not required.

### PR creation

Use your framework's native PR command:

[FRAMEWORK_SECTION_AUTO_INSERTED]

super-harness will automatically append a metadata block to your PR description
between `<!-- super-harness:metadata -->` markers.
**Do not modify content between those markers manually.**

### Agent-specific guidance

<!-- super-harness no-agent-adapter-installed -->

### Before opening PR

Ensure `super-harness verify` passes (tests / lint / build / anchor sentinels).
If using a `done` skill, run `super-harness done <slug>` instead—it triggers
verify and emits the lifecycle event automatically.

### File scope

When implementing a change, edit only files in the declared `scope.files`
(see the plan artifact). Edits outside scope trigger drift warnings.

### Decision conformance

Ratified decisions under `docs/decisions/` are binding: super-harness
hash-locks each decision's text and, where configured, attaches an executable
check. Treat `super-harness decision check` as a LOCAL SENSOR you consult while
you work — CI runs it too as the un-bypassable floor, so keep it green locally.

- **At natural checkpoints** (a chunk done, before you commit) run
  `super-harness decision check --changed`. A non-zero exit means you violated a
  ratified decision or edited a ratified decision's body text — fix it before
  continuing; don't push the drift downstream to CI.
- **Don't hand-edit the body of a ratified decision.** Its text is hash-locked;
  re-ratifying (`super-harness decision ratify <id>`) is the only unlock, and is
  a deliberate, recorded act.
- **Attaching an executable check to a decision?** Before you propose it, run
  `super-harness decision ratify <id> --dry-run` to confirm the check actually
  bites (runs the bite-test without ratifying).
- `super-harness decision check` (full) and `super-harness doc check` are also
  CI gates — keep both green locally so a push never bounces.

**Arming a decision with a check (the craft).** A check is a shell snippet that
exits nonzero when a decision is violated; `ratify` bite-tests it so it can't be
hollow. Writing one that catches violations without false positives is judgment —
yours, not the tool's — and the recipe is:

- Pick the **brittle one-token signature** of a violation, not a broad word
  (`^import requests`, not `requests`, which also hits prose / yaml).
- Prefer import/access patterns over bare substrings to dodge prose false positives.
- The check runs through the host's `/bin/sh` and `grep`, so prefer portable
  patterns (avoid GNU-only `grep` extensions); it **must exit nonzero on
  violation** (`! grep ...` inverts grep's exit).
- A denylist is coarse by construction (`^import` misses `from X import …` forms);
  widen deliberately and record the ceiling in the decision body.
- **Scope the grep to source paths (e.g. `src/`), never `.`** — at ratify the
  check runs over the whole tree, so a bare `.` scans the decision file itself
  (which holds the counterexample) and reports "check fails on current code".
- Add a check + a minimal counterexample, then
  `super-harness decision ratify <id> --dry-run` until it reports `bites`:

  ```check
  ! grep -rn '<brittle pattern>' <scoped paths>
  ```

  ```counterexample path=<relative/path>
  <one minimal violating line the check above must catch>
  ```

- **If there is no brittle signature, leave it context-only (tier-3)** — do not
  invent a hollow check just to have one.
- **The check MUST be read-only and reentrant.** With `authoring_time: true` the
  check runs concurrently with the other armed checks on every turn end, so it must
  not write source, caches, `.pyc`, lock files, or any temp under the working tree
  (two checks writing the same path would race). A conformance check should be a pure
  predicate — e.g. use `lint-imports --no-cache` so no cache file is written.
- **Keep the armed authoring set small.** Each armed check spawns a subprocess
  concurrently every turn end; a large armed set misuses the interactive budget (CI
  is the exhaustive path).
- **Not sure which decisions to make?** To discover candidate architecture norms
  in an existing codebase, point your agent at the discovering-architecture-norms
  skill: https://github.com/Dawinia/super-harness/blob/main/skills/discovering-architecture-norms/SKILL.md
  (private repo during v0.1 — the link needs repo access until the public release).

<!-- super-harness section end -->"""


def render_super_harness_section(root: Path, agents_path: Path, version: str) -> None:
    """Render the §2.2 super-harness section into ``agents_path`` (init + sync SSOT).

    Performs the 3-step sequence both ``init`` and ``sync`` share:
      1. inject_section with the outer template stamped at ``version``;
      2. inject the plain framework block (PlainAdapter is the single source of
         that block — no hardcoded text);
      3. re-inject every adapter still registered in ``.harness/adapters.yaml``
         via ``_reinject_installed_adapters`` (full re-render loop closure).

    The injectors are idempotent (replace-in-place by name), so re-rendering an
    existing section never duplicates it. On a fresh repo (no adapters.yaml) step
    3 is a no-op.

    Error contract: ``OSError`` (unwritable AGENTS.md / full disk) and
    ``AgentsMdInjectionError`` (duplicate super-harness outer block) PROPAGATE to
    the caller — each CLI owns its own ``format_error`` envelope + exit code, so
    this renderer never swallows them. The internal adapters.yaml load IS
    non-fatal (advisory + skip) — see ``_reinject_installed_adapters``.
    """
    inject_section(agents_path, _AGENTS_MD_SECTION_TEMPLATE.format(version=version))
    inject_framework_subsection(agents_path, "plain", PlainAdapter().agents_md_subsection())
    _reinject_installed_adapters(root, agents_path)


def _reinject_installed_adapters(root: Path, agents_path: Path) -> None:
    """Re-inject every installed adapter's AGENTS.md subsection (loop closure).

    Called right after the base section + plain framework block are written, so
    a re-render (``--force`` or otherwise) restores the guidance for every
    adapter still registered in ``.harness/adapters.yaml`` rather than leaving
    only the no-agent anchor. On a fresh render (no adapters.yaml) `load_adapters`
    returns ``([], [])`` → this is a no-op; so it is NOT gated on ``--force``.

    Idempotent: the inject_* functions replace an already-present block in place
    (by name), so re-running never duplicates a subsection.

    Error split:
      - ONLY the `load_adapters` call is wrapped defensively, and only for its
        CONFIG-driven failures. A corrupt / malformed adapters.yaml is NON-FATAL
        here: the base section + plain block + anchor are already a valid
        baseline, so we emit an advisory and return rather than crash the render.
        The catch tuple covers a syntactically-broken file (`yaml.YAMLError`,
        raised by the unguarded `yaml.safe_load` in `load_adapters`), a
        wrong-shape / non-mapping / non-builtin config (`ValueError`), and an
        unreadable file (`OSError`) — `yaml.YAMLError` derives from `Exception`,
        NOT `ValueError`, so it must be listed explicitly. A bad *builtin's* own
        constructor (a code bug, not a config problem) is deliberately NOT caught
        — it should fail loud rather than be mislabeled as "unreadable". (The old
        plugin-exec `ImportError`/`AttributeError`/`TypeError` families are gone
        with v0.1 builtin-only loading.)
      - The inject_* calls are deliberately OUTSIDE that catch: an OSError /
        AgentsMdInjectionError they raise propagates to the caller's AGENTS.md
        envelope (fail-loud), matching the base-section writes.
    """
    try:
        frameworks, agents = load_adapters(adapters_yaml_path(root))
    except (yaml.YAMLError, ValueError, OSError) as e:
        click.echo(
            "Note: couldn't re-inject installed adapters into AGENTS.md "
            f"(adapters.yaml unreadable: {e}); re-run "
            "`super-harness adapter install <name>` to restore their guidance.",
            err=True,
        )
        return

    for fw in frameworks:
        if fw.name == "plain":
            # The plain block was already injected by the caller (PlainAdapter
            # is the single source of that block); re-injecting would be a
            # redundant in-place replace.
            continue
        inject_framework_subsection(agents_path, fw.name, fw.agents_md_subsection())
    for ag in agents:
        # The first agent consumes the no-agent anchor (inject branch 2);
        # subsequent agents append after the last agent block.
        inject_agent_subsection(agents_path, ag.name, ag.agents_md_subsection())
