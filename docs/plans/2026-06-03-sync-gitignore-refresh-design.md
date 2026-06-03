# `sync` refreshes the managed `.gitignore` block — design

Date: 2026-06-03
Status: design (pre-implementation)
Scope: give an already-initialized repo a **non-destructive** way to refresh the
marker-bounded `.gitignore` block when `_CANONICAL_PATHS` changes across a
super-harness upgrade, plus a dogfood drift-guard that the committed block stays
in sync with the injector. (HG-D step-2 roadmap open-item.)

> This document is brainstorming output, not a lifecycle plan — it intentionally
> carries **no** `change:` / `stage:` frontmatter so the framework observer does
> not mint a change from it. The implementation plan is produced separately via
> writing-plans, and the lifecycle is driven by hand through the `super-harness`
> CLI verbs (this is the first change run under the live PreToolUse hard gate).

---

## 1. The gap

`super-harness init` writes **two** managed artifacts: the AGENTS.md
super-harness section *and* the repo-root `.gitignore` marker block (the list of
auto-generated / per-machine paths from
`engineering.gitignore_injector._CANONICAL_PATHS`).

`super-harness sync` re-renders **only** the AGENTS.md section. So when a
super-harness upgrade adds entries to `_CANONICAL_PATHS` (HG-D step-2 added
`.harness/gate-disabled`, `.claude/settings.local.json`,
`.claude/*.super-harness-backup.*`), an already-initialized repo has **no
non-destructive command** to pick them up:

- `init --force` would refresh the block, but it also re-scaffolds skeleton
  files — it clobbers a customized `verification.yaml` (HG-D's was hand-wired
  with the `PATH="$(pwd)/.venv/bin:…"` prefix and the full-suite must-pass
  config).
- Hand-editing inside the markers is fragile: any later `init` regenerates the
  block and silently discards the edit.

This is exactly the drift that masked final-review **I-1** in PR #34: the
committed block was written by an early adoption run, never re-rendered, and the
two new lines survived only because *this* machine's global `excludesfile`
ignored them. A teammate cloning the repo could have committed machine paths.

## 2. What we build

**(A) `sync` refreshes the `.gitignore` block too.** Reuse the existing
`engineering.gitignore_injector.inject_gitignore_block`, which is already
marker-bounded, non-destructive (only the block body is replaced), a
byte-identical no-op when already current, and fail-loud on ≥2 / unbalanced
markers. No new injector logic — `sync` just calls it.

**Command surface (chosen: default refreshes everything + a `--gitignore`
scope).** This mirrors how `init` writes both artifacts at once and makes the
flag set internally consistent — no flag = all managed artifacts, each flag = one
scope:

| invocation        | effect                                                |
|-------------------|-------------------------------------------------------|
| `sync`            | AGENTS.md section **+** `.gitignore` block (all)      |
| `sync --agents-md`| AGENTS.md section **only** (semantics tightened)      |
| `sync --gitignore`| `.gitignore` block **only** (new)                     |
| `sync --adapter X`| adapter `X` subsection only (unchanged)               |

Semantics change for `--agents-md`: it was documented in v0.1 as "identical to
no-arg (placeholder)". Tightening it to a real AGENTS.md-only scope is benign —
it was always a placeholder, and the result is three uniform scope flags. The
existing precedence rule extends naturally: `--adapter` is the narrowest scope
and wins over `--agents-md` / `--gitignore` if combined (adapter-only).
`--agents-md` and `--gitignore` together = both (i.e. same as no-arg).

**(B) Dogfood drift-guard test.** A unit test that reads *this repo's* committed
root `.gitignore`, extracts the marker-bounded block, and asserts it equals
`inject_gitignore_block`'s rendered output (`_render_block()`). Same pattern as
`tests/unit/scripts/test_gen_cli_reference.py::test_real_cli_reference_is_in_sync`
(locate the repo root via `Path(__file__).resolve().parents[N]`, compare to the
generator's output, fail with a "run `super-harness sync --gitignore` and commit"
hint). This catches `_CANONICAL_PATHS` drifting from the committed block on every
test run — the guard that I-1 lacked.

## 3. Behavior details

- **No new confirm prompt for the gitignore leg.** The AGENTS.md section can
  contain user-authored prose between the markers, so its overwrite is gated by
  `_confirm_overwrite_if_present`. The `.gitignore` block is *purely* our
  canonical path list — there is no user content between its markers to lose, and
  the injector is already a no-op when current. So the gitignore re-render runs
  without its own prompt. In the default (all) mode the existing AGENTS.md
  confirm still fires once for the AGENTS.md leg; the gitignore leg piggybacks
  silently after it.
- **Error envelope.** Mirror `init`: wrap the `inject_gitignore_block` call in
  `except (OSError, GitignoreInjectionError)` → `format_error(subcommand="sync",
  …)` → `sys.exit(EXIT_GENERIC)`. A `.gitignore` with ≥2 blocks or non-UTF-8
  content surfaces as a friendly error, never a traceback.
- **Ordering in default mode.** AGENTS.md first (it owns the confirm prompt),
  then `.gitignore`. A failure in either leg exits non-zero; a successful run of
  both prints a combined success line.
- **Workspace root.** `.gitignore` lives at the **repo root**, which is the same
  `root` that `_resolve_root` already returns (the dir containing `.harness/`).
  `init` writes `root / ".gitignore"` — `sync` uses the identical path.
- **`--json`** stays unhonored (no machine-parseable state), as today.

## 4. Out of scope (YAGNI)

- No change to `_CANONICAL_PATHS` itself or to the injector's marker grammar.
- No `init` change (the `.harness/.state.lock` omission is a *separate*
  OPEN-ITEM and not touched here).
- No new gitignore confirm prompt / `--force` flag (the block is always safe to
  regenerate).
- No multi-agent / framework-adapter gitignore concerns (none contribute
  gitignore paths in v0.1).

## 5. Risk

Low. The only behavioral shift is `--agents-md` narrowing from "== no-arg" to
"AGENTS.md-only", on a flag explicitly marked a v0.1 placeholder. Everything else
is additive. The injector is reused unchanged, so the data-loss guards
(fail-loud on duplicate/unbalanced/non-UTF-8) carry over for free. The drift
guard is a pure read-only assertion against a committed file.
