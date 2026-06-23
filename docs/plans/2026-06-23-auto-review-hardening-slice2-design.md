# Auto-review hardening slice-2 — design (D rework-loop teeth + E skip/attest gate)

> Status: design (brainstorming output, pre-plan)
> Date: 2026-06-23
> Change slug: `2026-06-23-auto-review-hardening-slice2`
> Intent line: ①b "content-corruption closed loop" — see
> `private/CAPABILITY-CONVERGENCE-LEDGER.md` (①b row), VISION Axiom 4,
> HARNESS-GAPS HG-02 / HG-12.
> Umbrella design (SSOT for the whole ①b auto-review effort):
> `docs/plans/2026-06-23-auto-review-hardening-design.md` (§4.D, §4.E, §10).
> Builds on slice-1 (PR #47, `9a635cd`): A `review prepare` bundle + B inlined
> structured verdict + C emit-time teeth (bare/incomplete/stale approve rejected)
> + configurable checklist.

## 1. Problem (what slice-1 left undone)

Slice-1 hardened the code-review boundary so that an `approve` must carry a real,
checklist-covering, fresh structured verdict. Two teeth from the umbrella design
were deferred to this slice:

- **D — the rework loop has no teeth.** The state machine allows
  `AWAITING_CODE_REVIEW → CODE_REVIEW_REJECTED → READY_TO_MERGE`. After a
  `code_review_failed` records findings, nothing forces the eventual `approve` to
  **respond to those findings**. The agent can reject, change one unrelated line,
  then approve with a fresh clean verdict — the prior findings silently evaporate.
- **E — `skip` is a free bypass.** `review skip` emits the *same*
  `code_review_passed` event (only `payload["skipped"]=True`), and `attest verify`
  does not block on it. So one `review skip` sidesteps C **and** D entirely and
  still merges. The HG-12 cut-1 disclosure surfaces the skip in human output but
  never fails the gate.

This slice closes both, building on slice-1's verdict shape. It does **not** add
state-machine transitions, a new event type, or an LLM in the harness.

## 2. What this is (and the honest ceiling — unchanged from the umbrella §2)

D and E are **mechanical, deterministic** teeth. The harness still does not run
the review; *whether* findings are real and *whether* a disposition is honest
stay inferential (the agent's reviewer subagent / a human owns that).

**Honest ceiling (bedrock, not a TODO).** A solo repo owner controls everything
in-repo, so a determined agent can **fabricate** a `prior_findings` block that
"disposes" every open finding without doing the work, or self-sign a
`skip --override`. D validates that *every open finding id is accounted for* and
that `wontfix` carries a note; E validates that a skip is a *deliberate, logged,
committed* act. Neither validates the *truth* of the disposition or the
*justification* of the override. So slice-2 buys **detection-grade /
process-grade** hardening — it moves "reject then silently drop the findings" and
"silently skip" from *free* to *requires either a fabricated disposition block or
a committed, disclosed override, both leaving an auditable trail* — **not**
independence. Real independence is HG-12 (cross-actor, mathematically unreachable
for solo), deferred to v0.2. This is recorded, not papered over; the
ledger/OPEN-ITEMS state it plainly.

## 3. Goals / non-goals

**Goals**
- An `approve` emitted **from `CODE_REVIEW_REJECTED`** is rejected at emit time
  unless its `prior_findings` disposes **every open finding** (`resolved` or
  `wontfix`; `wontfix` requires a note).
- The open-finding set is derived deterministically from the raw event stream in
  append order, correctly handling **N rejects** and **resolved-then-reopened**
  findings.
- A terminal **non-overridden** `skip` of `code-reviewer` becomes a **merge-gate
  blocker** in `attest verify`.
- `review skip --override --reason "<why>"` records a deliberate, committed,
  disclosed override that `attest verify` treats as **pass-with-disclosure**.

**Non-goals**
- New state-machine transitions / new event types (the loop is already in
  `transitions.py`; only payload flags + emit-time and verify-time checks change).
- A reducer field retaining the latest verdict on `ChangeState` — see §7; the
  umbrella §4.B floated it as a "convenience" but it has **no consumer** in this
  slice. Deferred, recorded in OPEN-ITEMS.
- Verifying the *truth* of a finding disposition or the *justification* of an
  override — inferential, not us.
- Hard teeth on `plan-reviewer` — still only the code-review boundary is hardened
  (umbrella §11).
- Independence / un-forgeable reviewer identity — HG-12, v0.2.

## 4. Components

Two independent components. D is an emit-time check on `review approve`; E is a
verify-time check in `attest verify`. They share slice-1's verdict shape but
touch different files and are independently testable.

### D. Rework-loop teeth — open findings must be disposed

**Where it runs:** at `review approve --reviewer code-reviewer` emit time, *only
when the change's current state is `CODE_REVIEW_REJECTED`* (an approve straight
from `AWAITING_CODE_REVIEW` has no prior rejects, so D is inert there). This sits
**after** slice-1's `_validate_code_review_verdict` (coverage + freshness) in
`cli/review.py`. The current state is already available — `review approve`
already calls `derive_state(events_path(root)).get(change)`.

**Data source:** the **raw event stream** (`.harness/events.jsonl`), present
locally during the live lifecycle run when `approve` executes. D cannot use the
reducer's per-change `ChangeState` (or the `state.yaml` snapshot derived from it):
a single last-write-wins field structurally cannot express the multi-reject open
set (umbrella §4.D).

**A new stream-reader helper is required (R1-M2).** `review approve` today only
calls `derive_state(events_path(root)).get(change)` (cli/review.py:96, 182),
which returns a single `ChangeState` — there is **no existing helper that returns
a parsed `list[Event]`** for a change (`extract_change_events`,
attestation.py:87, returns verbatim *strings*; the reducer consumes events
internally and discards them). So this slice adds a small **named deliverable**:

```
read_change_events(events_file: Path, change_id: str) -> list[Event]
  # read file → splitlines → parse_event_line each, TOLERANTLY (skip malformed
  # lines, never raise — same policy as reducer.py / independence_for_attestation)
  # → keep those whose change_id matches, in append order.
```

It lives in **`core/review_verdict.py`** (alongside the walker — it is verdict-
domain stream reading, and keeping it there avoids a new module for one function).
Its tolerance policy is the reducer's:
events.jsonl may contain lines written by older tool versions or partial writes,
so the reader **warn-skips** malformed lines rather than crashing an emit-time
check (which would be a fail-open regression).

**Verdict-file extension (slice-1 left `prior_findings` ignored).** The verdict
schema (umbrella §4.B) carries:

```yaml
prior_findings:                 # required when emitting from CODE_REVIEW_REJECTED
  - id: f-001
    disposition: resolved | wontfix
    note: "..."                 # REQUIRED for wontfix
```

`parse_verdict_file` (in `core/review_verdict.py`) is extended to **validate the
shape of `prior_findings` whenever present**: each entry needs a string `id`, a
`disposition` in `{resolved, wontfix}`, and a non-empty `note` when
`disposition == wontfix`. **It is also extended to require `findings[].id` to be
a non-empty string** — slice-1 only validated `findings[].severity`
(review_verdict.py:60-65), so a finding with no `id` is writable today; D keys on
`id`, so new verdicts must carry it. Shape validation stays in
`parse_verdict_file`; semantic coverage (did it dispose *every open* finding?) is
a separate helper (below), mirroring slice-1's `check_coverage` split.

This makes **new** verdicts clean by construction. But the walker also reads
**historical** `code_review_failed` payloads written before this extension (or by
direct edits) — those may have findings/prior_findings entries with a missing or
non-string `id`. So the walker itself is **independently tolerant** (R1-M1): it
skips any finding/disposition entry whose `id` is missing or non-string, never
raising. Belt-and-suspenders: clean-by-construction going forward + tolerant
on the historical stream, matching the reducer's emit-strict / read-tolerant
split.

**Open-finding derivation (pure function, in `core/review_verdict.py`):**

```
derive_open_findings(events: list[Event], change_id: str) -> list[str]
  open = ordered set (insertion order preserved, for stable error messages)
  for V in events, in append order, where
        V.change_id == change_id and V.type == "code_review_failed":
      verdict = V.payload.get("verdict") or {}
      for pf in verdict.get("prior_findings", []):   # dispose FIRST
          if isinstance(pf.get("id"), str): open.discard(pf["id"])   # tolerant
      for f in verdict.get("findings", []):           # introduce SECOND
          if isinstance(f.get("id"), str): open.add(f["id"])          # tolerant
  return list(open)
```

- The `.get("id")` + `isinstance` guards are the walker's R1-M1 tolerance: a
  malformed historical entry is skipped, never crashes the emit check.
- **discard-then-add per verdict** implements "a finding resolved in one
  re-review then **reopened** by a later reject is open again": if the same
  verdict both disposes `f1` and re-lists it as a finding, it ends up open.
  **Note (deviation from umbrella §4.D wording, R1-n10):** umbrella §4.D phrases
  the rule as "union of introduced, minus every id disposed in any *later*
  verdict." Taken literally, a same-verdict dispose-and-reintroduce has no
  "later" verdict and is ambiguous. This slice resolves that ambiguity with
  per-verdict discard-then-add, which is *more* correct for the reopen case the
  umbrella explicitly calls out. This is a deliberate, documented refinement of
  the SSOT wording, not a contradiction.
- A disposition referencing an id that was never introduced is a **no-op
  discard** (tolerant; not an error). The walker only reads `code_review_failed`
  verdicts — the disposing `approve` verdict is *not* walked (it is the verdict
  being validated against the open set).
- Only `code_review_failed` events carry findings into the open set. A
  `code_review_failed` verdict *may* also carry `prior_findings` (a re-review that
  resolves some and raises others); the walker honors those dispositions.

**The teeth (`check_disposed`, in `core/review_verdict.py`):**

```
check_disposed(verdict: dict, open_ids: list[str]) -> list[str]
  disposed = {pf["id"] for pf in verdict.get("prior_findings", [])}
  return [i for i in open_ids if i not in disposed]   # undisposed open ids, in order
```

In `cli/review.py`, when state is `CODE_REVIEW_REJECTED`: read+parse the event
stream, `open = derive_open_findings(...)`, `undisposed = check_disposed(...)`.
If `undisposed` is non-empty → `EXIT_VALIDATION` with a hint listing the ids and
pointing at `prior_findings`. `wontfix`-without-`note` is already caught earlier
by `parse_verdict_file`.

**Scope of the teeth:** only `review approve` is gated. `review reject` from
`CODE_REVIEW_REJECTED` (the self-loop) is *not* required to dispose — a reject
keeps the loop open, it does not claim resolution. (It *may* carry
`prior_findings`, which the walker honors.)

### E. Skip-override + attest merge-gate blocker

**`review skip` gains `--override`.** `cli/review.py`:
- `--override` is a boolean flag.
- `--reason` default becomes `None`. When `--override` is set, `--reason` is
  **required** (missing → `EXIT_VALIDATION` with a hint); when not set, it falls
  back to `"manual_skip"` (slice-1 behavior preserved). This avoids brittle
  "is the reason the default string?" detection.
- `--override` stamps `payload["override"]=True` alongside the existing
  `payload["skipped"]=True`. `--reason` is already recorded in the payload. No
  new event type, no transition change.

**Classification (`engineering/attestation.py`, extend `derive_independence`).**
`derive_independence` already selects the terminal review (`reviews[-1]`, the last
`code_review_passed`) and reads `payload["skipped"]`. Extend it to also read
`payload.get("override") is True` and surface it (plus the skip `reason`) in the
returned `code_review` dict. The existing classification truth table is unchanged
(`skipped` still classifies as `"skipped"`); `override` is an *additional* field
on the disclosure, consumed by both the blocker rule and the disclosure line.

**The teeth (`verify_attestations`, `engineering/attestation.py`).** Today
`verify_attestations` produces the merge verdict; `derive_independence` is
disclosure-only and never affects `verdict.ok`. E makes a non-overridden terminal
skip a **real blocker**. **Wiring (R1-m6):** `verify_attestations` does not parse
events itself — it works from `att_path` + `derive_state`. So for each
**validated** slug (newly-ADDED, complete, ordered, scope-covering — i.e. an
attestation actually gating *this* PR; the `validated` list at attestation.py:227)
it calls the existing `independence_for_attestation(att_path)["code_review"]` to
get the same terminal classification `derive_independence` uses (`reviews[-1]`,
attestation.py:262-267), then branches: if that classification is `skipped` and
`override` is not True → append:

```
attestation {slug}: code review was skipped without --override
(a deliberate, reasoned `review skip --override --reason ...` is required to merge)
```

This must live **inside `verify_attestations`** (or be merged into
`verdict.blockers` before `ok` is computed) — only a blocker in the verdict can
fail the gate. An **overridden** skip adds **no blocker**; it is
pass-with-disclosure.

**Disclosure (`cli/attest.py`, `_independence_line`).** `derive_independence` adds
`override` (bool) and `reason` (str) **inside the returned `code_review` sub-dict**
(R1-m7) — `independence_for_attestation` returns only `["code_review"]`
(cli/attest.py:167-169), so the fields must live there or the disclosure line
cannot see them. `_independence_line` is extended to read them; an overridden skip
prints loudly, e.g.:

```
review independence: skipped (OVERRIDE: <reason>) — <who>
```

A non-overridden skip is now a blocker, so it surfaces in the error/`blockers`
section as well. The override flag + reason ride along in the committed
attestation snapshot (`attest write` already snapshots the payload-bearing
events), so the override is loudly visible at the merge boundary.

**No retroactive sweep.** `verify_attestations` only inspects attestations
**newly ADDED in the diff** (`added_slugs`, status `A`). Previously-merged
attestations carrying bare skips are not re-verified — E gates only *new* PRs.
(Umbrella §10: old attestations are not retroactively broken.)

## 5. Data flow (delta over slice-1)

```
... done → AWAITING_CODE_REVIEW → commit → review prepare → reviewer subagent
  → review reject  (records code_review_failed verdict with findings)  → CODE_REVIEW_REJECTED
  → fix code → git commit → re-prepare → re-review
  → review approve --verdict-file <f>     (slice-1 C: coverage + freshness;
                                            NEW D: from CODE_REVIEW_REJECTED, prior_findings
                                            must dispose every open finding)
  → READY_TO_MERGE
  → [escape] review skip [--override --reason "..."]   (NEW E: bare skip → merge blocker;
                                                         override → pass-with-disclosure)
  → attest write / attest verify          (NEW E: terminal non-overridden skip → blocker)
```

The slice-1 commit obligation (in-scope tree clean before `prepare`/`approve`)
carries over: each D rework iteration re-commits so HEAD + digest reflect the fix.

## 6. Architecture / reuse map

- **`core/review_verdict.py`** (changed): extend `parse_verdict_file` to validate
  `prior_findings` shape **and require `findings[].id`**; add pure
  `derive_open_findings(events, change_id)` (tolerant of malformed entries) and
  `check_disposed(verdict, open_ids)`; add the `read_change_events(events_file,
  change_id) -> list[Event]` tolerant stream reader (R1-M2; R2: pinned here, not a
  new module).
- **`cli/review.py`** (changed): D check in `approve` (only when state is
  `CODE_REVIEW_REJECTED`) — reads+parses the event stream, runs the two helpers;
  `skip` gains `--override` + reason-required-on-override.
- **`engineering/attestation.py`** (changed): `derive_independence` reads
  `override` + surfaces reason; `verify_attestations` appends a blocker for a
  validated slug whose terminal code-review is a non-overridden skip.
- **`cli/attest.py`** (changed): `_independence_line` discloses override + reason.
- **Reuse:** `parse_event_line` / `extract_change_events` for reading the stream;
  slice-1's `check_coverage`/`_validate_code_review_verdict`; the existing
  attestation milestone/ordering/coverage machinery.
- **Unchanged:** `transitions.py` (loop already supported), `reducer.py` (see §7),
  `PreToolUseGate` (order only), `state.yaml` snapshot path.

## 7. Decision: do NOT add a reducer field for the latest verdict

The umbrella §4.B floated retaining the latest code-review `verdict` on
`ChangeState` (mirroring `scope` retention) as a "convenience" for D, and
OPEN-ITEMS echoed it. **This slice does not add that field.** Verified rationale:

- The only consumer it was designed for — C's freshness check — was implemented a
  **different, better way** in slice-1 (recompute the digest from the committed
  HEAD tree and compare to the verdict-file's `bundle_digest`; tamper-evident,
  reproducible). C does not read any reducer-retained verdict.
- D — the other candidate consumer — **structurally cannot use** a single
  last-write-wins field: the multi-reject open set requires the raw stream walker
  (umbrella §4.D, this doc §4.D).

So the field would be **orphaned dead code** this slice (no test could exercise a
real use of it). It is **deferred, not dropped**: recorded in
`private/OPEN-ITEMS.md` as "reducer latest-verdict field — deferred pending a real
consumer (C uses HEAD recompute; D uses the stream walker; a v0.2 status/`change
show` display may want it — add it then, with a real consumer to test against)."
This is a deliberate deviation from the umbrella's stated plan, on YAGNI grounds.

## 8. Testing (TDD — every new helper first)

- `parse_verdict_file` `prior_findings` shape: valid; missing `id`; bad
  `disposition`; `wontfix` without `note` → reject; `resolved` without note → ok.
- `derive_open_findings`: empty (no rejects); single reject; **N-reject
  self-loop**; **resolved-then-reopened** (later reject re-lists a disposed id);
  disposition referencing a never-introduced id (no-op); append-order stability.
- `check_disposed`: all disposed → empty; one missing → that id returned; order
  preserved.
- D emit (cli/review): approve from `AWAITING_CODE_REVIEW` → D inert (no
  prior_findings required); approve from `CODE_REVIEW_REJECTED` with all disposed
  → pass; with an undisposed open id → `EXIT_VALIDATION`; `wontfix` without note
  → rejected.
- `derive_independence` override: terminal skip with `override` → `override:True`
  + reason surfaced; without → `override:False`.
- `verify_attestations` E blocker: validated slug, terminal bare skip → blocker
  (`ok=False`); terminal `--override` skip → no blocker (pass-with-disclosure);
  terminal real approve → no blocker; non-validated (non-covering) skip
  attestation → not a blocker for *this* diff (only validated slugs gate).
- `_independence_line`: override skip wording; reason present.
- `review skip --override` without `--reason` → `EXIT_VALIDATION`; with reason →
  payload carries `override:True` + reason; bare skip → `manual_skip`, no override.

## 9. CLI surface + doc sync (in scope)

- `review skip` gains `--override` / `--reason` (reason now required on override).
  No new *leaf command* — but `review skip` gains a new `EXIT_VALIDATION` path
  (`--override` without `--reason`), and it currently has **no `_EXIT_CODES`
  entry** in `scripts/gen_cli_reference.py` (only `review prepare`/`review approve`
  do, R1-m3). So add a `"review skip"` entry (0 ok / 2 missing-reason-on-override
  / 3 no-`.harness/`) before regenerating. Regenerate `docs/cli-reference.md` via
  `super-harness doc check --fix`.
- Update the `AGENTS.md` review-protocol section (source:
  `src/super_harness/adapters/agent/claude_code.py` `_AGENTS_MD_SUBSECTION`, then
  `doc check --fix`). Note (R1-m4): the **existing** wording describes `review
  skip` as a free "escape hatch (records an approval with `reason=manual_skip`)"
  — after E that is *actively misleading* (a bare skip now blocks at merge), so
  this is a **rewrite**, not an append:
  - an approve coming out of a rejected review must dispose every open finding via
    `prior_findings` (`resolved`/`wontfix`+note);
  - `review skip` of `code-reviewer` now blocks at the merge gate unless
    `--override --reason "..."` is used.
- `--verdict-file` already documents `prior_findings` from slice-1; no new option
  on `approve`/`reject`.

## 10. Self-host bootstrap (the change validates itself)

- **D is inert for this PR.** This PR's own code review is expected to pass from
  `AWAITING_CODE_REVIEW` directly (a clean approve, no prior reject), so D
  requires no `prior_findings` here. If a real reject occurs during this PR's
  lifecycle, the author genuinely disposes the findings — that is the intended
  behavior, not a workaround.
- **E does not fire on this PR.** This PR's terminal code-review is a real approve
  (not a skip), so E's blocker stays silent for this PR's own attestation.
- **Exercise D and E on throwaway changes — the "bite" demonstration.** Per the
  ledger's "engaged but not yet drawn blood" note and umbrella §10, run a live
  tripwire on **throwaway** changes (not this PR's attestation):
  - **D bite:** seed a lifecycle to `CODE_REVIEW_REJECTED` with a finding
    `f-001`; attempt `review approve` whose `prior_findings` does **not** dispose
    `f-001` → confirm `EXIT_VALIDATION` + "undisposed: f-001"; then dispose it →
    confirm pass.
  - **E bite (R2-MAJOR — the recipe is NOT a bare `write + verify`):**
    `attest verify` keys off a real `git diff --name-status base...head`
    (cli/attest.py:116-161) and only reaches a slug's skip classification if that
    slug's attestation appears as status `A` in the diff **and** its `scope.files`
    covers at least one non-attestation subject in the same diff (else the slug is
    rejected at attestation.py:221-223 with a *coverage* blocker and the skip
    blocker never fires — the wrong tooth). Two honest ways to demonstrate the E
    bite, pick one in the plan:
    - **(preferred) unit layer:** drive `verify_attestations(root,
      [DiffEntry(...)])` directly with a constructed diff (a committed throwaway
      attestation whose terminal code-review is a bare skip + a covered subject) —
      this is the truthful bite surface and is what `tests/unit/engineering/
      test_attestation.py` already exercises the machinery with. Assert `ok=False`
      with the skip blocker; then flip to `--override` and assert `ok=True` +
      disclosure.
    - **(CLI layer) only if a full scaffold is built:** a throwaway git repo with
      a base commit, the throwaway attestation `.jsonl` **committed** (so it shows
      as `A` in `base...head`), and a covered non-attestation subject file also in
      the diff; then `attest verify --base <c0> --head <c1>` shows the skip
      blocker. More setup, same result.
  Capture the output into the PR / ledger. **Honest framing:** this proves the
  teeth *engage and bite on a constructed case*; it is **not** "caught a real
  evasion in the wild." The ledger records "engaged + throwaway self-bite
  demonstrated; no real reject-skip evasion caught yet" — not "drew blood."
- `plan ready --scope` must list **every changed file individually** (src + tests
  + docs + AGENTS.md + this design doc + the implementation plan) — exact paths,
  no directory prefixes (`attest verify` matches by canonical-path set membership;
  see `project-self-host-pr-attest-scope`).

## 11. Deferred / open items (recorded, not dropped)

- Reducer latest-verdict field on `ChangeState` — deferred pending a real consumer
  (§7); recorded in OPEN-ITEMS.
- Truth/justification of a `prior_findings` disposition or a `skip --override`
  reason — inferential, self-signable by solo owner (HG-12 ceiling, §2); v0.2.
- Consistency check that an `approve` verdict itself has no `fail` checklist item
  — a slice-1 gap, not expanded here (out of scope; note only).
- Hard teeth on `plan-reviewer` — still deferred (umbrella §11).
- HG-12 reviewer independence (cross-actor, un-forgeable) — bedrock ceiling, v0.2.
