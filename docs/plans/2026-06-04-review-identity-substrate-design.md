# Design: Reviewer-identity & review-independence substrate (HG-12, cut 1)

Date: 2026-06-04
Status: approved (brainstorm), pending implementation plan
Tracks: HG-12 / HG-DF item **B** — cross-actor reviewer identity. This is the
**first cut**: the *substrate* (record identity + disclose independence), not the
anchor (un-forgeable enforcement).

> NOTE: this file deliberately carries NO `change:` / `stage:` frontmatter — the
> repo self-hosts on the SuperpowersAdapter, which *discovers changes by that
> frontmatter in `docs/plans/`*. With it present, the next `adapter scan-once`
> would auto-emit `intent_declared` / `plan_ready` for this doc before the change
> is deliberately declared. It stays an inert design artifact until `change start`
> is run explicitly. (Same precedent as `2026-06-03-layer2-merge-gate-design.md`.)

## 1. Problem & the bedrock truth that bounds it

The Layer-2 CI merge gate (PR #36, attestation verifier) stopped "no lifecycle at
all" — a changed file with no covering, complete, ordered, scope-covering
attestation is blocked at merge. Its honest, deliberately-left ceiling: it does
**not** prove the recorded review actually happened by anyone but the author. A
single actor who emits their own `review approve --reviewer code-reviewer` and
writes a complete attestation still passes. That is the **self-signing** gap —
HG-DF item B / HG-12.

Brainstorming this cut surfaced a bedrock truth that **bounds what is achievable**:

> Un-forgeable cross-actor review needs an authority the author does **not**
> control. A solo repo *owner* controls everything in-repo — workflows, all keys,
> branch-protection settings, committed files, and any bot account they create
> (its token is in their hands). Therefore **no in-repo mechanism is un-forgeable
> against a solo owner.** "A reviewer the author cannot self-sign" requires a
> genuinely different person, or a third-party service. For a solo owner the
> project's north-star guarantee is *mathematically unreachable*, not merely
> unbuilt.

This is itself an honest finding to deliver, not a thing to paper over with a
mechanism that only *looks* like enforcement. Given it, HG-12 decomposes into:

- **Substrate (this cut):** the system records **no identity at all** today —
  every event is `Actor(type="human", identifier="cli")` (a placeholder; see the
  pre-existing TODO at `cli/change.py`). Before any cross-actor story is even
  expressible you must record real author & reviewer identities and disclose
  whether review was independent. This cut does exactly that, **sold as
  disclosure, not enforcement.**
- **Anchor (later cuts):** bind identity to a harder-to-forge source — GitHub
  PR-review attribution (P1, genuinely un-forgeable for teams / non-admin
  contributors) or a CI reviewer (P3, gives solo a real independent review *by
  default*). Both are deferred; both require this substrate first. **Honest caveat
  even for the anchor:** against a *solo owner*, even P3 is only *raise-the-cost /
  visible-if-forged* (the owner controls the workflow and can edit the reviewer to
  pass), **not** un-forgeable. The fully un-forgeable guarantee (P1 with a real
  second account) needs an actor the author does not control. So the honest
  end-state for this solo repo is **disclosure + raise-the-cost**, never the
  unreachable north-star.

## 2. What this cut does and does NOT prove (read before §4)

**Proves (the disclosure guarantee):** every change's attestation now records a
real **author identity** (on `intent_declared`) and a real **reviewer identity**
(on `plan_approved` / `code_review_passed`), and the merge boundary
(`attest verify`) **discloses**, per change, whether code review was
`self-signed`, `independent` (by a different identity), `skipped` (the
`review skip` escape hatch), or `unattributed` (legacy `"cli"` placeholder). (A
fifth class, `ci`, exists in the classifier but is not producible until the P3
anchor — §4.1 row 2.) The self-sign that was previously invisible is now recorded
and surfaced where merges are decided.

**Does NOT prove / explicit non-goals (deferred):**
- That self-signing is **prevented**. This cut **discloses, it does not block** —
  `attest verify` pass/fail is unchanged. Blocking self-signed changes is the
  anchor cut.
- That identities are **un-forgeable**. They are **self-asserted** (default:
  `git config user.email`); a solo owner can set both author and reviewer to
  whatever they like. Enforcing `reviewer ≠ author` on self-asserted identity
  would be theater (the Tension-5 trap) — which is exactly why enforcement is
  deferred to the anchor cut, where identity is bound to GitHub/CI.
- Cross-actor enforcement, CI-run review, GitHub-review binding → **anchor cuts
  (P1/P3)**.

**So the honest bar this cut raises:** from *"review independence is invisible —
a self-signed change is indistinguishable from an independently-reviewed one"* to
*"every change's review independence is recorded and disclosed at the merge
boundary."* It makes the dogfood pain point (a self-sign that hides in plain
sight, per the don't-conflate-ritual-with-value lesson) **visible**. It does not,
and does not claim to, make self-signing impossible for a solo owner.

## 3. Identity model — no schema change

Decision: **do not touch the event schema.** `Actor.identifier` is already a free
string; today it carries the placeholder constant `"cli"`. This cut populates it
with a real identity and derives a property from it.

- **`actor.type`** stays the existing enum (`human` for CLI-driven, `ci` for
  CI-emitted — already used by `on_merge` / `pr_emit`). Unchanged.
- **`actor.identifier`** becomes the resolved identity instead of `"cli"`.
- **`payload.reviewer`** ("plan-reviewer" / "code-reviewer" role label) and
  **`actor.identifier`** (who) are kept as **separate concerns** — the role label
  stays in payload; the *who* goes in the actor. Both remain.

### 3.1 `resolve_identity` (new `core/identity.py`)

`resolve_identity(workspace, override=None) -> str`, precedence (first non-empty
after `.strip()` wins):

1. `override` = the `--as <id>` CLI flag value (explicit override),
2. env `SUPER_HARNESS_ACTOR` (empty / whitespace-only → treated as unset),
3. `git config user.email` (run in `workspace`; the natural "who am I" — also the
   identity P1/P3 anchors will later map to in git/GitHub),
4. fallback `"cli"` (preserves today's behavior + reads as `unattributed`).

**Failure modes must all fall through to `"cli"`, never raise** (enumerated as
test rows): not a git repo (git exits non-zero), `user.email` unset (exit 1, empty
stdout), no `git` binary (`FileNotFoundError`), whitespace-only output. The git
call is a private **seam** (e.g. `_git_config_email(workspace)`, swallows
`subprocess` non-zero + `FileNotFoundError`) so tests mock it and never shell out
(pattern already used elsewhere, e.g. `test_l1_helpers.py` patches
`subprocess.run`).

**`--as` is a Python reserved word** → the click option needs an explicit dest:
`@click.option("--as", "as_identity", ...)`. Env var `SUPER_HARNESS_ACTOR`
verified free of collision with existing `SUPER_HARNESS_*` names.

This supersedes the `cli/change.py` `TODO(post-v0.1)` about per-user identifiers
(`getpass.getuser()` doesn't map to git/GitHub authorship, which the anchor cuts
need). The implementation **must delete that stale TODO and the now-false comment
"single 'cli' identifier is used for every `Actor(...)` below"** (doc-drift the
project explicitly guards against).

### 3.3 Privacy note (PII in committed evidence)

The default identity is `git config user.email`, which is **already** in every
git commit's authorship metadata — so attestations record the same email git
history already carries, not new PII. But it is now *also* in committed
`events.jsonl`/`.harness/attestations/*.jsonl`, and this repo flips public
(Phase 15). Document the `SUPER_HARNESS_ACTOR` / `--as` escape hatch as the way a
contributor chooses a non-email handle. (Hashing/truncating is rejected — it would
break the git/GitHub-authorship mapping the anchor cuts depend on.)

### 3.2 Where identity is populated (this cut: the two load-bearing points only)

- `cli/change.py` `start` → `intent_declared.actor.identifier = resolve_identity`
  = **author identity** (whoever declares intent owns the change). Add `--as`.
- `cli/review.py` verdicts (`approve` / `reject` / `skip`, i.e. `plan_approved` /
  `code_review_passed` / `plan_rejected` / `code_review_failed`) →
  `actor.identifier = resolve_identity` = **reviewer identity**. Add `--as`
  (`as_identity` dest). Additionally, **`review skip` stamps
  `payload["skipped"] = True`** (the structured marker §4.1 row 3 classifies on;
  `--reason` stays free-text audit). `approve`/`reject` do not set it.

Other emit sites (`plan ready`, `implementation start`, `done`, `abandon`) keep
the `"cli"` placeholder for now — they are not needed to derive author/reviewer
independence. Filling them out (full identity model) is a registered follow-up.

## 4. Independence derivation & disclosure

### 4.1 `derive_independence` (pure function, in `engineering/attestation.py`)

Confirmed by review: the reducer's `ChangeState` does **not** retain per-event
actors (`core/state.py` has no actor field; `core/reducer.py` never reads
`ev.actor`), so independence must be derived from **raw events**. The attestation
file carries per-event actors verbatim (`write_attestation` snapshots raw lines).

Signature: **`derive_independence(events: list[Event]) -> dict`** — pure over
already-parsed events (no I/O; tests pass event lists directly). A thin
`independence_for_attestation(att_path)` reads the file →
`parse_event_line` → `derive_independence` (the only I/O), called by the CLI.

Inputs:
- `author` = identifier of `intent_declared` (the change-owner).
- `R` = the **last `code_review_passed` in append order** (a reject→re-review
  cycle legitimately yields more than one; the last is the verdict that reached
  READY_TO_MERGE).

**This cut discloses code-review independence only.** `plan_approved` identity is
recorded (§3.2) but its disclosure is a follow-up (§5 OUT).

**Classification truth table (evaluated top-to-bottom, first match wins):**

| # | Condition | Class | Meaning |
|---|-----------|-------|---------|
| 1 | no `code_review_passed` present | `unattributed` | no review milestone recorded |
| 2 | `R.actor.type == "ci"` | `ci` | reviewer is a CI actor — **forward-compat, NOT reachable this cut** (see note) |
| 3 | `R.payload.get("skipped") is True` | `skipped` | review was *skipped* (`review skip`), not performed — records the identity but never reads as a real review |
| 4 | `R.actor.identifier == "cli"` **or** `author == "cli"` | `unattributed` | a legacy/placeholder side makes the author≠reviewer test meaningless |
| 5 | `R.actor.identifier == author` | `self-signed` | same identity authored and reviewed |
| 6 | otherwise | `independent` | a distinct recorded identity reviewed |

Returns `{author, code_review: {classification, reviewer, skipped}}` (reviewer +
skipped-flag included for audit, e.g. `skipped — alice@…`).

**Row 2 (`ci`) is forward-compatible, not producible this cut.**
`code_review_passed` is emitted only by `cli/review.py`, which hardcodes
`Actor(type="human", …)`; this cut changes only `actor.identifier`, not
`actor.type`. The two `type="ci"` emitters (`on-merge`, `pr-emit-opened`) never
emit `code_review_passed`. So row 2 cannot fire via the current CLI — it is
**unit-tested via a hand-constructed `ci` event** (keeps the branch covered) and
arrives for real only with the P3 anchor (CI reviewer). It is kept in the
classifier so P3 needs no re-plumbing; the doc must not present it as live CLI
behavior (hence it is dropped from the §4.2 sample output).

**Row 3 (`skipped`) keys on a structured marker, not free text.** `review skip`
stamps a dedicated `payload["skipped"] = True` (a key `--reason` cannot collide
with — `--reason` stays free text for audit). Classifying on
`payload.get("skipped")` closes both forgeries the editable-`reason` approach
opened: `review approve --reason manual_skip` is **not** mislabeled `skipped`, and
`review skip --reason "on vacation"` is **still** `skipped`.

Rows 1–6 must each be an explicit unit-test row, including the self-referential
cells `(author="cli", reviewer=real)` → `unattributed` and `(author=real,
reviewer="cli")` → `unattributed`, the `cli==cli` legacy pair → `unattributed`
(row 4 fires **before** the row-5 equality branch, which is why old all-`"cli"`
changes read `unattributed`, not `self-signed`), N>1 `code_review_passed`
last-wins, the structured-`skipped` true/false cases, and a constructed-`ci`
event → `ci`.

### 4.2 Disclosure point = the merge boundary (`attest verify`)

`attest verify` is the agent-agnostic, pure-committed-evidence merge gate; the
attestation carries actor identities, so disclosure lives here with **no
dependence on `events.jsonl`** (absent in CI).

**Attachment point (important — matches the gate's real data flow):**
`verify_attestations` returns `AttestationVerdict.attestations` = the slugs that
were **newly ADDED in the diff, passed `check_attestation`, and cover a subject**
(`attestation.py` `validated` list). `verify_attestations`' verdict logic is
**unchanged**; the CLI (`cli/attest.py`) loops exactly those validated slugs,
reconstructs each `root/.harness/attestations/<slug>.jsonl`, calls
`independence_for_attestation`, and prints one line per slug:

```
review independence: self-signed (self-review) — dawinialo@gmail.com
review independence: independent — alice@example.com
review independence: skipped — alice@example.com
review independence: unattributed (legacy "cli" placeholder)
```

(No `ci` line shown — it is not producible via the current CLI; see §4.1 row 2.)

- **Pass/fail is unchanged** — disclosure, not a gate. The `(self-review)` marker
  is plain ASCII (no emoji — avoids non-UTF8 CI-locale corruption + exact-match
  test fragility).
- **Rejected or non-added attestations get NO disclosure line** (they are already
  surfaced as FAIL / are not in scope). If a diff has **no** validated added
  attestation, `attest verify` prints **no** independence line at all.
- **Disclosure must never raise out of the non-failing path.**
  `independence_for_attestation` re-reads the validated attestation (a second read
  after `verify_attestations`' `derive_state`); it must use the **same tolerant
  parse policy as the reducer** (catch `EventSchemaError`, skip the line) — because
  `check_attestation`/`derive_state` *tolerate* malformed lines (warn+skip) while a
  naive `parse_event_line` pass would *raise*, which could crash an otherwise-
  passing `attest verify`. Test row: an attestation that passed `check_attestation`
  but contains a tolerated-malformed line still prints a disclosure line and
  leaves the exit code unchanged.
- `--json`: the CLI adds an `independence` field (list keyed by slug) to the
  envelope `data`, preserving the existing envelope contract.

The disclosure is additive output the CLI computes from files
`verify_attestations` already validated; the pure `derive_independence` carries no
I/O.

## 5. Scope

**IN (this cut):**
- `core/identity.py` + `resolve_identity` (+ git seam, all fall-through modes).
- Identity on `intent_declared` (`change start --as`) and review verdicts
  (`review … --as`, threaded through `_emit_verdict` for approve/reject/skip).
- `derive_independence` pure function (the §4.1 truth table, incl. the `skipped`
  distinction read from the structured `payload["skipped"]` marker — not the
  free-text `--reason`).
- `attest verify` disclosure (human + `--json`), non-failing, attached to the
  validated-added attestations only.
- Delete the stale `cli/change.py` TODO + false "single 'cli'" comment.
- Unit tests for all; `docs/cli-reference.md` regen; this design doc + the
  implementation plan.

**OUT (register in OPEN-ITEMS with status):**
- **Enforcement** (`self-signed` → FAIL): the anchor cut (P1/P3). `DOABLE-NOW`
  but **deliberately not done** — enforcing self-asserted identity solo = theater
  (Tension 5); needs an un-forgeable anchor.
- **`plan_approved` independence disclosure** (identity is recorded this cut, but
  only `code_review_passed` is disclosed) → `DOABLE-NOW`.
- Identity at the other emit sites (`plan_ready` / `implementation_started` /
  `implementation_complete` / `intent_abandoned`) → `DOABLE-NOW`, full
  identity-model follow-up.
- PR-body disclosure + a standalone `status`-style display → `DOABLE-NOW`, defer
  (`attest verify` is the spine).
- P1 (GitHub-review binding) / P3 (CI reviewer) → `COUPLED-phase` on the HG-12
  anchor.

## 6. Reuse / non-disruption

- **Reuse unchanged:** `parse_event_line`, `find_ordering_violations`,
  `derive_state`, `verify_attestations` verdict logic. No new
  lifecycle/validation surface.
- **Not touched:** the event schema (`Actor` dataclass), the `events.jsonl`
  storage model, `attest verify`'s pass/fail logic, the existing PreToolUse gate.
- **Backward compatibility:** existing events / merged attestations carry
  `identifier="cli"` → classified `unattributed`, *not* `self-signed`. Old
  changes were genuinely unattributed; this reads honestly and avoids noise.

## 7. Dogfood / verification (distinguishing ritual from value)

- **Unit matrix:** the §4.1 truth table rows 1–6 (incl. `skipped`,
  `(author="cli", reviewer=real)`, `(author=real, reviewer="cli")`, `cli==cli`
  legacy, N>1 `code_review_passed` last-wins). Assert `attest verify` prints the
  right line **and that exit code / pass-fail is unchanged** in every case (the
  line is output, not a gate); assert a diff with **no** validated added
  attestation prints **no** independence line. `resolve_identity` precedence
  (flag > env > git > fallback) **plus the four fall-through modes** (not-a-repo,
  unset email, no git binary, whitespace) with the git seam mocked. `--json`
  gains `independence` while the envelope contract is preserved. Grep for and
  cover any `change resume` Markdown / `asdict` snapshot consumers of
  `actor.identifier` that a real email would shift — the **intended** new behavior
  is that `change resume` now shows the resolved identity; update those snapshots
  to the real value, do **not** re-pin them to `"cli"`. Also a test row for the
  structured `skipped` marker and one for a tolerated-malformed attestation line
  (disclosure still prints, exit code unchanged).
- **The honest dogfood (demonstrates the *plumbing*, not teeth):** after merge,
  start a fresh change and self-review it with the author's own
  `git config user.email`; run `attest verify --base main --head HEAD` **on that
  change's branch, where its newly-added attestation covers the changed subjects**
  (the only configuration in which the line is emitted, per §4.2) and observe it
  **honestly prints `self-signed (self-review) — dawinialo@gmail.com`**. The value
  is that the self-sign PR #35 left invisible is now recorded and surfaced at the
  merge boundary. **But be precise about what this proves:** only that the
  disclosure plumbing works end-to-end — it surfaces the identity the author
  supplied. It proves *nothing* about independence-as-a-property; a lazy author
  defeats it with `--as someone@else` or a different `git config`. Per the
  don't-conflate lesson, the claim is **"independence is now disclosed"**, NOT
  "self-signing is blocked / harder" — it is neither (disclosure only; the owner
  forges the identity freely).
- **Self-referential note:** *this* change's own `intent_declared` is emitted by
  `change start` *before* the new code is active, so its author identity is the
  `"cli"` placeholder → `unattributed` regardless of reviewer (truth-table row 4)
  — honest (the feature does not retroapply to its own declaration). The
  fresh-change dogfood above is what exercises a real identity.

## 8. Known limitations (documented, not solved)

- **Self-asserted identity is forgeable** (§2): default `git config user.email`,
  and a solo owner can set author and reviewer to anything. Disclosure is honest
  about *what was recorded*, not *who really did it*. Un-forgeable attribution =
  anchor cut (P1/P3).
- **Disclosure ≠ enforcement** (§2): nothing is blocked by independence in this
  cut.
- **Quality is still inferential** (Axiom 8): even an `independent`/`ci` review is
  not proven *correct* — only that a distinct identity is on record.
- **Partial identity model** (§5): non-author/non-reviewer emit sites still carry
  `"cli"` until the follow-up.
- **Disclosure is scoped to validated-added attestations** (§4.2): under the
  one-PR-per-change / branch==slug convention every changed file's attestation is
  added in its PR, so this covers the realistic case; a change whose attestation
  was already merged (not in the diff) gets no line. Same scoping the merge-gate
  §10 already documents.
- **`skipped` is recorded, not blocked** (§4.1 row 3): a `review skip` still
  reaches READY_TO_MERGE (it is the existing escape hatch); disclosure now makes a
  skipped review *visible* rather than indistinguishable from a real one, but does
  not prevent it.
- **PII** (§3.3): committed evidence records the contributor's git email (same as
  commit authorship); `SUPER_HARNESS_ACTOR` / `--as` is the opt-out.
