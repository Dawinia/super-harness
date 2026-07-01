# Authoring-time conformance feedback (Stop hook, cut-1) — design

> When a Claude Code agent finishes a turn, run the ratified tier-1 decision checks
> once and, if a check fails, block the stop and feed the deterministic
> "you violated decision X" verdict back so the agent self-corrects **before** it
> hands the turn to the human / merge gate. Reuses the existing `decision check`
> machinery — **no new relevance model, no per-edit trigger**. Agent-agnostic verdict
> core + per-agent delivery seam + unchanged CI cold-path floor.
> **Cut-1 ships Claude Code delivery only**; Codex is a de-risked follow-on.
>
> Written 2026-07-01. Shaped by four independent cross-reviews (Claude + Codex ×
> mechanical + architecture) and a LIVE `claude -p` stub that validated the core
> hypothesis before any durable code was written. See
> `private/research/2026-07-01-agent-conformance-pain.md`, memory
> `project-agent-conformance-research`, and the REVISION-1/2 history at the bottom.

## 1. Why — and the hypothesis, now with a stub result

The sharpest research pain is "rules are written down, the agent reads them, and
violates them anyway" — enforced today only by a human in a 5–10×/session review
loop, or by deterministic tools (import-linter) that fire at CI, after the code is
written. The named gap is an **authoring-time feedback loop**: let the agent
self-correct before a human/CI does.

**Hypothesis H:** deterministic, precise, turn-end feedback naming the *specific*
decision the agent violated makes it self-correct — and does so for violations the
agent *cannot self-police*, which is the case that matters.

**Stub result (2026-07-01, LIVE `claude -p`, N=1, true-causal):** with a throwaway
Stop hook and **no CLAUDE.md rule**, the agent introduced a `core → sensors` import,
tried to end its turn, got the turn-end advisory, and **self-corrected to compliant
working code** (dependency injection) — citing the advisory in its own words. H is
supported enough to build.

**But the stub also sharpened the value claim (honest).** The baseline (a CLAUDE.md
rule, no hook) did *not* reproduce the research's "ignores the rule" failure for this
strong model + one blatant rule — instead the agent **refused up-front and punted the
task to the human**. So the value of this cut is **not** "static rules fail, we
succeed" for the easy case. The defensible value is two-fold:

1. **Catch what the agent cannot self-police.** A strong model that has read
   "core must not import sensors" can *still* introduce a **transitive** violation
   (`core → adapters → sensors`) because it cannot check the import graph in its head
   — exactly the edge that grep, Codex, and Plan all missed in PR #56 and only
   import-linter's real graph engine caught. Static prose cannot catch transitive
   edges; a deterministic graph check + precise feedback can.
2. **Self-heal to working code**, rather than the baseline's "refuse and punt": the
   feedback path let the agent deliver a compliant, *complete* implementation.

**Consequence for the bite-test (§7):** it must target a **transitive** violation
(the real `core → adapters → sensors` shape), not a blatant direct import — the direct
case a strong model dodges on its own and proves nothing.

**H may still be false at scale** (N=1; weaker models, fuzzier rules, conflicting
constraints). If the bite-test shows the agent ignores the turn-end verdict, this cut
is token-noise over the floor — reported honestly. The floor stands either way.

## 2. Shape — per-turn Stop hook, reuse `decision check`

```
agent finishes a turn
      │  (Stop hook — stdin carries stop_hook_active, NOT a changed file)
      ▼
super-harness-hook --agent claude-code --event stop
      │  run the ratified, authoring-opted-in tier-1 checks ONCE (full, sound)
      ▼
[AGNOSTIC VERDICT CORE]  reuse run_executable_checks → Verdict{violations|clean|unavailable}
      │  violations AND not stop_hook_active?
      ▼  yes                                   │ no (clean / unavailable / already-nudged)
AgentAdapter.format_stop_feedback(verdict)      │ allow stop (exit 0, no output)
      │                                         ▼
      ▼  {"decision":"block","reason": advisory}   CI cold-path floor still authoritative
agent gets another turn → self-corrects (H under test)
```

Because a Stop hook has **no changed file** and runs **once per turn**, there is **no
relevance model, no `applies_to` path-glob, no new per-file core module** (all cut by
the cross-review). We run the full check set once — sound (whole graph, not the
unsound `--changed` anchor-intersection) and cheap (1×/turn, not N×/edit).

## 3. Scope

**IN (cut-1 — Claude Code delivery):**
- **Agnostic verdict core:** a thin function reusing `load_decisions` +
  `run_executable_checks` to produce a structured `Verdict` — the ratified tier-1
  checks that opted into the authoring loop, with a **tri-state** per check:
  `violation` (real non-zero) / `clean` / `unavailable` (timeout or spawn failure,
  `exit_code == -1`). Only `violation` reaches the agent.
- **`authoring_time: true` opt-in** frontmatter on a decision (parsed like other
  frontmatter — body-hash-safe, no re-ratify). Only opted-in checks run in the
  interactive loop. This is both the **safety control** (§4) and the **scope** (which
  checks participate) — replacing the dropped `applies_to`. Default absent = **not** in
  the authoring loop.
- **Stop hook path** on `super-harness-hook` (new `--event stop`), **loop-safe**:
  blocks (feeds back) only when `violations AND not stop_hook_active`; otherwise allows
  the stop. One turn-end nudge, never an infinite block; the floor catches the rest.
- **Per-agent delivery seam:** `AgentAdapter.format_stop_feedback(verdict) -> str`
  takes the **structured Verdict** (not a pre-rendered string), so a third-party agent
  can choose channel/fields. Default (floor-only agents) returns `""`. Claude Code
  renders `{"decision":"block","reason": ...}`.
- **Claude Code install/uninstall** of the Stop hook. Uninstall is left as the existing
  **restore-earliest-backup** mechanism (which already removes all three super-harness
  hooks, Stop included, by restoring the pristine pre-install backup) — the plan (Task 4)
  reversed the earlier marker-strip idea because marker-strip would break the three
  existing backup round-trip tests and cannot reproduce a pristine `hooks`-free file
  without extra pruning. The pre-existing absent-settings uninstall leak is OUT of scope.
- **Bite-test** on a **transitive** `core → sensors` violation (§7).

**Designed-but-not-shipped (seam only):** adding an agent = a new `AgentAdapter`
subclass + `format_stop_feedback` override. Codex is the first follow-on (its Stop-hook
feedback semantics need the same LIVE check the PostToolUse spike got).

**Explicitly OUT (non-goals):**
- Per-edit / PostToolUse triggering (cross-review: maximizes noise, mis-attributes
  whole-graph violations, needs a relevance model). Turn-end is the trigger.
- `applies_to` path-glob relevance; a new per-file core module; `fnmatch` matching.
- Blocking merges / replacing the floor. The Stop block is a *soft* one-turn nudge; the
  merge-gate `decision check` stays the authoritative, agent-agnostic hard block.
- Tier-2 / judgment checks; running checks **not** opted into `authoring_time`.
- Fabricated fix text — the verdict carries the check's own detail + the decision-doc
  pointer only.
- Codex delivery (follow-on); a 9th canonical capability key; daemon-autonomous
  dispatch; changing WHAT the checks assert.

## 4. Safety / trust — running checks in the dev loop

A decision's `check` is arbitrary `shell=True`, trusted because it is ratify-time
hash-locked and (until now) only run at explicit `decision check` / CI. Running checks
automatically every turn in the interactive environment is a real frequency/blast-radius
shift (cross-review S3). Controls:
- **Opt-in only:** a check runs in the authoring loop **only** if its decision declares
  `authoring_time: true`. Default = never. So nothing runs in the dev loop unless a
  decision author explicitly deemed it safe + fast.
- **cut-1 = one import-linter contract** (`d-core-is-base`), read-only graph analysis.
- **Fail-open + kill switch:** honor `.harness/gate-disabled` (the existing break-glass)
  on the Stop path too; any error → allow stop.

## 5. Latency
The Stop check runs the whole import graph **once per turn** (not per edit), so the
cost is paid at a natural boundary, not on every keystroke-edit. Still: the agent waits
for the hook. So the **inner check timeout must be strictly less than the hook's outer
timeout** (`_settings_merge._TIMEOUT` = 10 s), e.g. inner 8 s, and a slow graph →
`unavailable` → silent allow (never a hard kill, never a false "you violated"). Record
the measured whole-graph latency in the bite-test; incremental/cached analysis is a
follow-on if it is too slow.

## 6. Naming
Not a `sensor` — the codebase's `Sensor` is a dispatcher-run, event-emitting lifecycle
observer (`EventWriter`, `events.jsonl`, `SensorResult.emit_events`); this is a
synchronous, on-stdout advisory. Name the module/functions `authoring_check` /
`format_stop_feedback`; document "this is not a `Sensor`; it deliberately bypasses
`SensorDispatcher` because the dispatcher is an event-emitting engine and this path
wants a synchronous verdict."

## 7. Testing & the bite-test (the experiment)
- **Unit:** verdict core — ratified + `authoring_time` + tier-1 filtering; tri-state
  (`unavailable` on `exit_code == -1` is NOT a violation); adapter `format_stop_feedback`
  produces the correct `decision:block` envelope for violations and `""` otherwise;
  loop-safety (`stop_hook_active` true → allow).
- **Integration:** the `--event stop` path — given a workspace with a failing opted-in
  check, returns `{"decision":"block","reason": ...}` naming the decision, exit 0; given
  a clean or `stop_hook_active` workspace, emits nothing.
- **LIVE (already done for the mechanism):** Stop hook fires under `claude -p` and
  `decision:block`+`reason` reaches the model — confirmed by the stub. Re-confirm once
  wired through the real adapter.
- **Dogfood bite-test = the H experiment, on a TRANSITIVE violation:** in a live
  self-host change, induce a `core → adapters → sensors` transitive edge (the #56
  shape), and record: (a) did the turn-end verdict name `d-core-is-base`; (b) did Claude
  self-correct before merge/human; (c) measured whole-graph latency; (d) any noise.
  A null result (ignored) is a valid, reported outcome; the floor still catches it.

## 8. Success criteria
1. A ratified, `authoring_time` tier-1 violation present when a Claude Code turn ends
   produces a deterministic, loop-safe `decision:block` advisory — naming the decision,
   carrying the check's own detail + decision-doc pointer (no fabricated fix) — that the
   agent reads on its next turn.
2. Claude Code delivery works end-to-end; the "feedback reaches the model" claim is
   LIVE-verified (stub done; re-confirm through the real adapter).
3. The verdict core is agent-agnostic and reuses `run_executable_checks`; adding an
   agent is a new `AgentAdapter.format_stop_feedback` override touching no core.
4. The CI cold-path floor is unchanged and catches everything the nudge misses,
   including when H is false.
5. The bite-test honestly reports H supported (self-correct on a transitive violation)
   or falsified, plus measured latency. Only an oversold result is a failure.

---

## REVISION history
- **Rev 1 (initial):** PostToolUse per-edit sensor with an `applies_to` relevance model,
  a new core module, and a string-shaped adapter seam.
- **Rev 2 (post cross-review, 2026-07-01):** four cross-reviews → stub-first + per-turn
  Stop hook. The stub validated H, so this document was rewritten to the simpler shape:
  reuse `decision check`, drop `applies_to`/relevance/new-core-module, tri-state verdict,
  inner<outer timeout, uninstall kept as restore-earliest (marker-strip reversed in the
  plan — see §3), adapter-takes-Verdict, `authoring_time`
  opt-in as the safety+scope control, transitive-violation bite-test, non-`sensor` naming.
  Convergent correctness fixes from all four reviews are folded into §3–§7.
