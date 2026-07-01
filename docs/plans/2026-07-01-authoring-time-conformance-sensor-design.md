# Authoring-time decision-conformance sensor — design

> Open the first G-FEEDFORWARD cut: after an agent edits a file, run the relevant
> tier-1 mechanical decision check for that file and feed the deterministic result
> **back to the agent as advice (non-blocking)** so it can self-correct *before* the
> lifecycle / merge gate or a human sees it. Attacks the research-named
> "authoring-time sensor" gap and the 5–10×/session review whack-a-mole.
> **First cut ships Claude Code delivery only**, but designs the agent-agnostic
> check core + per-agent delivery seam so other agents (Codex first) slot in as
> follow-on cuts. Codex delivery is gated on a LIVE spike (§6) and is floor-only
> until that spike proves the loop.
>
> This cut is framed as a **falsifiable experiment**, not a guaranteed win — see §1.
> Written 2026-07-01; revised after cross-review (Claude + Codex independent reviewers
> converged on the findings folded in below). See
> `private/research/2026-07-01-agent-conformance-pain.md` and memory
> `project-agent-conformance-research`.

## REVISION 2 — architecture cross-review outcome (2026-07-01)

Four independent cross-reviews (Claude + Codex × mechanical + architecture) converged
that the plan below should **not** be built as-is. Two decisions were taken (user-approved)
that reshape the cut; the durable rewrite of §2–§9 + the plan is **deferred until the H
stub (below) shows signal** — this is the stub-first discipline itself.

**Decision A — stub-first sequencing.** §1 admits H may be false (net value ≈ 0). Building
the durable, least-reversible parts first (a new `applies_to` Decision-schema field + a full
core module + the adapter seam) before testing H would strand that schema on a null result.
So: **validate H with a throwaway stub first** (a ~20-line hook that runs the existing check
for the known `core → sensors` violation and feeds back the advisory), then build only if
Claude actually self-corrects.

**Decision B — per-turn (Stop hook), not per-edit (PostToolUse).** Per-edit maximizes the
intermediate-state noise §5 frets about and mis-attributes whole-graph violations to whichever
file was just edited. A `Stop` / `SubagentStop` hook that runs the standard `decision check`
**once when the agent finishes its turn** is simpler (reuse `decision check` almost verbatim —
**no `applies_to`, no new relevance model, no new core module**), sound (full graph, not the
unsound `--changed` anchor-intersection), 1× cost, and noise-free. Trade-off: feedback at
turn-end vs per-edit immediacy — still before the human / merge gate. (The Codex PostToolUse
spike still stands as proof Codex hooks fire under `exec`; the Stop-hook feedback semantics
must be LIVE-verified before the rewrite.)

**Convergent fixes to fold into the rewrite (all four reviews):** (1) tri-state result
`violation | clean | unavailable` — only a real non-zero check feeds back; timeout/spawn
(`exit_code == -1`)/parse → silent (the current plan reports a timeout as "you violated X").
(2) Timeout budget: inner check timeout **strictly less** than the hook's outer timeout
(`_TIMEOUT = 10`), and a sub-second-to-few-second budget (a 10–15 s synchronous stall is worse
than no sensor). (3) Uninstall must **strip by marker**, not restore-earliest-backup (which
leaks the PreToolUse hook when settings were absent) — and test all three markers gone.
(4) Adapter seam takes the structured `Verdict`, not a pre-rendered string, so third parties
can choose channel/fields (the contribution requirement). (5) Rendering lives in the adapter;
core returns a structured verdict only. (6) Don't name it `sensor` (collides with the `Sensor`
+ dispatcher framework) — `authoring_feedback` / `conformance_check`; document "not a `Sensor`".
(7) Security/trust: running ratified `shell=True` checks automatically every turn in the dev
loop is a frequency/blast-radius shift — restrict the authoring path to declared-safe check
kinds (import-linter contracts) for cut-1. (8) YAGNI: drop `by_id`; reuse `_ensure_event_list`;
`fnmatch` is not path-aware (moot if `applies_to` is dropped per Decision B).

Verified clean by review: a `core`-resident conformance module importing only `core` does
**not** violate `core-is-base`; the layering split is sound.

---

## 1. Why — and the honest hypothesis this cut is testing

The sharpest, most-reproduced complaint about AI coding agents is not "the rules
aren't written down" — it is **"the rules are written down, the agent reads them
into context, and it violates them anyway."** Redundant authoring, memory files,
and full spec-driven scaffolding all fail the same way: advisory text has no
mechanical enforcement (research §A/§B). Today the only in-the-loop enforcement is a
human reviewing and correcting — a 5–10×/session whack-a-mole that "defeats the
purpose." Deterministic tooling that *does* bite (import-linter, ArchUnit) fires at
**CI/test time — after the code is written** (research §D/§E). The gap named by the
harness-engineering discourse is an **authoring-time feedback loop** that lets the
agent self-correct before a human/CI does (research §E).

**The uncomfortable self-reference (cross-review S5).** This cut delivers
*non-blocking* feedback — which is still advisory text, just injected into the
agent's `additionalContext` instead of `CLAUDE.md`. The research's own open question
#1 is that pre-write feedback reducing the whack-a-mole is **unproven**. So we must
state the hypothesis plainly and hold it falsifiable:

> **H:** Deterministic, precise, just-in-time feedback that names the *specific*
> decision the agent *just* violated changes behaviour more than static advisory
> prose does — not because it is louder, but because it is specific, contextual, and
> arrives at the moment of the violation. The existing floor (merge-gate check)
> stays underneath regardless.

**H may be false.** If the agent ignores the authoring-time advisory the same way it
ignores CLAUDE.md, this cut delivers only token noise on top of the existing floor —
net value ≈ 0 (or negative). That is a real possible outcome. **The §7 bite-test is
the experiment that decides it, and a null result will be reported honestly, not
buried.** This honesty is the point: the project's discipline is "don't conflate
ritual with value" — so we build the smallest thing that can actually test H.

## 2. Scope

**IN (first cut — Claude Code delivery only):**
- **Agnostic check core.** Given the file the agent just changed, resolve which
  tier-1 decision check(s) are *relevant to that file* (see §3a — this is NOT the
  existing `--changed` anchor-intersection, which is unsound for whole-graph
  contracts), run them, and produce a structured verdict. No agent knowledge.
- **PostToolUse hook path** on the `super-harness-hook` binary (new event mode),
  wired strictly **non-blocking**: it never blocks the edit (already applied); it
  returns advisory feedback iff there is a violation, and fails **open** (no feedback)
  on timeout / any error / missing file.
- **Claude Code delivery.** Adapter formats the verdict into
  `hookSpecificOutput.additionalContext`; Claude Code adapter's `install_hooks`
  registers a PostToolUse entry; matching uninstall cleanup.
- **Bite-test (dogfood)** proving (or falsifying) H on `d-core-is-base` with Claude Code.

**Designed-but-not-shipped (the agnostic seam — architecture only, no code beyond the ABC):**
- The check core and the adapter delivery method are defined so a new agent =
  a new `AgentAdapter` subclass + delivery method, touching no core. Codex is the
  first intended follow-on (§6).

**Explicitly OUT (recorded non-goals):**
- **Codex (and any other agent) delivery in this cut.** The §6 LIVE spike **PASSED**
  (2026-07-01), so Codex is now a **de-risked, immediate follow-on cut** — not
  floor-only, but still not folded into cut-1 (keeping cut-1 the smallest thing that
  tests H; cross-review C1/scope). Its delivery ships right after cut-1.
- **A 9th canonical capability key.** Not added. Claude Code already has both the post
  hook and a feedback channel, so no new capability is needed to gate cut-1 install.
  The real distinction the research surfaced — *has-hook ≠ can-feed-back* (Copilot has
  a postToolUse hook but cannot inject context back) — is deferred to the cut that adds
  a feedback-less agent, and will go through the ABC's own `x_<vendor>_*` extension
  path (or an explicit, acknowledged contract change), not a silent canonical addition.
- **Blocking.** The merge-gate `decision check` stays the authoritative hard block;
  the sensor only reduces how often it fires.
- **Tier-2 / judgment-class decisions.** Stay reviewable. A deterministic per-edit
  sensor must not depend on LLM judgment (noisy, slow, violates "harness never spawns agent").
- **Debounce / batching.** Not built — noise is measurable; measure in the bite-test first.
- **Fabricated fix text.** The verdict carries only what the mechanism actually
  produces (§3b) plus a pointer to the decision doc — no invented `suggested_fix`.
- **Daemon-autonomous dispatch** (v0.2); **changing WHAT the checks assert** (decision
  records / `.importlinter` unchanged — this cut changes *when/how* the verdict reaches the agent).

## 3. Architecture — three layers, all on existing seams

```
agent edits file
      │  (PostToolUse hook — carries the changed file path in tool_input)
      ▼
super-harness-hook --event post-tool-use --agent claude-code
      │  a) resolve changed file → relevant tier-1 contract(s)
      │  b) run check → verdict
      ▼
[AGNOSTIC CHECK CORE]  relevant tier-1 check → verdict  ── shares check machinery with ──▶ merge gate
      │  verdict has violations?
      ▼
AgentAdapter.format_post_edit_feedback(verdict)   ── per-agent delivery (Claude Code in cut-1)
      │
      ▼  Claude Code: hookSpecificOutput.additionalContext (read next turn)
agent reads feedback → self-corrects (H under test)
      ⋮  (whatever the sensor misses — or if H is false)
CI cold-path floor: merge-gate `decision check` — authoritative, agent-agnostic  ← never removed
```

### 3a. Changed-file → relevant contract (the B1 fix)
The existing `decision check --changed` scopes by intersecting git-changed files with
a decision's sparse `@decision:` code anchors (`core/check_runner.py:select_changed`,
which its own comment flags as unsound for this purpose). `d-core-is-base` has a
single anchor (`core/__init__.py:8`) but its check is a **whole-graph** import-linter
contract over all of `super_harness.core`. So anchor-intersection would miss an edit
to `core/foo.py` — the sensor would silently produce nothing, and the §7 bite-test
would not fire.

The sensor therefore needs its **own relevance resolution**: a changed file is
relevant to a tier-1 contract when the file's module falls within that contract's
declared scope (for import-linter, its `source_modules` / `forbidden_modules`). For
cut-1's single contract: **an edit to any file under `src/super_harness/core/` runs
`d-core-is-base`.** This is a small, principled resolver (contract-scope membership),
not the sparse-anchor intersection. Generalising it to arbitrary contracts is noted
for the plan; cut-1 needs only the core-scope rule.

### 3b. The verdict is what the mechanism actually produces (the B2 fix)
The check machinery yields `CheckFailure(id, exit_code, detail)` where `detail` is the
check's stderr tail (for import-linter, the offending import line). There is **no**
`reason` schema and **no** `suggested_fix` field anywhere in the decision records or
the runner. So the verdict is honestly:

```
{ violations: [ { decision_id, detail, decision_doc_path } ] }
```

Feedback to the agent = the decision id + the check's own violation detail + a pointer
to `docs/decisions/<id>.md` (which contains the human-readable rule + counterexample).
No fabricated fix text. If a curated one-line fix hint is wanted later, it is an
**explicit new optional field on the decision record** (small additive work) — a plan
decision, not a "free reuse."

### 3c. Layering
- **Agnostic core** (relevance resolver + check runner + verdict) is agent-independent
  and shares the check machinery with the merge gate — one check, two trigger times.
- **Per-agent delivery** is the only agent-aware part: one `AgentAdapter` method. This
  is the contribution seam; the ABC, registry, and degraded-mode docs already exist.
- **CI cold-path floor** is unchanged and remains the guarantee for *every* agent
  (including feedback-less ones). The sensor is a real-time assist on top; the floor is
  the enforcement (`project-positioning-layer-not-replacement`).

## 4. Hook wiring & control flow
- Extend `daemon/hook_entry.py` with a **post-tool-use** event path parallel to the
  existing pre-tool-use path. **The changed file comes from the hook payload's
  `tool_input.file_path`, NOT from `git diff`** (git diff sees the whole dirty tree,
  not this tool call). `MultiEdit` = one file per call; `NotebookEdit` / any multi-file
  tool is handled per its payload or skipped in cut-1 (noted).
- Claude Code adapter's `install_hooks` additionally registers a **PostToolUse** entry
  (matcher `Edit|Write|MultiEdit`) → `super-harness-hook --event post-tool-use --agent claude-code`.
- **Non-blocking contract:** the post path always resolves to "continue"; it attaches
  advisory feedback iff there is a violation and attaches nothing on timeout/error
  (**fail-open**). It must never emit a blocking exit.
- **Invocation path:** call the relevance-resolver + check directly on the hook path
  with a tight timeout — **not** through `SensorDispatcher` (its 300s timeout + thread
  pool is far too heavy for a per-edit path). It is a "sensor" in the Böckeler sense but
  deliberately does not register as a code `Sensor`; this naming/architecture split is
  called out so it is not mistaken for the existing dispatcher path.

## 5. Latency — a feasibility constraint, not an open question
`d-core-is-base`'s check is whole-graph import-linter (`check_runner.py` default
timeout 30s). On the PostToolUse path the agent waits for the hook to return, so a
whole-graph run per edit means: **the larger the repo, the slower the graph, the more
likely a timeout → fail-open → no feedback** — the sensor's reliability is inversely
correlated with the codebase size that needs it most. Cut-1 therefore:
- Sets an explicit **bounded latency budget** for the post path (fail-open past it).
- Runs the whole-graph check for cut-1's single contract and **records the measured
  latency** in the bite-test as a first-class result.
- Names the scaling answer as a **follow-on** (incremental/cached import analysis, or
  a faster relevance pre-filter), and honestly marks cut-1 as "validated on this
  repo's graph size" rather than "works at any scale."

## 6. Codex LIVE-verify spike — RESULT: PASSED (2026-07-01)
Codex's adapter declared `post_tool_use_hook: False` with a coverage caveat, and
OpenAI's docs are silent on whether hooks run under `codex exec` — a load-bearing
uncertainty we **LIVE-verified rather than assumed**.

**Spike (scratch dir, reversible, `codex-cli 0.142.2`):** registered a project-level
`.codex/hooks.json` PostToolUse hook → a logging script emitting a unique marker; ran
`codex exec --dangerously-bypass-hook-trust --sandbox workspace-write` making an
`apply_patch` edit. Result, from the real run:
- ✅ **PostToolUse fires on `apply_patch`** (native diff edit) under non-interactive
  `codex exec` — logged payload shows `tool_name: apply_patch` + `tool_response:
  Success. Updated hello.py`. It also fired on `Bash` tools in the same run, so both the
  native-edit and shell paths trigger it.
- ✅ **Feedback reaches the model** — the hook's unique marker was echoed verbatim in
  Codex's final agent message, and the file was edited. So the loop closes.
- Config detail: event name is **PascalCase `PostToolUse`** (same shape
  `_settings_merge.py` writes; the snake_case in `config.toml [hooks.state]` is Codex's
  internal trust-key, not the config key). stdin payload carries `tool_input`,
  `tool_response`, `tool_use_id`, `cwd`, etc. — the post hook can see whether the edit
  succeeded.

**Consequences for the plan:**
- The Codex adapter's `post_tool_use_hook: False` is **factually wrong for exec** and
  must be flipped to `True` (with a test) in the Codex follow-on cut.
- **Open micro-uncertainty (honest):** the spike put the marker in *three* feedback
  fields at once (`decision:block`+`reason`, `systemMessage`, `additionalContext`), so
  it proves feedback *arrives* but does **not** isolate which channel delivered it. The
  Codex cut must pin the channel with three distinct markers (cheap) before relying on
  `additionalContext` specifically.
- This result also bears on the standing "does `codex exec` run hooks" open item
  (memory `project-gate-escape-hatch-self-bypass`): at least PostToolUse **does** run
  under exec on this Codex version — that open item needs reconciling.
- The rejected "route via a shell-tool path" fallback is now moot (real PostToolUse
  works), but recorded: a pre-exec shell intercept cannot observe the applied patch, so
  it was never an equivalent to authoring-time feedback.

## 7. Testing & the value-bleed proof (the experiment)
- **Unit:** relevance resolver maps a `core/` file to `d-core-is-base` and a non-core
  file to nothing; verdict shape matches §3b; fail-open on timeout/missing file; Claude
  adapter formats correct `additionalContext`.
- **Integration:** the PostToolUse path, given a changed `core/` file importing
  `sensors/`, returns non-blocking advisory naming `d-core-is-base` + detail + doc path.
- **Load-bearing LIVE spikes (must actually run, not assume):** (i) Claude Code
  PostToolUse `additionalContext` genuinely reaches the model on its next turn in
  headless/real use — `post_tool_use_hook: True` only means the hook exists, not that
  feedback lands; (ii) the Codex spike (§6); (iii) measured whole-graph latency (§5).
- **Dogfood bite-test = the H experiment:** in a live self-host change, have Claude
  Code edit `core/` to import `sensors/` (a genuine `d-core-is-base` violation) and
  record whether the agent, on receiving the authoring-time advisory, **self-corrects
  before** the merge gate / a human — and how much intermediate-state noise occurred.
  **A null result (agent ignores it) is a valid, reportable outcome that falsifies H
  for this cut**; the floor still catches the violation regardless.

## 8. Success criteria
1. A tier-1 violation introduced by a Claude Code edit produces a deterministic,
   non-blocking advisory — naming the decision, carrying the check's own violation
   detail, and pointing at the decision doc — delivered via `additionalContext` at
   authoring time. (No fabricated fix text; §3b.)
2. Claude Code delivery works end-to-end and the feedback-lands claim is **LIVE-verified**
   (§7), not asserted from capability flags.
3. The check core (relevance resolver + runner + verdict) is agent-agnostic and shares
   the check machinery with the merge gate; adding a new agent is a new `AgentAdapter`
   subclass + delivery method touching no core.
4. The CI cold-path floor is unchanged and still catches everything the sensor misses —
   including the case where **H turns out false**.
5. The bite-test reports, honestly, whether authoring-time advisory changed the outcome
   (H supported) or not (H falsified) — plus the measured latency. Either way is a valid
   deliverable; only a hidden/oversold result is a failure.

## 9. Non-goals recap for the plan (so nothing silently creeps back in)
Codex real delivery (spike-gated), any 9th canonical capability key, blocking,
tier-2/judgment checks, debounce/batching, fabricated fix text, daemon-autonomous
dispatch, general contributor-seam productisation beyond the ABC method, and
"works at any repo scale" latency claims are all OUT of this cut.
