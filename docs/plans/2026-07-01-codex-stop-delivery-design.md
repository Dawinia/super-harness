# Codex Stop delivery (authoring-time conformance, cut-2) — design

> Extend cut-1's Claude Code turn-end conformance feedback to **Codex**, the second
> agent — and in doing so, factor the Stop path along its true responsibility boundary
> instead of the one cut-1 left half-drawn. The agnostic verdict core is unchanged; the
> Stop **orchestration** becomes genuinely agent-agnostic, and each adapter owns its
> **full** Stop protocol (re-entrancy guard + feedback rendering). This is the first real
> test of the `format_stop_feedback` seam's generality — and it passes cleanly.
>
> Written 2026-07-01, after PR#58 (`767f65a`). Load-bearing behavior is **spiked, not
> assumed**: see `private/research/2026-07-01-codex-stop-spike.md`
> (codex-cli 0.142.2, gpt-5.5, LIVE `codex exec`). Companion to cut-1's design
> `2026-07-01-authoring-time-conformance-sensor-design.md`.

## 1. Why

Two goals, both concrete (not "open a new dimension"):

1. **Complete a shipped-but-half mechanism.** cut-1 declared Codex "a de-risked
   follow-on." The portability thesis ("not bound to Claude Code; CC + Codex in
   parallel") is only *claimed* until a second agent actually rides the same seam.
2. **Test the seam's generality — honestly.** `AgentAdapter.format_stop_feedback` was
   designed for a third party to override. Wiring the second agent is the first evidence
   for or against "a third party can contribute an agent on this seam." A clean fit
   validates the seam; a messy one teaches where it was wrong. Either is a real signal,
   reported as-is.

**Non-inflation note (honest framing):** strict value-bleed is still counted 1 (#45).
This cut completes and generalizes; it is not a new governed dimension, and the field
has not yet produced an unrelated-PR live tripwire. That background stands.

## 2. Spike result — the premise changed

cut-1's follow-on note assumed Codex might only have `PostToolUse` (post-edit) and worried
about per-edit noise. **The spike refutes that premise.** Empirically (see the spike log):

| Question | Answer |
|---|---|
| Does `Stop` fire under non-interactive `codex exec`? | **YES** (tool + tool-less turns) |
| Re-entrancy / loop-guard field? | **YES — `stop_hook_active`**, identical name & semantics to Claude |
| Does `{"decision":"block","reason"}` continue under exec? | **YES** — model runs another turn, edit stands |
| Which channel reaches the model? | **`reason` ONLY.** Adding `systemMessage`/`additionalContext` → Codex reports "Stop Failed", no continuation |

**Consequence:** Codex's Stop protocol is **byte-identical** to Claude Code's — because
Codex deliberately cloned the Claude hook interface (`adapters/agent/codex.py` docstring).
So the Codex delivery is a **turn-end mirror** of cut-1, not a per-edit variant. The
per-edit noise problem that sank cut-1 rev-1 does not exist here.

**One spike anomaly, not load-bearing:** a very first invocation with a *Stop-only*
`hooks.json` did not fire; not reproduced across the 3 subsequent runs (full event set).
The product installs the full event set, so this is treated as a first-touch fluke — but
the LIVE step (§7 / Task 9) explicitly watches for a first-invocation non-fire and records
it rather than assuming it away.

## 3. Shape

```
Codex agent finishes a turn
      │  (Stop hook — stdin carries stop_hook_active + last_assistant_message)
      ▼
super-harness-hook --agent codex --event stop
      │
      ▼
[AGNOSTIC ORCHESTRATOR]  _run_stop(adapter)
   find root → kill-switch → adapter.stop_should_check(payload)?
                                    │ no → allow stop (exit 0)
                                    ▼ yes
   run_authoring_check(root) → Verdict           (unchanged from cut-1)
                                    │
                                    ▼
   adapter.format_stop_feedback(verdict)
                                    │
                                    ▼
   {"decision":"block","reason": advisory}  →  stdout  (reason ONLY — spike §Q4)
      │
      ▼
Codex continues → self-corrects; edit stands; next Stop sees stop_hook_active=true → allow
```

## 4. Architecture — factor by responsibility, not by "they happen to match"

The invariant pipeline for turn-end conformance feedback:

```
agent turn-end hook → [re-entrancy?] → [compute verdict] → [render to agent channel] → [deliver via agent I/O]
```

- **Agnostic:** verdict computation, kill-switch, root-finding, fail-open policy, the
  advisory prose (`_render_advisory`).
- **Per-agent:** the turn-end event itself, the **re-entrancy signal field**, the
  **render protocol**, the **I/O convention**.

**What cut-1 left half-drawn (fixed here, in-scope):** cut-1 put *rendering* on the
adapter (`format_stop_feedback`) but hard-coded the *re-entrancy guard*
(`stop_hook_active`) inside `hook_entry._run_claude_code_stop`. Those are two halves of
the **same agent-specific Stop protocol**; splitting them across adapter + hook_entry
bakes a Claude-ism into what should be the agnostic runner. Serving the second agent
correctly *requires* moving the guard onto the adapter — this is intrinsic to doing the
generalization right, not scope creep.

### 4.1 The decomposition

1. **`hook_entry._run_stop(adapter)` — truly agnostic orchestrator.**
   `find root → kill-switch → adapter.stop_should_check(payload)? → run_authoring_check →
   adapter.format_stop_feedback(verdict) → write stdout`, fail-open throughout. **No agent
   field name appears in it.** `main()` dispatches `--agent {claude-code,codex} --event
   stop` to it (the current Codex `sys.exit(0)` placeholder is removed). Per-agent **lazy
   import** of the concrete adapter is kept (§4.3).

2. **The adapter owns its full Stop protocol.** New contract method
   `AgentAdapter.stop_should_check(payload: dict) -> bool` (default `True`); existing
   `format_stop_feedback(verdict) -> str` unchanged. Agents whose payload carries a
   re-entrancy guard override `stop_should_check` to skip the continuation turn.

3. **Shared impl lives in a Claude-Code-hook *family* helper, not the base class.**
   `adapters/agent/_stop_protocol.py`: `is_continuation(payload)` (reads
   `stop_hook_active`) and `block_feedback(verdict)` (emits
   `{"decision":"block","reason": _render_advisory(verdict)}`). Claude Code and Codex both
   delegate to it in one line each. The base-class defaults stay **agnostic** (`""` /
   `True`) — sharing between these two adapters is a *family* fact (Codex cloned the Claude
   interface), **not** a universal truth. A third agent with a different turn-end model
   overrides the two methods with its own protocol and touches neither the family helper
   nor the orchestrator.

Why a family helper and not a base-class default: putting
`{"decision":"block","reason"}` on `AgentAdapter` would assert "this is how *all* agents
feed back," which is false (a future agent may have no turn-end hook, or a different
protocol). The helper's name and location say *why* Claude and Codex share it.

**Considered and rejected — moving `_render_advisory` into `core.authoring_check`.** The
family helper composes `AgentAdapter._render_advisory` (the agnostic Verdict→prose). One
reviewer proposed relocating that prose to core so the helper imports downward. Rejected:
(a) it violates this cut's load-bearing "adding an agent touches no core" criterion; (b)
`core.authoring_check` deliberately owns *structured* verdicts, not presentation prose —
rendering is a delivery-layer concern (SRP); (c) the helper→base call is the sanctioned
build-on-base direction, not a layering violation. The prose stays a shared static on the
base `AgentAdapter` (its single post-cut consumer is the family helper).

### 4.2 Why this contrasts with (and validates against) the PreToolUse path

The PreToolUse path already has **per-agent shims** (`_run_claude_code_shim` exit 2 /
`_run_codex_shim` stdout `permissionDecision` / `_run_positional` exit 1) because those
agents' block-signalling did **not** converge. The Stop path **did** converge (both:
stdout JSON, exit 0). So one agnostic Stop runner + adapter-supplied guard/render is
correct *here*, while keeping separate PreToolUse shims is correct *there*. The
architecture reflects which protocol converged and which did not — it is not a blanket
"share everything."

### 4.3 Hot-path isolation — why NOT registry-driven dispatch

A name→adapter registry exists (`adapters/registry.get_builtin`), but it **eagerly
imports all adapters + yaml + the plugin loader**. `hook_entry` is the
latency/robustness-critical gate entrypoint — it runs on every tool call and every
turn-end, imports only `core.active_change` + `core.paths` at module scope, and
lazily imports the single needed adapter inside each branch. Routing dispatch through the
registry would pull the whole adapter graph onto the hot path (cold-start cost) and widen
the failure surface (a syntax error in *any* adapter would break the gate). The
per-agent lazy branch is the right pattern for this entrypoint; the registry belongs to
`cli/*` and `verification_runner`, which are not the per-call path.

## 5. Scope

**IN (cut-2):**
- `_run_stop(adapter)` agnostic orchestrator + `main()` dispatch for `--agent codex
  --event stop`; remove the Codex `sys.exit(0)` placeholder. Refactor
  `_run_claude_code_stop` into this shape (no behavior change for Claude).
- `AgentAdapter.stop_should_check(payload)` contract method (default `True`); move the
  `stop_hook_active` guard out of `hook_entry` onto the adapters.
- `adapters/agent/_stop_protocol.py` family helper; Claude Code + Codex `format_stop_feedback`
  and `stop_should_check` delegate to it.
- Codex Stop hook install into `.codex/hooks.json` via the existing `merge_stop_hook`,
  **passing an explicit Codex marker** (`--agent codex --event stop`) — `merge_stop_hook`
  defaults to the *Claude* marker (`_STOP_OURS_MARKER`), so a default call would make the
  Codex install non-idempotent (reinstall appends a 2nd Stop entry → two JSON objects on
  stdout → Codex "Stop Failed", feature silently lost). Mirrors the existing Codex
  PreToolUse `marker=_CODEX_MARKER` pattern. Snapshot-rollback like the existing Codex
  hooks. **Uninstall is best-effort** via the existing restore-earliest-backup path (works
  when a pre-install backup exists; the absent-file fresh-install uninstall leak is
  pre-existing and OUT of scope, matching cut-1's stance).
- Codex `agents_md_subsection`: add the Stop authoring-check paragraph + the trust caveat
  (`/hooks`-trust required before active — same as the PreToolUse gate).
- **Capability corrections (folded in):** add a turn-end/stop capability key to the
  canonical set for **both** Claude Code and Codex (cut-1 shipped the Claude Stop hook
  without describing it — this fixes that descriptive gap and updates the "canonical keys"
  contract); flip Codex `post_tool_use_hook` `False → True` (the spike proved Codex
  PostToolUse fires; the flag describes agent capability).
- **LIVE re-confirmation** through the real Codex adapter (not the throwaway spike hook):
  Stop fires under `codex exec`, `reason` reaches the model, `stop_hook_active` breaks the
  loop.

**Explicitly OUT (non-goals):**
- Registry-driven hook_entry dispatch (§4.3 — hot-path isolation).
- `SubagentStop` delivery (parity with cut-1; deferred, logged in OPEN-ITEMS).
- `systemMessage` / `hookSpecificOutput.additionalContext` channels on Stop (spike: they
  break Codex's Stop → "Stop Failed", no continuation). `reason` is the only channel.
- HG-ENV-LEAK (`verification_runner` scrubbing `SUPER_HARNESS_*` before test subprocesses)
  — a separate cut; no logical relationship to Stop delivery.
- Changing WHAT the checks assert; daemon-autonomous dispatch; per-edit triggering.

## 6. Safety / trust

- **Fail-open + kill switch** on the Codex Stop path exactly as on Claude's: any error /
  malformed stdin / no harness / `.harness/gate-disabled` → allow the stop. The agnostic
  orchestrator owns this once, for both agents.
- **Trust caveat (honest limitation):** the Codex Stop hook, like the existing Codex
  PreToolUse gate, is **INACTIVE until a human runs `/hooks`** to trust it (Codex keys
  trust to the hook's hash). Documented in the AGENTS.md subsection. This is a
  pre-existing Codex property, not new debt. (The spike used
  `--dangerously-bypass-hook-trust`, which is a spike-only automation flag, never wired
  into the product.)
- **Advisory carries no self-bypass escape hatch** (aligned with cut-1 + PR#51/#52): the
  shared `_render_advisory` already says "stop and surface to the human — do not proceed
  on your own authority." Delivered verbatim on Codex; a negative test locks it.

## 7. Testing

- **Unit:** `_stop_protocol.is_continuation` (true only when `stop_hook_active is True`)
  and `block_feedback` (correct `decision:block` envelope on violations, `""` when clean);
  `CodexAdapter.stop_should_check` / `format_stop_feedback` delegate correctly; Codex Stop
  hook install writes the expected `.codex/hooks.json` entry + snapshot-rollback on
  failure; capability keys present/correct.
- **Agnostic-orchestrator:** `_run_stop` given a stub adapter — root/kill-switch/fail-open
  branches; skips the check when `stop_should_check` is False; emits the adapter's output
  when a violation is present. No agent field names in the orchestrator (guarded by the
  test using a stub adapter with a *different* guard field).
- **LIVE (the seam-generality evidence):** through the real `CodexAdapter` + `codex exec`,
  induce a ratified `authoring_time` violation at turn end and record: (a) Stop fires; (b)
  the advisory naming the decision reaches the model via `reason`; (c) `stop_hook_active`
  stops the second nudge; (d) latency; (e) whether Codex self-corrects (a null result is a
  valid, reported outcome — the floor still catches it).

## 8. Success criteria

1. A ratified, `authoring_time` tier-1 violation present when a **Codex** turn ends
   produces a deterministic, loop-safe `decision:block` advisory (naming the decision,
   check's own detail + decision-doc pointer, no fabricated fix) that the agent reads on
   its next turn — LIVE-verified through the real adapter.
2. `hook_entry._run_stop` contains **no agent-specific field names**; both Claude Code and
   Codex ride it; the Claude behavior is unchanged (regression-tested).
3. Adding the second agent touched **no core** and no new orchestration branch beyond
   dispatch — the seam's generality is demonstrated (or, if it wasn't clean, the friction
   is documented as the finding).
4. Capability descriptions are accurate for both agents (turn-end key present; Codex
   `post_tool_use_hook` corrected).
5. The CI cold-path floor is unchanged.

---

## Context / provenance
- Spike evidence: `private/research/2026-07-01-codex-stop-spike.md` (all §2 facts).
- cut-1 design: `2026-07-01-authoring-time-conformance-sensor-design.md`;
  memory `project-authoring-feedback-cut1`.
- Codex hooks: OpenAI Codex developer docs (`developers.openai.com/codex/hooks`) — used
  to locate the event set; all behavior under `codex exec` verified by spike, not doc.
- Escape-hatch stance: PR#51/#52, memory `project-gate-escape-hatch-self-bypass`.
