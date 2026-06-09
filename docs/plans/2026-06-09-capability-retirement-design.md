# Design: Capability retirement — drop the dead anchor pipeline, re-root onto decisions

Date: 2026-06-09 (v3, 2026-06-10)
Status: spec (brainstorm-converged; **v3** = round-1 (3 reviewers) + round-2
(2 reviewers) findings folded in, incl. the §5.1 archival collapse and the
foundational two-arm reframe — umbrella §13). Second buildable slice of
the decision-conformance design
(`2026-06-05-decision-conformance-harness-design.md`, the umbrella SSOT),
following slice-1 (`2026-06-08-decision-records-anchors-design.md`). Slice-1
was **pure addition** — it introduced `@decision:` records + anchors + the
`decision check` gate while leaving the old `@capability:` machinery running
untouched. This slice **collects that temporary coexistence**: it removes the
`@capability:`-era machinery and re-roots the surviving code anchors onto real
decisions.

> NOTE: like the sibling design docs in this folder, this file deliberately
> carries **NO** `change:` / `stage:` frontmatter. The repo self-hosts on the
> SuperpowersAdapter, which discovers changes by that frontmatter in
> `docs/plans/`. With it present, the next `adapter scan-once` would auto-emit
> `intent_declared` / `plan_ready`. It stays an inert design artifact until
> `change start` is run explicitly.

---

## 1. What this slice is (and what it is deliberately not)

**In scope — retire the `@capability:` era, end to end:**

1. **Remove the `affected_anchors` lifecycle field** and its entire pipeline
   (reducer / state / framework adapters / `pr_metadata` / `cli/plan`).
2. **Delete the old anchor sensors and their plumbing**:
   `anchor_sentinel_presence`, `anchor_index_rebuilder`, `l1_updater`, their
   private helpers (`_l1_helpers`, `_anchor_policy`), the anchor baseline inside
   `verification_runner`, the builtin registrations, the `on-merge` sensor
   dispatch, the `.harness/anchors/index.yaml` artifact + its path, and the
   `anchor` CLI group (`sync` / `list`).
3. **Re-root the surviving in-code anchors**: migrate the repo's `@capability:`
   sentinels to `@decision:` anchors — *not* by mechanical 1:1 rename, but by the
   projection settled in §3: extract the betrayable decision each one embodies
   (where one exists) into a ratified decision record and place a narrow anchor
   on the code that can violate it; delete the rest as the dead labels they are.
4. **Keep the parametrized `anchor_scanner`** (slice-1 made it generic); drop its
   vestigial `@capability:` default keyword now that no caller needs it.
5. **Reconcile every dependent surface** — the private CLI spec, the bundled CI
   template, the hand-written `getting-started.md`, `AGENTS.md`, and the
   regenerated `cli-reference.md` (Part E, §10).

**Explicitly NOT in scope** (deferred *by boundary*, registered in
`private/OPEN-ITEMS.md`):

- The `doc ↔ decision` leg (doc anchors + regen-and-diff checker) — its own slice.
- The integrity-lock / betrayal teeth (change-detection, re-check, the
  re-ratification lock) — umbrella §7.2.
- The checkability tier (executable-check-per-decision, hard-anchor vs context) —
  umbrella §12.3. This slice still gates only on *referential integrity*.
- **The upper links (Spec↔Plan, Plan↔Code)** — umbrella §12.1. See §6.
- **Full decomposition of every module into all its decisions** — that is the
  AI-proposes-decisions / spec-authoring scope. This slice does a **bounded,
  conservative** re-root (§7.2), not an exhaustive decision census.

### 1.1 How this honors slice-1 §9's keyword contract (the reading, made explicit)

Slice-1 §9 (verbatim, echoed in OPEN-ITEMS #7(b)): *"the migration MUST adopt
`@decision:` (not introduce `@implements:`, not retain `@capability:`) — so three
keywords never coexist past that slice."* Two readings are textually available and
**round-1 review flagged that I must not silently pick one**:

- **Strong:** every `@capability:` sentinel must be *converted* 1:1 into a
  `@decision:` anchor.
- **Weak (chosen):** the slice's end-state must contain **no `@capability:`** and
  use `@decision:` as the only anchor keyword.

I choose the **weak** reading, and argue it on umbrella grounds rather than
assuming it: a non-betrayable module label (§3) has **no decision to become** —
forcing it into a `@decision:` record re-creates the exact anti-pattern the whole
design exists to kill ("tags float free… rooted in nothing", umbrella §4;
"too-vague decisions = the 34-dangling problem wearing a ratification stamp",
umbrella §11.3). Slice-1 §9's own stated *purpose* — *"so three keywords never
coexist"* — is keyword hygiene, which **deletion satisfies completely** (a deleted
sentinel is neither `@capability:` nor `@decision:`). So deleting a dead label
honors the contract's intent **better** than converting it to a fake decision.
This is the one place this slice overrides a sibling "MUST"; it is overridden
deliberately and on the SSOT's own logic, not by convenience.

This slice is the keyword-contract closer: after it, **no `@capability:` remains in
source, `@decision:` is the only anchor keyword, `@implements:` is never
introduced.**

## 2. The central finding: this is dead code, not a live nerve

The instinct is "ripping `affected_anchors` out of the reducer/state could crash
the whole lifecycle." Reading the real code says the opposite — **this pipeline
has never carried current in this repo:**

- `reducer.py:139` only sets `affected_anchors` when a `plan_ready` event's
  payload *contains* that key.
- Who puts the key there? Only two adapters. `openspec.py:145` emits `payload={}`
  unconditionally. `superpowers.py:115` (`_plan_payload`) copies `affected_anchors`
  only if the plan artifact's **frontmatter** has it — and every design/plan doc in
  `docs/plans/` deliberately carries no frontmatter.
- Net: across all real self-host history, `affected_anchors` has been the empty
  list. (Confirmed by all three round-1 reviewers against the real tree.)

The three downstream "consumers" therefore idle:

- `anchor_sentinel_presence.py:71` — `if not declared: return pass`. With
  `declared` permanently empty, **this sensor is a no-op that always passes.**
- `l1_updater.py:214` — short-circuits on empty anchors; its **PR-creation half has
  never fired** (umbrella §8 already judged this pipe "inert"). **But — correction
  caught in round-2 — its short-circuit branch (`:217-227`) STILL emits
  `l1_update_completed{pr_url:None}`, and that event is the SOLE trigger of the
  `MERGED → ARCHIVED` transition** (`transitions.py:43`; `l1_update_completed` has no
  other emitter). So l1_updater was *not* fully dead: its terminal emission is the
  live archival path. Deleting it leaves `MERGED` a dead end unless archival is
  re-homed — handled by §5.1 (the collapse).
- `verification_runner.py:370` (anchor baseline) — fed the same empty list.

**Consequence for risk framing (revised after round-2):** removing the
`affected_anchors` pipeline + the sentinel/index sensors is a *structural* change
with **no behavioural effect** (those paths idle). The **one genuine behavioural
change** is archival: l1_updater's terminal `l1_update_completed` currently drives
`MERGED → ARCHIVED`, so its removal requires the §5.1 state-machine change (merge
archives directly). That single change touches the lifecycle state machine — the
one place real care is needed. Everything else is effort/blast-radius risk, not
correctness risk: the real cost is the **dependent test files** (concentrated in
`test_l1_updater.py`, 79 refs), which turn red and must be deleted/updated in
lockstep. Mitigation (mandated): every task runs the **full** pytest suite + ruff +
mypy, so a broken green test or a stale import surfaces immediately.

**Count correction (this supersedes slice-1 §8 / OPEN-ITEMS #7's "34"):** the repo
has **20 distinct `@capability:` ids in `src/`** (across 21 sentinel lines —
`framework-adapter-builtin` appears on both framework adapters), not 34. A further
14 `cap-*` ids are synthetic test fixtures — not migrated, and `tests/**` is already
excluded from anchor scanning.

## 3. Conceptual foundation: capability is retired as a *concept*, not renamed

This slice rests on a settled conceptual point (the brainstorm that produced it),
recorded here because it dictates §7.2.

**A code anchor's only job is to be a tripwire tying a spot in the code to a human
decision the code could betray.** Its entire value is that the thing it points at
can be *violated* — that is what makes the edit-time reminder, the dangling check,
and the future re-check meaningful. A thing that cannot be violated gives the
machinery nothing to guard.

**Capability and decision are different axes, not synonyms:**

- A **capability** answers "what the system does" — a feature/module; a noun; it
  exists or it doesn't; it **cannot be betrayed** (`gh-integration` is always the
  gh-integration module).
- A **decision** answers "what was chosen, and could have been otherwise" — a
  proposition that can be honored or violated ("use the `gh` CLI, not raw REST";
  "state is a pure fold, never mutated in place").

The relation is **many-to-many and lossy**: one capability embodies several
decisions; one decision spans several capabilities. The *existence* of a
capability is itself a degenerate "we decided to provide X" decision — but
precisely the uncheckable, unbetrayable kind. So saying "capability ≈ decision" is
true only at the **useless end** of the spectrum; they diverge exactly where this
tool earns its keep (betrayability).

**Attribution (corrected per round-1):** this lossy-projection framing is *this
design's synthesis*, grounded in umbrella **§3** (the "many-to-many web" between
decisions and code sites) and umbrella **§12.3** (the rung-3 "context, never gates"
class for uncheckable decisions). Umbrella **§8** only says `@capability` anchors
"become decision anchors, valid only when rooted in a ratified Spec decision" and
calls the dangling ones "not assets" — it does **not** itself state lossy-ness or
that deletion is a valid outcome. So: §8 says "re-root onto decisions"; §3 + §12.3
are what tell us the mapping is lossy and that label-only capabilities have no
decision heir. **Capability therefore retires as a unit, as an anchor, and as a
concept in the enforced model.** The only enforced unit is the decision. (If
feature-grouping is ever wanted, it is an optional `area:` label on a decision —
never a resurrected capability entity, never an anchor.)

**Honest cost named:** the old `@capability` was bound to a "one L1 capability spec
doc per capability" layer. Killing the concept means that doc layer has no
same-named heir — its heir is the per-decision record (finer-grained), consistent
with the umbrella's deferred doc-leg.

## 4. Part A — remove the `affected_anchors` pipeline

Delete the field and every site that reads or writes it (the full set, confirmed by
grepping `src/`):

- `core/state.py` — drop the `affected_anchors` field from `ChangeState` (and its
  docstring mention at ~46).
- `core/reducer.py:139-140` — drop the `plan_ready` branch that sets it.
- `cli/plan.py:106,148` — drop the `--anchors` option and the payload population.
- `adapters/framework/superpowers.py` — drop `affected_anchors` from
  `_plan_payload`'s copied-key tuple (`:115`, leave `scope` / `tier_hint`) **and**
  fix the stale prose in its `agents_md_subsection`-feeding docstring (`:272`,
  "A plan may also carry `affected_anchors`…").
- `engineering/pr_metadata.py:193-204,270,279` — drop `_derive_affected_anchors`
  and the "Affected anchors:" line it feeds in the PR body.

**`state.yaml` backward-compat: none needed, no `schema_version` to bump.**
Verified: neither `core/state.py` nor `core/state_yaml.py` carries a
`schema_version`. `state.yaml` is a gitignored, *derived* artifact, rebuilt from
`events.jsonl` on every read; serialization is `asdict()` and the read path is a
plain dict-load with no `ChangeState` reconstruction. A stale `state.yaml` that
still carries the key just has it ignored; old `plan_ready` events that still carry
`affected_anchors` are simply not read.

## 5. Part B — delete the old anchor sensors and plumbing

All confirmed dead (§2) or load-bearing for nothing. **The verification_runner
surgery is enumerated in full** because round-1 caught that a partial removal
crashes the daemon (B1):

- **`sensors/anchor_sentinel_presence.py`** — the no-op sensor. Delete.
- **`sensors/l1_updater.py`** + **`sensors/_l1_helpers.py`** — the never-fired L1
  follow-up pipe. Delete both.
- **`sensors/anchor_index_rebuilder.py`** — writes `.harness/anchors/index.yaml`,
  which (verified) **no production code reads**; only the `anchor list` CLI
  consumed it. Delete.
- **`sensors/_anchor_policy.py`** — used only by the sentinel sensor + the
  verification baseline, both removed here. Delete.
- **`sensors/verification_runner.py`** — remove the **entire** anchor baseline, all
  of it, or the module won't import:
  - the `scan_sentinels` import (`:51`) **and** the
    `from …_anchor_policy import anchor_must_pass_for_tier` import (`:75`);
  - the `_BASELINE_ANCHOR = "anchor-sentinel-presence-final"` constant (`:294`) and
    its membership in `BASELINE_CHECK_IDS` (`:301-302`) — this shrinks the
    single-sourced, `__all__`-exported `BASELINE_CHECK_IDS` from 3 → 2, which is a
    deliberate **public verification-baseline contract change** (consumed by
    `collectable_check_ids`, `:787`);
  - `_baseline_anchor_presence` (`:342-383`) and the tier-aware build-time block
    that resolves `anchor_must_pass_for_tier` and registers the `CheckTask`
    (`:591-607`, incl. the `_run_anchor` closure).
  - **(round-2 completeness)** the surgery spans the whole `baseline_check_tasks`
    function (`~:542-640`), not just `:591-607`: its docstring and the module
    docstring (`~:20-22`, "builds 3 in-process baselines: `anchor-sentinel-presence-final`…")
    still name removed symbols and must be updated to "2 baselines". After removal,
    confirm **no** surviving reference to `anchor_must_pass_for_tier` / `scan_sentinels`
    / `_BASELINE_ANCHOR` / `_run_anchor` remains in the file.
  The rest of the runner stays.
- **`sensors/__init__.py:136-154`** — remove the three `register_builtin(...)` lines
  and their imports. (Verified: the daemon does **not** directly reference these
  sensors; the only crash path was the transitive `_anchor_policy` import in
  verification_runner, closed above.)
- **`cli/on_merge.py`** — remove the `SensorDispatcher([L1Updater(),
  AnchorIndexRebuilder()])` dispatch (`:263-273`), the `_l1_followup_pr_from_results`
  walk, the `_SENSORS_TRIGGERED` constant, and the `sensors_triggered` /
  `l1_followup_pr` `data` fields. **`on-merge` still emits `merged`**
  (`_emit_merged`, `:241`, independent of dispatch); under §5.1's collapse,
  `merged` now drives `READY_TO_MERGE → ARCHIVED` **directly** (no MERGED beat, no
  l1_update_completed). The remaining `data` is `commit_sha` / `change_id` /
  `events_emitted: ["merged"]`. (Spec + golden + freeze-test reconciliation: Part E / §9.)
- **`cli/anchor.py`** — delete the whole `anchor` group (`sync` / `list`); its
  introspection role is taken by slice-1's `decision show` / `decision list`.
  Remove its registration from the CLI group.
- **`core/paths.py:121-123`** — remove `anchors_index_path` (only remaining callers
  after the deletions above were the deleted files + the gitignore entry).
- **`engineering/gitignore_injector.py:76`** — remove `.harness/anchors/index.yaml`
  from the managed block (no longer generated). Re-sync the committed `.gitignore`
  (`sync --gitignore`) so the drift-guard test stays green.
- **`cli/init.py:194,198`** — `init` scaffolds `.harness/anchors/` and
  `.harness/pending-l1-updates/` dirs whose only writers (anchor-index-rebuilder,
  l1_updater) are deleted. **Decision: drop both dir-creations** (they would be
  orphaned), and update `tests/integration/cli/test_init.py` accordingly (§9).

### 5.1 The archival collapse (the ONE state-machine change — handle with care)

Deleting `l1_updater` removes the sole emitter of `l1_update_completed`, which is the
sole trigger of `MERGED → ARCHIVED` (§2). So archival must be re-homed. **Decision:
collapse — `merged` archives directly.** Rationale (grounded in umbrella §13): `MERGED`
("L1 update pending", `gates/decisions.py:31`) existed *only* to wait for the post-merge
L1 write-back; the decision-conformance model has **no post-merge doc step** (doc
conformance, when built, runs *at PR time*, not after merge — umbrella §13.1). So the
beat `MERGED` represented no longer exists; keeping it is dead scaffolding.

Concrete state-machine surgery (all mechanical, fully test-covered):
- **`core/transitions.py`** — change `("READY_TO_MERGE", "merged"): "MERGED"` (`:42`) to
  `→ "ARCHIVED"`; **delete** `("MERGED", "l1_update_completed"): "ARCHIVED"` (`:43`).
- **`core/state.py:22`** — remove `"MERGED"` from the state enum/list (11 → 10 states).
- **`gates/decisions.py`** — remove the `"MERGED"` rows from `PRE_TOOL_USE_DECISIONS`
  (`:31`) and `SUGGESTIONS` (`:46`).
- **Event types `l1_update_completed` / `l1_update_failed`** (`core/events.py:34`,
  `transitions.py:23`) — now have no emitter and no transition; **delete the event
  types** (they were l1_updater-only). `merged_reverted` and the other informational
  events are unaffected.
- **lifecycle-event-model spec** — the "11-state" contract becomes 10; the
  `MERGED → l1_update_completed → ARCHIVED` rows go (Part E).

Honest note: this is the only place slice-2 touches the lifecycle state machine — the
exact "命脉" flagged as the danger zone. It is bounded (deletions + one re-point) and
guarded by the full suite + the E2E lifecycle canary (§9). Alternatives considered and
rejected: (b) keep `MERGED` + a new terminal event `change_archived` — adds an event
type for a beat with no content; (c) on-merge emits `l1_update_completed` directly —
keeps a misnamed "l1 update completed" event with no l1 update (dishonest). Collapse is
the honest end-state.

## 6. What is genuinely lost vs honestly deferred

`anchor-sentinel-presence`'s *semantic* was "the active change's plan declared
anchor X — is X present in code?" — a per-change **Plan↔Code presence** check. Two
honest facts:

1. It **never executed** that semantic (declared was always empty, §2) — so
   deleting it removes **no running capability**.
2. The real upper-link machinery belongs to the **upper-links slice** (umbrella
   §12.1) and is unbuilt anywhere today.

So this is **not a silent loss**: the dead shell is removed here; the intent is
relocated — explicitly registered in OPEN-ITEMS — to the upper-links slice.
**Precision (corrected per round-1):** umbrella §12.1's *mechanical* upper-link
check is **dangling-down on plan-items** (set difference: ratified decisions vs
decisions-with-a-plan-item) — a Spec↔Plan property. The old sensor was a *degenerate
Plan↔Code presence* check, a cousin, not that exact primitive. The deferral target
is right; the lineage is "related upper-link family", not "the same check". Slice-1's
whole-repo dangling-down already covers the *global* "ratified decision has code";
what's deferred is the *per-change* "this plan's declared decisions are present".

## 7. Part C/D — keep the scanner, re-root the anchors

### 7.1 Scanner (Part C)

`core/anchor_scanner.py` **stays** — `decision check` and `decision show` use it.
After §5 deletes the old callers, the **only** surviving callers
(`core/decision_check.py:50`, `cli/decision.py:198`) already pass
`keyword="@decision:"` explicitly. So remove the vestigial `@capability:` default:
delete the `_DEFAULT_KEYWORD` constant (`:50`) and the `keyword=_DEFAULT_KEYWORD`
default from **both** `scan_sentinels` (`:156`) and `scan_sentinel_locations`
(`:114`), making `keyword` a **required keyword-only argument**. This removes the
last `@capability` reference in the engine and forces call-site explicitness.

### 7.2 Migration / re-root (Part D)

Of the 20 `src/` `@capability:` ids, **3 live in files deleted in §5**
(`capability-l1-anchor-check`, `capability-l1-follow-up-pr`, `capability-l1-updater`
in `anchor_sentinel_presence.py` / `_l1_helpers.py` / `l1_updater.py`). They vanish
with their files. That leaves **17** module-label sentinels.

Two settled knobs:

**Placement = α (narrow, on the betrayable code).** Even though this slice's gate
(referential integrity) is indifferent to *where* or *how many* anchors sit, the
anchor's entire future value (edit-reminder + the deferred teeth) requires it to be
**on the code that can violate the decision**, not on a file-top comment (the β
anti-pattern that hollowed the old anchors). Each re-rooted anchor is placed at the
narrowest site embodying its decision.

**Extraction depth = conservative, and the set is frozen in the plan.** This is a
*retirement* slice, not a decision census. For modules that visibly embody **one**
load-bearing, betrayable, *non-cross-cutting* architectural decision, write a crisp
ratified `d-<x>` record and place a narrow anchor in the **same commit**
(per-commit-green, §8). For everything else, **delete the sentinel, write no
record**, and register the deferral in OPEN-ITEMS. **The exact record set is frozen
in the TDD plan with a hard cap (~7); "pulling another one up" requires a new slice,
not in-flight scope growth.** A cross-cutting decision (no single anchor home) is
**not ratified in this slice at all** — it is deferred whole to the checkability /
region-marker slice, so this slice creates **no permanent dangling-down warn**
(corrected per round-1: the transient-warn channel is not repurposed for a
permanent-by-construction state).

**Candidate `keep` set to propose (AI proposes; human ratifies at plan-approval;
final set frozen in the plan):**

| Module (current `@capability:`) | Betrayable decision (proposed `d-…`) |
|---|---|
| `core/reducer.py` (state-reducer) | State is a pure left-fold over the event log; never mutated in place. |
| `core/events.py` (event-stream) | Events are append-only; the log is the source of truth, state is derived. |
| `core/transitions.py` (state-machine) | Transitions come only from the fixed declared matrix; no ad-hoc transition. |
| `gates/pre_tool_use.py` (gate-architecture) | Gate policy lives in exactly one literal (`gates.decisions`); daemon + in-process gate both read it, neither invents policy. |
| `engineering/attestation.py` (merge-gate) | The merge gate verifies committed evidence with pure git — no network, no runtime trust. |
| `core/identity.py` (actor-identity) | Identity resolution order is fixed: `--as` > env > git config > `"cli"`. |
| `engineering/gh.py` (gh-integration) | GitHub access goes through the `gh` CLI, never raw REST. (Pulled into `keep` per round-1: it is the umbrella's own canonical betrayable exemplar, §3/§4; dropping it while keeping the same-class `merge-gate` was inconsistent.) |

**`cli-surface` is demoted to defer (corrected per round-1):** "every command emits
the frozen 6-key JSON envelope + follows the global exit-code convention" is
(a) two decisions bundled and (b) **cross-cutting** across every CLI file — a single
anchor on `cli/__init__.py` would be the exact β file-top pattern this section
forbids. It genuinely *wants an executable check* ("every command's `--json` output
has exactly the 6 keys") → defer to the checkability slice; delete the sentinel,
write no record now.

**`delete + defer`** (module-existence labels or multi-decision modules):
`adapter-protocol`, `agent-adapter-builtin`, `agents-md-injection`, `ci-templates`,
`framework-adapter-builtin`, `pr-metadata`, `sensor-architecture`,
`verification-runner`, `verification-runner-config`, `cli-surface`. Each registered
in OPEN-ITEMS as "decisions for `<module>` to be authored in the spec-authoring
slice."

## 8. Migration ordering + the per-commit-green invariant

**Order: retire first, re-root second.** Do Parts A + B (delete the dead pipeline
and sensors) **before** touching sentinels. Once the machinery is gone, the
remaining `@capability:` strings are inert text no scanner reads — no half-migrated
window where an old sensor sees a partially-renamed set. (Verified safe: no
`@decision:` anchor lives in any deleted file, so deleting files removes no
ratified-record root; the existing `d-dangling-check` / `d-decision-records` anchors
live in `core/decisions.py` / `core/decision_check.py`, untouched.)

**Per-commit green for the gate.** `decision check`'s only hard failure is
dangling-up. Rules that keep every commit green:
- A re-rooted anchor and its **ratified** record land in the **same commit** → no
  dangling-up, and (because we anchor each kept decision immediately) **no new
  dangling-down warn** either.
- Deleting a `@capability:` sentinel creates no dangling (different keyword).
- Un-migrated `@capability:` strings are invisible to `decision check`, so a
  partially-done migration never false-positives.

**The migration PR does not block itself.** At branch tip, every `@decision:` anchor
roots in a ratified record (no dangling-up); the cross-cutting/deferred decisions
are *not recorded* this slice (so they don't warn). The lifecycle gate
(PreToolUse / attest) is a separate concern handled in §9.

## 9. Test blast radius

**Delete (their subject is gone):** `test_l1_updater.py`, `test_l1_helpers.py`,
`test_anchor_index_rebuilder.py`, `test_anchor_sentinel_presence.py`,
`tests/unit/sensors/test_anchor_policy.py` (hard-imports the deleted
`_anchor_policy`), `tests/unit/cli/test_anchor.py` (the `anchor` CLI), and the
`test_frozen_sensors_triggered_matches_registered_merged_sensors` test in
`tests/integration/cli/test_on_merge.py:591` (after removal there are zero
merged-triggered builtins and `_SENSORS_TRIGGERED` is gone).

**Edit:**
- `test_on_merge.py` — drop dispatch / L1-PR / `sensors_triggered` assertions; keep
  `merged`-emit + exit-code coverage.
- `test_pr_metadata.py` — drop `_derive_affected_anchors`.
- `test_plan.py` — drop `--anchors`.
- `test_state_yaml.py` + `test_state.py` — drop the field.
- `test_superpowers.py` — drop the `affected_anchors` passthrough.
- `test_verification_runner.py` — drop the `_baseline_anchor_presence` tests
  (`~:921-986`) **and** fix the `BASELINE_CHECK_IDS`-set assertions
  (`~:452/465/488`) to the new 2-id set. **(round-2 completeness)** also: the `:31`
  import of `_baseline_anchor_presence`, the tier-resolution test (`~:368/374`), the
  `only_ids=["anchor-sentinel-presence-final"]` cases (`~:977/994`), and the
  lifecycle tests asserting the anchor id in collected results (`~:1116/1194`).
- `test_anchor_scanner.py` — adapt to the now-required explicit `keyword`.
- `tests/integration/cli/test_init.py:51` (`test_init_creates_all_subdirs`) — drop
  the `anchors` / `pending-l1-updates` dir assertions (`~:54,58`); **(round-2) also**
  remove `.harness/anchors/index.yaml` + `pending-l1-updates/` from the
  `_CANONICAL_GITIGNORE_PATHS` assertion (`~:435-436`), matching the
  `gitignore_injector` change (§5).
- **(§5.1 collapse) state-machine tests** — any test asserting the `MERGED` state,
  the `11`-state count, the `("READY_TO_MERGE","merged")→MERGED` /
  `("MERGED","l1_update_completed")→ARCHIVED` transitions, the `MERGED` gate
  verdict, or the `l1_update_completed` / `l1_update_failed` event types must be
  updated to the 10-state, merge-archives-directly model (`test_transitions.py`,
  `test_state.py`, `test_state_yaml.py`, the gate/decisions tests, any reducer test
  touching `l1_update_*`). Full-suite run is the safety net.

**Integration safety net — `tests/e2e/openspec_claude_code/test_full_lifecycle.py`.**
This is **multi-block surgery, not a one-line edit**: it carries an
`affected_anchors: [cap-hello]` fixture (`:131`), asserts the anchor-index existence
+ `cap-hello in idx` (Phase L, `:251-255`), and asserts `l1_update_completed`
emission (`:271`). All of that goes; the surviving assertion is "`on-merge` emits
`merged` and the change reaches ARCHIVED with no anchor dispatch." This E2E is the
canary that the lifecycle still completes end-to-end after the surgery — rewrite
Phases K/L/M + the fixture + docstring deliberately.

## 10. Part E — surfaces to reconcile (specs, docs, template)

Round-1 caught that the forgotten surfaces — not the code — are the real
drift risk. Enumerated explicitly so none is "grep and hope":

- **`private/specs/2026-05-27-cli-command-surface.md`** (the real spec — gitignored,
  *not* under `docs/`). Edit FIVE surfaces: `on-merge` behaviour (`:311`, drop the
  dispatch description), the `on-merge` data block (`:657-664`, drop
  `sensors_triggered` / `l1_followup_pr`), `plan ready … [--anchors <ids>]`
  (`:421,425`), the entire **`anchor list`** section (`:449-460`), and the entire
  **`anchor sync`** section (`:464-470`).
- **`docs/cli-reference.md`** (auto-generated golden, CI `--check`-gated): regenerate
  after the CLI surface changes (removed `anchor` group, removed `--anchors`, changed
  `on-merge` output). Self-heals on regen but must be regenerated in-slice.
- **`src/super_harness/templates/super_harness_workflow.yml:148`** — the merge-job
  step name "Process merged change (trigger l1-updater, etc.)" and the `:117-126`
  comments about l1-updater become lies (the `on-merge` *command* stays valid and
  still emits `merged`; only its side effects are gone). Fix the step name + comments
  (ships to every adopter via `init`).
- **`docs/getting-started.md:282-345`** — **named edit task, not "grep docs".** A
  hand-written user walkthrough with **no drift test**: it describes `on-merge`
  dispatching the L1-updater (`:300-303`) + anchor-index-rebuilder refreshing
  `.harness/anchors/index.yaml` (`:305-306`), the L1 follow-up PR (`:310`), a Note to
  run `anchor sync` first (`:321-322`), `anchor list` in the inspect commands
  (`:330`), and "anchor-sentinel-presence-final warns" (`:345`). All describe a
  lifecycle stage that **ceases to exist** → multi-paragraph rewrite.
- **`AGENTS.md`** — `:22` ("A plan may also carry `affected_anchors`…") and `:80`
  ("anchor sentinels" in the verify-gate description) go stale → update prose.
- **(round-2) `private/specs/2026-05-26-sensor-gate-architecture.md`** — same-vintage
  SSOT spec describing the deleted machinery as live: the built-in sensor table
  (`~:326-340`), §3.1.9 l1-updater + §3.1.10 anchor-index-rebuilder, the
  `affected_anchors` reducer snippets (`~:462/489/516`), the `capability-l1-updater`
  carve-out (`~:845-847`). Mark the deleted sensors retired + drop `affected_anchors`.
- **(round-2) `private/specs/2026-05-26-lifecycle-event-model.md`** — the
  `affected_anchors` field + reducer case (`~:156/251/308/538`), the l1_update events,
  **and the state machine**: this is where the `11`-state matrix and the
  `MERGED → l1_update_completed → ARCHIVED` rows (`~:346/363/416/433`) live → update to
  the §5.1 collapse (10 states, merge archives directly). This spec is the SSOT for
  the state machine the collapse changes, so it is **required** in-slice.
- **`private/VISION.md`** — already carries the superseding note (§1.1) pointing at
  umbrella §13; the `anchor-sentinel-presence` / `l1-updater` v0.1 deliverables and the
  "L1 更新机制" decided-item are covered by that note (no further per-line edit needed).

## 11. Validation — run this slice *through* the self-host lifecycle (honest scope)

Per "按 super-harness 流程来": dogfood the development itself, with the honesty
caveat round-1 demanded.

- Design + TDD plan are authored **with no active change** (the PreToolUse gate
  allows edits only in `PLAN_APPROVED` / `IMPLEMENTATION_IN_PROGRESS` /
  `CODE_REVIEW_REJECTED`, and blocks all edits — incl. docs — in `INTENT_DECLARED`
  / `AWAITING_PLAN_REVIEW`; with no active change it allows). So planning precedes
  `change start`.
- Then `change start` → `plan ready` → independent plan review →
  **`review approve --reviewer plan-reviewer`** (emits `plan_approved`, verified in
  `cli/review.py:45` — this IS a real production emitter, *not* the lifecycle gap) →
  implement under the gate → independent code review →
  **`review approve --reviewer code-reviewer`** (emits `code_review_passed`) →
  `done` → (PR only on the user's say-so; whole branch as one PR).
  - The only transition lacking a public emitter is `implementation_started`
    (PLAN_APPROVED → IMPLEMENTATION_IN_PROGRESS) — but the gate **allows edits in
    PLAN_APPROVED already**, so implementation proceeds without it; no manual event
    seeding is required for this flow (unlike the slice-1-era concern).
- **Honest scope of the gate-proof (corrected per round-1, per memory
  `harness-never-spawns-agent` + the OPEN-ITEMS subagent-bypass caveat):** the
  PreToolUse hook governs the **main Claude Code session's** tool calls. If
  implementation uses `subagent-driven-development`, those Task-tool subagents'
  edits may **not** route through the same hook. So the dogfood proves the lifecycle
  state machine + the *main session's* edits respect the gate — it is **not** a
  claim that each subagent file-write was individually gated. To keep the proof
  meaningful, the gate-relevant transitions (the `change start` / `plan ready` /
  `review approve` / blocked-state edit attempts) are exercised from the main
  session; the file kill switch (`.harness/gate-disabled`) is used only for
  out-of-change bookkeeping edits.

## 12. Edge cases / risks

| Case | Disposition |
|---|---|
| `verification_runner` import after `_anchor_policy` delete | **B1**: must remove the `:75` import + `:594` call + the whole baseline block, or the daemon crashes — enumerated in §5 |
| `on-merge` after dispatch removal | still emits `merged`; under §5.1 collapse `merged` archives directly (READY_TO_MERGE→ARCHIVED, no MERGED); `data` schema shrinks → spec + golden + freeze-test updated (§5, §9, §10) |
| Archival path loses its emitter (l1_update_completed deleted) | **§5.1 collapse**: re-point `(READY_TO_MERGE,merged)→ARCHIVED`, drop MERGED state + dead edge + l1_update_* events; the ONE state-machine change |
| Daemon startup after unregistering 3 builtins | unaffected — no direct reference; the only crash path (transitive `_anchor_policy`) is closed in §5 |
| Stale `state.yaml` / old `plan_ready` events with `affected_anchors` | ignored on read; derived artifact, no `schema_version`, no migration (§4) |
| `@capability:` left un-migrated mid-branch | invisible to `decision check`; no false positive (§8) |
| Cross-cutting decision (e.g. cli-surface) | **not ratified this slice**; deferred whole → no permanent warn (§7.2) |
| `cli-reference` drift | regenerate golden in-slice (§10) |
| Subagent edits bypassing the gate | acknowledged; gate-proof scoped to main-session edits (§11) |

## 13. Deferred + honest limits

Registered in `private/OPEN-ITEMS.md` (umbrella item #7):

- **Per-change Plan↔Code presence check** → upper-links slice (§6, umbrella §12.1).
- **Decisions for the ~10 deleted module-labels (incl. the cross-cutting
  cli-surface)** → spec-authoring / checkability slice (§7.2).
- **Checkability tier / executable checks / the teeth** → unchanged from slice-1.

Honest limits (unchanged ceilings): `ratified_by` is self-asserted (solo-owner
ceiling); the check verifies structure, not semantics; the CI rail is only as hard
as branch protection.

## 14. One-line summary

> The `affected_anchors` pipeline and the old `@capability` sensors never carried
> current in this repo, so retiring them is structural surgery, not a behavioural
> change — the risk is the dependent test files and the forgotten surfaces (the
> private CLI spec, the bundled template, the hand-written getting-started), met by
> running the full suite per task and enumerating every surface (Part E). Capability
> retires as a *concept*, not a rename: code anchors exist only to tie code to a
> *betrayable* decision, so the ~7 surviving sentinels that embody one are re-rooted
> (narrow anchors on the code that can violate a crisp ratified decision), the dead
> labels deleted, cross-cutting and exhaustive decision-authoring left to their own
> slices. Retire first, re-root second; every commit stays dangling-up-clean and
> warns-clean.
