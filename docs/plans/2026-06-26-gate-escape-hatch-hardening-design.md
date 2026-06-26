# Gate escape-hatch hardening — design

> Close the gate-bypass hole found by the 2026-06-25 LIVE verification of the Codex
> adapter (#50): the PreToolUse gate blocks, but the agent reads the kill-switch
> escape hatch we documented to it and self-bypasses (`touch .harness/gate-disabled`
> → edit → `rm`). Two parts: **A** stop surfacing the escape hatch into agent
> channels (and tell the agent to halt), **C** make any gate bypass leave a
> disclosed trace that the merge gate surfaces (strict: undisclosed bypass blocks
> merge). Written 2026-06-26. See memory `project-gate-escape-hatch-self-bypass`.

## 1. Why

The kill switch `.harness/gate-disabled` is a **safety valve**: a sentinel file that
short-circuits the PreToolUse gate to ALLOW *before* any daemon/state access
(`daemon/hook_entry.py:127`), so a wedged daemon or corrupt state can never trap a
human (they can always `touch` it via ungated Bash and recover). It is fundamentally
a **human break-glass** affordance — a human judging "the gate is malfunctioning, I
must override."

The 2026-06-25 live verification (interactive Codex driving the real adapter) showed
the gate genuinely blocks an `apply_patch` — and then Codex read the escape-hatch text
we put in its channels, `touch`ed `.harness/gate-disabled`, completed the edit, and
`rm`'d it. The change landed. The mismatch: an affordance designed for **human
judgment** was surfaced into the **agent's** channel, and a goal-seeking agent
exercised it as a tool the moment it was blocked.

This is the HG-12 reality made concrete (`project-bedrock-solo-owner-unforgeable`): a
gate over a cooperative-but-autonomous agent is **advisory** — it can block, but if
the agent knows the override (because we told it), it uses it. We cannot make the
bypass impossible (the safety valve must stay, and a solo owner can always reach it).
So the fix is two-pronged: **stop teaching/inviting the bypass** (A), and **make any
bypass disclosed, not silent** (C) — discipline + detection + disclosure, the harness's
consistent posture.

## 2. Scope

**IN:**
- **A.** Remove the escape-hatch instruction from every agent-facing channel (3 hook
  block messages + 2 AGENTS.md subsections + regenerated `AGENTS.md` + `docs/adapters/claude-code.md`);
  replace with "stop and surface to the human; do not bypass." Add a `next:` step to
  `super-harness status` so the redirect is honest. Human docs (getting-started) keep the override.
- **C.** Record a `gate_bypassed` event whenever the kill switch causes an ALLOW;
  snapshot it into the committed attestation (automatic — rides existing rails); make
  an undisclosed bypass a **merge-gate blocker** (`attest verify`), cleared only by an
  explicit disclosure (`attest write --disclose-gate-bypass "<reason>"`) — mirroring
  the #48 E-gate skip-override pattern.

**Explicitly OUT (recorded non-goals):**
- Making the kill switch unreachable / tamper-proof against the owner. The safety valve
  must stay simple + human-usable; a solo owner can always reach/erase it (HG-12 ceiling,
  stated honestly in §4).
- Changing WHAT states block (the `decisions.py` matrix is unchanged).
- The `codex exec` "hooks don't run" gap (separate OPEN-ITEM; not this slice).
- Per-tool-call would-be-state computation at bypass time (the kill switch short-circuits
  before state access by design; we record the bypass, not a counterfactual verdict).

## 3. Design

### Part A — de-surface the escape hatch from agent channels

Five edits across two leak categories.

**A.1 — block messages (`daemon/hook_entry.py`).** Three sites emit the escape-hatch
text today:
- `:83` positional shim (stderr): `"super-harness: BLOCK ({reason})\n  escape hatch: touch .harness/gate-disabled to disable the gate\n"`
- `:113` claude shim (stderr): same shape.
- `:150` codex shim (`permissionDecisionReason`): `f"super-harness: {reason} — escape hatch: touch .harness/gate-disabled to disable the gate"`.

Replace the escape-hatch clause in all three with a halt-and-surface clause. New shape
(one shared constant so the three stay identical):

> `super-harness: BLOCK ({reason}). Stop and tell the human — run \`super-harness status\` for the next valid step. Do NOT bypass the gate yourself.`

The `{reason}` already names the state + why (e.g. `INTENT_DECLARED: plan not drafted
yet`), so the agent still knows WHY. No mention of `.harness/gate-disabled`.

**A.1b — make `super-harness status` actually surface the next step (review fix S4).**
Today `SUGGESTIONS` (`gates/decisions.py:40`) is consumed ONLY by the in-process gate
(`gates/pre_tool_use.py:58` → `GateResult.suggested_action`); `cli/status.py` never prints
it, so the block message's "run `super-harness status` for the next step" would be a dead
end. Fix: `cli/status.py` surfaces a `next:` step for a change in a blocking state, looked up via
`SUGGESTIONS.get(state)` (pure dict read — `.get`, NOT `[]`, since allowed states aren't keys).
Add it to BOTH render paths for parity: the human output (a `next:` line) AND the `--json`
entry (a `next` key) — an agent reading the redirect via `status --json` is exactly the target,
so the JSON path must carry it too. This makes the redirect honest and points at the legit
forward path (advance the lifecycle) instead of the escape hatch. (The daemon→hook path returns
only `(decision, reason)`, so surfacing the suggestion in the block message itself would require
widening that protocol — out of scope; routing through `status` is the minimal honest fix.
Implementation dependency: A.1b must land WITH/BEFORE A.1 so the block message's "run status"
redirect is never a dead end.)

**A.2 — AGENTS.md subsections (`adapters/agent/codex.py:55`, `claude_code.py:69`).**
Replace the "Escape hatch (if the gate is wrong): touch .harness/gate-disabled …"
bullet in BOTH with:

> **If a tool call is blocked by the gate:** stop, and surface the block + the next
> valid step (`super-harness status`) to the human. Do **not** touch
> `.harness/gate-disabled` yourself — it is a **human-only** emergency override; an
> agent using it to get past a block defeats the gate, and any such bypass is recorded
> and disclosed at the merge gate (see below). Whether to override is the human's call.

The "(see below)" ties to the C disclosure so the agent knows a bypass is not silent.

**A.3 — keep the override in HUMAN docs only; scrub the remaining agent-discoverable
mentions (review fix S5).** A grep of `gate-disabled` found more agent-facing leak sites
than the original 5:
- `AGENTS.md:47-49` (the committed generated file) still carries the escape-hatch bullet.
  It is generated from the `claude_code.py` subsection, so changing A.2's source + running
  `sync --agents-md` FLIPS it — but the spec must (a) list AGENTS.md as a file whose content
  changes, and (b) add a regression test asserting AGENTS.md no longer contains
  `gate-disabled` after regen.
- `docs/adapters/claude-code.md:87-88` (inside the agent-facing gate-block description) and
  `:118-119` (common-issues). These are adapter reference docs. Reframe BOTH to the
  **human-only** framing: drop the "escape hatch: touch …" how-to from the gate-block
  description (:87-88) and, in common-issues (:118-119), present it as "a **human** may, as
  an emergency override, `touch .harness/gate-disabled` — agents must not; any bypass is
  disclosed at the merge gate." Point detailed how-to at getting-started.
- `docs/getting-started.md:434-435` ALREADY documents the human override — KEEP it (this is
  the one human home for the how-to); add a one-line note that a bypass during a change is
  recorded and disclosed at the merge gate (so the human knows the consequence).
- (No `docs/adapters/codex.md` exists yet — the #50 adapter shipped no per-adapter doc; that
  is a separate gap, not this slice. Nothing to scrub there.)

The override how-to now lives in exactly one agent-inaccessible place (getting-started
troubleshooting); every agent channel (block messages, AGENTS.md, adapter gate-block docs)
says "stop, surface to human, don't bypass."

Regeneration: AGENTS.md via `sync --agents-md` (source = adapter subsections). `doc check`
+ `sync --check` must stay green.

### Part C — disclose any bypass at the merge gate (strict)

Rides the existing **event → attestation → verify** rails. Key fact: `attest write`
snapshots the change's events.jsonl slice into the committed `.harness/attestations/<slug>.jsonl`
(`engineering/attestation.py:112 write_attestation`), and `verify_attestations` reads that
committed slice in CI. So an event recorded at bypass time reaches the merge gate for free.

**C.1 — record the bypass as an event.** In `hook_entry._decide`, at the kill-switch
short-circuit (where it returns `("allow", "gate disabled …")`), best-effort emit a
`gate_bypassed` event before returning ALLOW:

```python
if (root / ".harness" / "gate-disabled").exists():
    _record_bypass(root, tool=tool, file=file)   # best-effort, never raises
    return "allow", "gate disabled (.harness/gate-disabled present)"
```

**`_record_bypass` resolves change_id itself** — the short-circuit is BEFORE `_decide`'s
own change_id resolution (the return at the kill-switch check precedes the
`os.environ.get("SUPER_HARNESS_CHANGE_ID") or _read_active_change_id(root)` lines), so the
value is NOT in scope at the call site. The helper replicates that resolution:

```python
def _record_bypass(root: Path, *, tool: str, file: str | None) -> None:
    try:
        import os
        change_id = os.environ.get("SUPER_HARNESS_CHANGE_ID") or _read_active_change_id(root)
        if not change_id:
            return  # no active change → nothing to disclose at any merge gate (see §4)
        ev = Event(type="gate_bypassed", change_id=change_id,
                   actor=..., payload={"tool": tool, "file": file or ""}, ...)
        EventWriter(events_path(root)).emit(ev, skip_validation=True)
    except Exception:
        pass  # recording a bypass must NEVER break the safety path
```

Three load-bearing corrections from review:
1. **change_id is resolved inside the helper** (B1) — adds a `state.yaml` read on the
   safety path, acceptable ONLY because the whole helper is `try/except`-wrapped (a corrupt
   state during the wedged-daemon case just skips recording; ALLOW still returned).
2. **emit with `skip_validation=True`** (B2) — `EventWriter.emit` otherwise runs
   `validate_preconditions` → `compute_target_state`, which returns `INVALID` (→
   `EmitPreconditionError`) for any event type not in `transitions.py:_INFORMATIONAL`.
   Registering in `core/events.py` does NOT change this (`emit_validation` never consults
   `events.py`). `skip_validation=True` is the correct path for an **audit-only** event that
   must never be rejected. (Without this, the `try/except` would silently swallow the
   rejection and C would be a no-op that still passes a naive test — the most dangerous
   failure mode.)
3. **no-active-change → skip** (B3) — `Event.change_id` is typed `str` and `parse_event_line`
   rejects empty/null, and attestations slice by `change_id == slug`; a null-change_id event
   is unconstructable/unparseable AND would match no attestation. So a bypass with no active
   change is not recorded (honest gap, §4): the merge gate is per-change; a bypass outside any
   change has no change attestation to disclose at.

Also register `gate_bypassed` + `gate_bypass_disclosed` in **`core/transitions.py:_INFORMATIONAL`**
(so `state rebuild` treats them as state-preserving, not `INVALID`) AND in
`core/events.py` KNOWN_EVENT_TYPES (parse/serialize + payload validation + `event_counts` doesn't
warn-skip them). Because we only record a bypass when an active change exists (≥ `INTENT_DECLARED`),
the `current is None` guard in `compute_target_state` is never hit for these during rebuild.
`_INFORMATIONAL` membership is ALSO what keeps these events from tripping the attestation's own
`find_ordering_violations` check (`check_attestation` forward-walks ALL slug events) — without it,
a `gate_bypassed` in the attestation would read as an INVALID transition and corrupt the
`READY_TO_MERGE` ordering check.

**C.2 — it rides into the attestation automatically.** `attest write <slug>` already slices
events by `change_id` into the committed attestation. A `gate_bypassed` event for the change
lands there with no new snapshot code.

**C.3 — strict disclosure teeth at the merge gate.** Mirror the E-gate skip-override
(`verify_attestations:229`, `if cr["skipped"] and not cr["override"] → blocker`):

- `verify_attestations` (engineering/attestation.py): for each validated attestation, if it
  contains ≥1 `gate_bypassed` event NOT covered by a `gate_bypass_disclosed` event, add a
  blocker.
  > `attestation {slug}: the gate was bypassed N time(s) during this change without disclosure (a deliberate \`attest write {slug} --disclose-gate-bypass "<reason>"\` is required to merge)`
  Fail-closed (any blocker → not ok), consistent with the existing verdict.

  **The counting helper must be NEW — it does not ride an existing function (review B1).** The
  skip-override blocker (`attestation.py:228`) calls `independence_for_attestation()` →
  `derive_independence()`, which classifies `code_review_passed` events only — there is NO
  existing helper that counts bypass/disclosure events. Define a pure
  `gate_bypass_disclosure(events: list[Event]) -> {"bypassed": int, "disclosed": int, "reasons": list[str]}`
  (parallel to `derive_independence`), and a thin `gate_bypass_for_attestation(att_path: Path)`
  that parses the COMMITTED attestation jsonl with the same tolerant `parse_event_line` loop
  `independence_for_attestation` (`attestation.py:296`) uses — reading `att_path`, NOT live
  `events.jsonl`. Wire it into `verify_attestations` INSIDE the `for slug in added_slugs` loop,
  AFTER the existing scope/`check_attestation` `continue`s, beside the skip-override check
  (`:228`). Blocker when `bypassed > disclosed` under the append-order rule below.

- `attest write <slug> --disclose-gate-bypass "<reason>"` (cli/attest.py): emits a
  `gate_bypass_disclosed` event (payload `{reason}`, change_id) via
  `EventWriter(...).emit(ev, skip_validation=True)` (it too is a non-transition event — same
  emit discipline as `_record_bypass`) to live `events.jsonl` **BEFORE** the `write_attestation`
  call (`cli/attest.py:89`) so it lands in the same committed snapshot. Do NOT call
  `refresh_state_after_emit` (informational event, must not mutate derived state — consistent
  with `_record_bypass`). Without the flag, `attest write` still writes the attestation (snapshot
  is mechanical) — the teeth are at `verify`, exactly like skip-override.
- Disclosure also prints as a plain-ASCII disclosure line in `attest verify` output (next to
  the existing "review independence:" disclosure lines, `cli/attest.py:51`), so a reviewer/human
  always SEES "gate bypassed N times (DISCLOSED: <reason>)".

**Coverage rule — APPEND-ORDER, not timestamps (review B2).** The codebase treats append
position as causal truth and timestamps as audit-only (`reducer.py`; `derive_independence`
last-wins-by-list-position). So the bypass rule is **append-order, never wall-clock**:
- A single `gate_bypass_disclosed` discloses every `gate_bypassed` that appears BEFORE it in the
  attestation's event list (a blanket "yes, I bypassed the gate during this work, reason X" — no
  per-event reasons; the human discloses a known fact).
- The invariant `verify` checks: **the last `gate_bypassed` in the attestation's append order must
  be followed by (or be at/before) a `gate_bypass_disclosed`.** Equivalently, walking the event
  list forward, there must be no `gate_bypassed` after the final `gate_bypass_disclosed`. A
  bypass that occurs AFTER the latest disclosure → undisclosed → blocker. (`attest write`
  re-snapshots every invocation, so the merge-time attestation always reflects the final order;
  re-running `--disclose-gate-bypass` after a late bypass re-satisfies it.)
- No `timestamp` comparison anywhere — this is deliberate, per §3.4's append-order discipline.

### 3.4 Why event-based (not a new log file)

Considered a standalone `.harness/gate-bypass-log.jsonl`. Rejected: it would need its own
snapshot-into-attestation + verify plumbing, duplicating what events already do. The event
stream + attestation snapshot + `verify_attestations` is the harness's existing disclosure
spine (skip-override already lives there); `gate_bypassed` is just another event on it. DRY,
and one disclosure language across the merge gate.

## 4. Honest limits / non-goals

- **Not tamper-proof against the owner (HG-12).** A malicious owner can `rm` events.jsonl /
  the attestation before `attest write`, or never run the harness. We do NOT aim to stop that
  — the math ceiling is unchanged (`project-bedrock-solo-owner-unforgeable`). C buys: a
  **cooperative/honest** bypass is recorded and disclosed, not silent. The agent that bypassed
  in the live test was being "helpful," not hiding — exactly the case C catches.
- **A reduces, doesn't eliminate, agent knowledge of the kill switch.** A capable agent could
  still discover `.harness/gate-disabled` (human docs, git history, reasoning). Not actively
  teaching it + explicitly instructing "don't touch it, surface to human" + disclosing any use
  is strictly better and is the realistic ceiling for an advisory gate.
- **`codex exec` still runs no hooks** (separate gap, OPEN-ITEMS) — C only discloses bypasses
  that go through a firing gate; it does not help where the gate never fires.
- **A's halt instruction is itself advisory.** A sufficiently determined agent could ignore
  "do not bypass." C is the backstop: if it bypasses anyway, the merge gate surfaces it.
- **Bypass outside an active change is NOT disclosed** (B3 consequence). The merge gate is
  per-change; a bypass when no change is active has no change attestation to attach to, so
  `_record_bypass` skips it. This is a deliberate, honest gap — disclosure is a per-change
  merge-gate concern, and a no-change bypass reaches no merge gate to disclose at.

## 5. Test plan

TDD throughout.

- **A (hook messages):** unit-assert the three block messages contain the halt-and-surface
  text and do NOT contain `gate-disabled` (regression-lock the leak shut). Existing block/allow
  exit-code tests unchanged.
- **A (AGENTS.md + adapter docs):** adapter `agents_md_subsection()` tests assert the new bullet
  is present and the old escape-hatch string is absent, for BOTH codex + claude. Regression-lock:
  after `sync --agents-md`, `AGENTS.md` contains NO `gate-disabled`; `docs/adapters/claude-code.md`'s
  gate-block section contains no agent escape-hatch how-to. `sync --check`/`doc check` green.
- **C.1 emit-acceptance (the dangerous one):** assert a `gate_bypassed` event is ACCEPTED by the
  real emit path — `EventWriter(...).emit(ev, skip_validation=True)` does NOT raise
  `EmitPreconditionError` (test against `core/writer` + `emit_validation`, NOT just parse/serialize;
  a parse-only test would pass while the real path rejects). Also assert `state rebuild` over a
  stream containing `gate_bypassed`/`gate_bypass_disclosed` does not mark them INVALID (they are in
  `_INFORMATIONAL`).
- **C.1 record:** with `.harness/gate-disabled` present + an active change, `_decide` ALLOWs AND
  appends a `gate_bypassed` event carrying the helper-resolved change_id + tool/file; with NO active
  change, `_decide` ALLOWs and records NOTHING (skip, no crash, no null-change_id event); an
  `EventWriter`/state-read failure is swallowed (ALLOW still returned — safety path intact).
- **C.3 counting helper:** `gate_bypass_disclosure(events)` returns correct `{bypassed, disclosed}`
  counts; `gate_bypass_for_attestation(att_path)` parses the committed jsonl tolerantly.
- **C.3 verify (append-order):** attestation with `gate_bypassed` and no disclosure → blocker
  (fail-closed); `gate_bypass_disclosed` AFTER the (only) bypass in append order → ok + disclosure
  line printed; a `gate_bypassed` appearing AFTER the last disclosure in append order → blocker
  again. No timestamp is consulted (assert order, not clock).
- **C.3 attest write:** `--disclose-gate-bypass "<reason>"` emits the disclosure event
  (`skip_validation=True`, no state refresh) BEFORE snapshot; the disclosure appears in the
  committed attestation and clears the blocker.
- **events schema + ordering:** `gate_bypassed`/`gate_bypass_disclosed` (a) are ACCEPTED by
  `EventWriter.emit(..., skip_validation=True)` without `EmitPreconditionError`, (b) round-trip
  parse/serialize, (c) are state-preserving on `state rebuild` (in `_INFORMATIONAL`), and (d) an
  attestation containing them still passes `find_ordering_violations` (no false ordering violation,
  `READY_TO_MERGE` check intact).

Self-host: this very change, if it ever bypasses its own gate during implementation, must disclose
it — dogfood the new teeth. (Expected: no bypass needed, since A removes the temptation and the
work proceeds through the normal PLAN_APPROVED→IMPLEMENTATION_IN_PROGRESS flow.)

## 6. Files touched

- `src/super_harness/daemon/hook_entry.py` — A.1 (3 block messages → shared halt constant) + C.1
  (`_record_bypass` at the kill-switch short-circuit).
- `src/super_harness/adapters/agent/codex.py`, `claude_code.py` — A.2 (subsection bullet).
- `src/super_harness/cli/status.py` — A.1b (`next:` step from `SUGGESTIONS.get(state)` in BOTH the
  human render AND the `--json` entry).
- `docs/getting-started.md` — A.3 (keep override how-to; one-line "bypass is disclosed at merge" note).
- `docs/adapters/claude-code.md` — A.3 (reframe both gate-disabled mentions to human-only).
- `src/super_harness/core/events.py` — register `gate_bypassed` + `gate_bypass_disclosed` in KNOWN types.
- `src/super_harness/core/transitions.py` — add both to `_INFORMATIONAL` (state-preserving on rebuild).
- `src/super_harness/engineering/attestation.py` — C.3 NEW pure helper `gate_bypass_disclosure(events)`
  + `gate_bypass_for_attestation(att_path)` (parallel to `derive_independence`/`independence_for_attestation`),
  + verify blocker wired inside the `for slug in added_slugs` loop AFTER the existing scope/check_attestation
  `continue`s, beside the skip-override check (~:228), under the append-order rule + disclosure surfacing.
- `src/super_harness/cli/attest.py` — C.3 `--disclose-gate-bypass` flag (emits `gate_bypass_disclosed`
  BEFORE `write_attestation` so it lands in the snapshot) + disclosure line beside `_independence_line`.
- `AGENTS.md` — regenerated via `sync --agents-md` (content flips: no more `gate-disabled`).
- tests for each unit; `private/OPEN-ITEMS.md` if any residue deferred.

## 7. Open questions — none blocking

The strict-vs-soft fork was decided (strict, mirroring #48 E-gate). The event-vs-log mechanism is
decided (event, §3.4). Remaining specifics (exact event payload keys, disclosure coverage-by-timestamp)
are pinned in §3 and finalized in TDD.

### Resolved by adversarial review (round 1)
- **B1 change_id not in scope at the short-circuit** — `_record_bypass` now resolves it itself (§C.1).
- **B2 `EventWriter.emit` would reject a non-transition event** — emit with `skip_validation=True` +
  add to `transitions.py:_INFORMATIONAL`; registering in `events.py` alone does nothing (emit_validation
  never consults it). The cited `cli/change.py:124` precedent emits a LEGAL transition, so it did NOT
  verify non-transition emit — corrected (§C.1, §5 emit-acceptance test).
- **B3 null change_id is unconstructable/unparseable + matches no attestation** — record only when an
  active change exists; no-change bypass is a documented gap (§4), not a silent null event.
- **S4 `status` doesn't surface SUGGESTIONS** — add a `next:` line to `cli/status.py` (§A.1b).
- **S5 missed leak sites** — `AGENTS.md` content flip (regen + regression test) + `docs/adapters/claude-code.md`
  reframed to human-only (§A.3).
- CONFIRMED-SOUND by review: the attestation snapshot rails (`write_attestation` slices by change_id),
  disclosure emit-then-snapshot ordering, and the re-snapshot coverage rule.

### Resolved by adversarial review (round 2)
- **B1 (round-2): no existing helper counts bypass/disclosure events** — `verify_attestations`' skip-override
  check rides `derive_independence` (code_review only). C.3 now specifies a NEW pure
  `gate_bypass_disclosure(events)` + `gate_bypass_for_attestation(att_path)` reading the committed
  attestation jsonl, wired beside the skip-override check. (Without this C.3 was a no-op.)
- **B2 (round-2): coverage rule said "counts/timestamps"** — repinned to APPEND-ORDER only (no wall-clock),
  matching the codebase's append-position-is-causal-truth discipline (`reducer.py`). Invariant: no
  `gate_bypassed` after the final `gate_bypass_disclosed` in the attestation's event order.
- **SHOULD-FIX: disclosure emit** also uses `skip_validation=True` + no `refresh_state_after_emit`; **status
  `--json`** carries the `next` key too; `SUGGESTIONS.get()` not `[]`.
- **NIT: `_INFORMATIONAL` membership** is what keeps these events from tripping the attestation's own
  `find_ordering_violations` — noted in §C.1 + added to §5 tests.
- CONFIRMED-CORRECT by round 2: B1/B2/B3/S4 round-1 fixes (skip_validation, transitions._INFORMATIONAL,
  change_id self-resolution, no-change skip, status feasibility) all verified against code.
