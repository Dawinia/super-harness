# F9 — Relocate `sync_check` out of `core`; extend `core-is-base` to `engineering`

Date: 2026-07-02
Change: `2026-07-02-sync-check-engineering-move-v3` (v1 abandoned after plan
review: both reviewers flagged `core/__init__.py` missing from scope + the
decision body naming the forbidden set in three places, not one. v2 abandoned
after code review: Codex NIT — F9 makes the forbidden-set comment in
`adapters/__init__.py` stale; folded that one-line fix into scope rather than
ship known drift)
Tier: Normal
Finding: `private/REVIEW-FINDINGS-2026-07-02.md` F9 (P2 architecture).

## Problem

`core/sync_check.py` directly imports two `engineering` modules:

```
core/sync_check.py:18  from super_harness.engineering.agents_md_render import ...
core/sync_check.py:19  from super_harness.engineering.gitignore_injector import ...
```

Meanwhile `engineering -> core` holds in 7 places, so the two packages form a
package-level **bidirectional cycle**. The `core-is-base` architecture-fitness
contract (`.importlinter`, anchored by `docs/decisions/d-core-is-base.md`)
forbids `core` from importing `{cli, gates, sensors}` — but **omits
`engineering`**. This is a real blind spot in our own G-FITNESS dimension: #56
severed the transitive `core -> adapters -> sensors` edges, but this *direct*
`core -> engineering` edge stayed open the whole time.

`core` is meant to be the pure base layer that upper layers build on (so the
daemon can import `core` without dragging in the CLI/gate/sensor/engineering
stack). A `core -> engineering` edge violates that invariant.

## Spike (verified, not assumed)

- `core -> engineering` real import edges = **exactly 2**, both in
  `core/sync_check.py`. (`core/paths.py` and `core/review_checklist.py` merely
  mention "engineering" in prose/docstrings — not imports.)
- `sync_check.py` imports **nothing from `core`** — only stdlib +
  `engineering.agents_md_render` + `engineering.gitignore_injector`. It is pure
  sync-orchestration logic.
- Its only importer is `cli/sync.py` (a caller layer — importing `engineering`
  is legal there).
- `lint-imports` with `super_harness.engineering` added to the forbidden list:
  **1 broken**, and the only offending edges are the two `sync_check.py` lines.
  No other `core -> engineering` path exists — so once `sync_check` moves, the
  extended contract passes cleanly.

## Design (approved approach A)

### Home for `sync_check`: `engineering/sync_check.py`

`sync_check` orchestrates two `engineering` concerns (rendering the AGENTS.md
section, injecting the gitignore block) and depends on nothing in `core`. Its
natural home is `engineering`. After the move:

- `engineering.sync_check -> engineering.{agents_md_render, gitignore_injector}`
  — intra-package, fine.
- `cli.sync -> engineering.sync_check` — legal caller-layer dependency.
- `core -> engineering` edges: **zero**.

**Rejected alternatives:**

- *Inline into `cli/sync.py`*: it is substantial, independently-testable
  orchestration; inlining welds it to the CLI layer and drops its unit-test
  seam. No.
- *Sink `agents_md_render` / `gitignore_injector` into `core`*: those are
  engineering concerns (AGENTS.md rendering, gitignore injection). Pulling them
  into `core` is reverse pollution. No.

### Extend the contract to `engineering`

`.importlinter` `core-is-base` forbidden list: add `super_harness.engineering`;
rename the contract to "…cli/gates/sensors/engineering".

`docs/decisions/d-core-is-base.md` is the human-ratified anchor
(`authoring_time: true`, tier-1, with `ratified_text_hash`). We amend its
ratified body. The forbidden set is named in **three** places — all must be
updated together, or `decision ratify` will permanently stamp a self-inconsistent
body (`compute_body_hash` locks whatever text is written; no gate checks internal
consistency):

- headline line (`core/ is the base layer … cli/gates/sensors`);
- the enumerated prose list (`cli`, `gates`, `sensors`);
- the trailing "CLI/gate/sensor stack" phrase.

All three: add `engineering`.

- The #56 parenthetical currently cites `core.sync_check -> engineering ->
  adapters` as one caught transitive edge. After this change that sentence is
  stale (sync_check leaves core; engineering itself is now directly forbidden).
  **Approach A**: keep the #56 history intact and *append* a sentence recording
  that F9 additionally closed the direct `core -> engineering` path and
  relocated `sync_check` to `engineering` — so the record faithfully shows the
  contract growing from 3 upper layers to 4. The appended sentence must state
  explicitly that `sync_check` has since left `core`, so a future reader does not
  grep for a `core.sync_check` edge that no longer exists.

`src/super_harness/core/__init__.py` is the code anchor of d-core-is-base
(`# @decision:d-core-is-base` at line 8) and its module docstring names the same
forbidden set (`cli`, `gates`, `sensors` + "CLI/gate/sensor stack"). It must be
updated to include `engineering` too — otherwise the very package the contract
governs carries prose inconsistent with its own ratified decision (a smaller
version of the exact drift this change closes). No gate text-matches tier-1
anchors, so this is a faithfulness fix, not a gate requirement.

Editing the ratified body changes `compute_body_hash(body)`, so the decision
must be **re-ratified** (`decision ratify d-core-is-base`), which re-stamps
`ratified_text_hash` and re-runs the bite-test. This is a re-ratification, **not**
a tier-2 `reconcile` (d-core-is-base is tier-1: it has a `check` block). The
existing counterexample (`import cli`) still proves the contract bites; a
dedicated engineering counterexample would be gilding — one counterexample per
forbidden contract suffices.

## Change set (scope — 10 files)

| File | Action |
|------|--------|
| `src/super_harness/core/sync_check.py` | removed (git mv) |
| `src/super_harness/engineering/sync_check.py` | added (content unchanged) |
| `src/super_harness/cli/sync.py` | import path `core.sync_check` → `engineering.sync_check` |
| `src/super_harness/core/__init__.py` | docstring: add `engineering` to the forbidden-set prose |
| `src/super_harness/adapters/__init__.py` | TYPE_CHECKING comment: forbidden-set now `{cli,gates,sensors,engineering}` |
| `.importlinter` | add `engineering` to forbidden; rename contract |
| `docs/decisions/d-core-is-base.md` | amend body (3 prose spots + #56 append); re-ratify re-stamps hash |
| `tests/unit/core/test_sync_check.py` | removed (git mv) |
| `tests/unit/engineering/test_sync_check.py` | added (import path fixed) |
| `docs/plans/2026-07-02-sync-check-engineering-move-design.md` | this doc |

`tests/unit/cli/test_sync.py` needs **no** change — its only `sync_check`
references are test-function names, not the module path.

## Implementation order (matters)

1. **Red** — add `super_harness.engineering` to `.importlinter` forbidden;
   `lint-imports` breaks pointing at the two `sync_check.py` edges (the spike's
   fail-first proof, now committed as the red state).
2. `git mv core/sync_check.py engineering/sync_check.py`; fix `cli/sync.py`
   import; `git mv` the test and fix its import line.
3. Update `core/__init__.py` docstring (add `engineering`). Amend
   `d-core-is-base.md` body (all 3 forbidden-set spots + #56 append), then
   **immediately** `decision ratify d-core-is-base` to re-stamp the hash. Keep the
   body edit and the re-ratify adjacent: a drifted `body`-vs-`ratified_text_hash`
   is an `integrity_violation` that drops the decision out of `effective_ratified`
   and hard-dangles all five `@decision:d-core-is-base` anchors until re-stamped.
4. **Green** — `lint-imports` contract KEPT; `decision check` clean;
   `test_sync_check` (new path) + `test_sync.py` + full suite pass.

## Testing

- The `.importlinter` extension is itself the mechanical regression: after the
  move the contract is KEPT; a future re-introduction of any `core ->
  engineering` edge breaks it.
- Unit: `tests/unit/engineering/test_sync_check.py` (relocated) exercises
  `run_sync_check` at its new import path.
- Full suite (`pytest`, ~1600 tests) green with `PATH` including `.venv/bin`.
- `decision ratify --dry-run d-core-is-base` bite-test passes under the new
  contract (real code KEPT + counterexample violates).

## Non-goals / YAGNI

- No behaviour change to `run_sync_check`; pure relocation.
- No new engineering counterexample block (existing `import cli` CE suffices).
- No touching the other 7 `engineering -> core` edges (that direction is the
  correct layering).
