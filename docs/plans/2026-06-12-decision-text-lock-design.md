# Design: The weldable hard teeth — decision text-lock + executable checks

Date: 2026-06-12 (scope expanded 2026-06-14; local-sensor + inline-check refinement
2026-06-15; **Tool B counterexample mechanism resolved 2026-06-16**, §4.2)
Status: converged (brainstorm) — aligned interactively with the user (product owner).
Both tools buildable; Tool A shipped (PR #40). Tool B mechanism now resolved (§4.2);
no TDD/task breakdown yet.

> NOTE: like the sibling design docs in this folder, this file carries **NO**
> `change:` / `stage:` frontmatter — the repo self-hosts on the SuperpowersAdapter,
> which discovers changes by that frontmatter in `docs/plans/`. It stays an inert
> design artifact until `change start` is run explicitly.

## 0. How it's used — the end-to-end picture (read this first)

The whole-shape view, so the tool isn't a pile of parts. Scenario: a solo owner + an
AI agent that runs largely unattended; the human only ratifies at decision points.

1. **A decision is set (the human's moment).** The AI hits something that needs a call
   and proposes a decision; the human approves. The record holds a **locked one-line
   claim** + **unlocked rationale**:
   - claim (locked): *Passwords must be stored with bcrypt — never plaintext, never MD5.*
   - rationale (unlocked): considered argon2, but team already runs bcrypt …
   The human runs `decision ratify password-storage`. That act records "this human, at
   this time, approved this claim" and freezes a fingerprint of the claim.
2. **The AI works, runs, never interrupts — and self-checks locally as it goes.** It
   implements the decision, tags the code, keeps changing code / refactoring / editing
   the rationale. At its natural checkpoints (a chunk done, before a commit) the agent
   runs `decision check` *locally* — the same CLI any code agent can call — and gets an
   instant signal if it drifted. As long as it does not touch the *claim* and the code
   honors the decision, that check is green and the human is never bothered. This is what
   vibe-coding wants: drift caught in a few lines, not half a day later in CI.
3. **The one moment the gate lights up:** the AI tries to change the ratified *claim*
   (e.g. soften "never MD5" → "prefer bcrypt" to launder lazy code), or its code starts
   failing a check the decision carries. Its own local `decision check` flags it first;
   if that's ignored or bypassed, **CI's `decision check` blocks merge** (the
   un-bypassable backstop): "claim of 'password-storage' changed without re-ratification
   → human must re-ratify; AI cannot bypass."
4. **The human is pulled back to judge:** re-ratify if legitimate, reject (make the AI
   fix the code) if laundering. The AI cannot clear this itself.

**One line:** *99% of the time the AI runs free, zero interruptions; the gate fires only
when the AI tries to overturn a board the human pinned — forcing the human back to that
one decision.* This slice is complete and usable on its own; executable checks add a
second layer later, not a prerequisite.

---

Implements the third standing obligation from the umbrella design
(`2026-06-05-decision-conformance-harness-design.md` §7.2, §12.2–12.3): **a
human-ratified decision must actually bind the AI's work; betrayal surfaces, never
laundered.** Builds on slice-1 (`2026-06-08-decision-records-anchors-design.md` —
decision records + `@decision:` anchors + referential-integrity `decision check`).

---

## 1. What this slice is: *all the weldable hard teeth* (and only those)

Sort the umbrella's "teeth" by one axis: **can the mechanism be welded shut
(deterministic), or does it need someone to *understand* content (soft, can't be
guaranteed)?** This slice ships **every weldable-shut mechanism, and nothing soft.**

There are exactly **two** weldable teeth, and they guard the **two ends** of a
decision:

| Tool | End it guards | Question (mechanical) | How | Certain? |
|---|---|---|---|---|
| **Text-lock** (text-change detection) | the **decision** | "Was the ratified decision *text* altered?" | hash compare — "did it move" | **Yes** |
| **Executable check** | the **code** | "Does the code *fail* a runnable check attached to the decision?" | run the check; non-zero → block | **Yes** (for the checkable subset) |

What is **deferred** is the **soft** part — and it is *not* a "semantic detection
tool", because semantics cannot be reliably detected (no ground truth; an AI/human
must understand → can err, be fooled, rubber-stamp). The soft residue is two honest
pieces, both later:

- decisions that **can't** carry a runnable check → judged by an **independent
  re-review against a written acceptance criterion**, on the record (forced re-look +
  trail, *not* detection);
- a **mechanical trigger that routes a changed anchor into that review** (attention
  routing, §12.4) — it detects nothing, it points eyes.

So this slice's boundary is principled, not arbitrary: **everything that can be made
certain, done now; everything that can only be soft, done later.**

## 2. The two ends, and the foundation relation

A decision is a thread; these two tools pin its two ends mechanically:
- **Text-lock** keeps the *decision* (the ruler) from being silently rewritten.
- **Executable check** keeps the *code* from silently failing what the decision
  demands — for decisions concrete enough to carry a runnable check.

**The text-lock is the foundation under everything that measures code against a
decision** — both the executable checks here and the soft re-review later. Every such
mechanism uses the decision text as its ruler; the text-lock guarantees the ruler
isn't quietly lengthened or shortened. If an agent could edit a decision to excuse
its code, every downstream check/review would validate an already-tampered standard.
So even though the two tools are independent, the text-lock is logically first.

Product value, plainly: **a board the human ratified cannot be silently overturned
(text-lock), and where a decision is concrete enough, the code is mechanically held
to it (executable check).** In the unattended vibe-coding flow these are the parts
that hold without anyone watching — because they need no judgement, only a hash and
an exit code.

## 3. Tool A — the text-lock (text-change detection)

The decision body is the prose a human approved (slice-1 record:
`docs/decisions/<id>.md`). The lock binds *the body at ratification time* to the
ratified status.

- **Ratify:** `decision ratify <id>` already stamps `ratified_by` / `ratified_at`.
  Add one step: fingerprint the body and store it in frontmatter
  (`ratified_text_hash`). Fingerprint over a **minimally normalized** body —
  normalize *only* line endings / trailing whitespace / leading-trailing blank lines.
  Nothing else: changing punctuation, wording, or fixing a typo *is* changing what
  the human approved and *should* re-ratify. (Deliberate opposite of code-side
  normalization, which suppresses reformat noise; a decision body should rarely move,
  so over-firing here is cheap and correct — prefer false alarm over silent miss.)
- **Check:** fold into the existing `decision check` — **no new gate**. This command is
  run **two ways**: the agent runs it *locally* throughout its work (fast feedback,
  §4.1), and CI runs it as the un-bypassable backstop before merge.
  Recompute each ratified decision's body fingerprint, compare to stored.
  Mismatch → **integrity-lock violation**: a *standalone* blocking condition
  (`CheckResult.integrity_violations`; `ok` false; exit non-zero), blocks whether or
  not any code anchor points at it; the decision also drops from the
  "effectively-ratified" set (so its anchors surface dangling-up). Stays **read-only**
  — never rewrites the record.
- **Unlock:** re-run `decision ratify` (a human re-ratifies) → fresh identity, time,
  fingerprint. The AI has no other legal path to clear it.
- **What the fingerprint covers — closing the gap to Tool B:** Tool B's check + its
  counterexample live **inline in the same decision body** (§4), so this one fingerprint
  locks them too. An AI that silently guts a check (rewrites it to `exit 0`) moves the
  body hash → text-lock fires, even though the *claim* prose didn't change. One lock,
  both teeth — no second hash to maintain.

## 4. Tool B — executable checks (only fires on failure → no flood)

A decision becomes a *hard* anchor only if it arrives **with a runnable check**
(umbrella §12.3: "no check → no hard anchor"). This is what gives code-conformance
real teeth without the false-positive flood that killed the "code changed → re-look"
approach: **the check fires only when it *fails*, not when code merely changes** —
rename/reformat never trips it.

- **Birth + ratify together:** when the AI authors a decision it proposes a check —
  a runnable command/test/rule that exits zero (satisfied) / non-zero (violated). The
  human ratifies **decision + check as one unit**.
- **Storage — inline in the decision body, two fenced blocks (resolved §4.2):** the check
  (usually one line — often a pointer to an *existing* test / lint rule / grep, not a
  bespoke script) lives in a ` ```check ` block; the counterexample in a
  ` ```counterexample path=<rel> ` block, **both inside the decision `.md` body**. Two
  payoffs: (a) no repo bloat — one file per decision, and only tier-1 decisions carry a
  check at all; (b) the body fingerprint (§3) locks **both** the check and the
  counterexample for free — no second hash. **This slice is inline-only**; spill-to-fixture
  is deferred (it would move the counterexample out of the body-hash's reach → a weaker,
  rot-able lock that needs its own digest mechanism — §4.2, and a markdown block has no
  size limit, so "too large to inline" is aesthetic, not a capability wall).
- **Anti-hollow ("show it biting"):** an always-passing check is worthless. Require
  the AI to supply a **counterexample** the check **demonstrably fails on**. At ratify
  time the tool runs the **two-sided bite-test** (§4.2): check **passes on current code**
  **and** **fails with the counterexample materialized** at its declared path. A check
  that can't be shown to bite is rejected — it cannot become a hard anchor. The
  counterexample is a **(relative path, content) pair**, not a bare snippet — placement
  must be deterministic since an opaque check command's scan scope can't be inferred.
  *Worked example* — decision "passwords never stored with MD5"; check
  `! grep -rIn "md5(.*password" src/`; counterexample `path=src/auth/legacy.py`,
  content `pw = md5(user.password)`. Ratify runs the check on real code (must pass) **and**
  in a sandbox with that file injected (must fail) — proving the check catches the thing
  the decision forbids. **The two-sided test self-detects counterexample pollution**: if a
  check's scope is so wide it scans the inline counterexample in the `.md` itself (e.g.
  `grep . ` instead of `grep src/`), the **pass side fails at ratify** → rejected with
  "scope your check or relocate the counterexample" — no need to parse the opaque command.
- **Run + gate (two layers, §4.1):** `decision check` runs each decision's check;
  non-zero → block. Run *locally* by the agent for fast feedback; run in *CI* as the
  hard, un-bypassable gate.
- **The checkability tiers** (umbrella §12.3) — this slice implements the hard rung
  and the "context" floor; the middle (soft) rung is the deferred review:
  1. **runnable check** → **hard anchor**, can block merge. *(this slice)*
  2. acceptance criterion, no automatable check → **reviewable anchor**, judged by the
     independent re-review against the criterion. *(deferred — the soft part)*
  3. nothing checkable → **recorded as context**, surfaced but **never gates** — a
     wish, not a contract. *(this slice: just the "never gates" classification)*
- **Watch the ratio** (§12.3, honest): if most decisions fall to tier 3 (context),
  the system has quietly gone advisory. The tool should **report the hard:context
  ratio** so this is visible, not hidden.

### 4.1 Where `decision check` runs — local sensor first, CI gate as backstop

The CLI is **AI-friendly by design**: any code agent with shell access calls it
directly, mid-run. So `decision check` is **first a local sensor the agent consults
throughout its work**, and only secondarily a CI gate. Three layers, sorted by hardness:

| Layer | Mechanism | This slice? |
|---|---|---|
| **CI hard gate** | CI runs full `decision check` before merge — un-bypassable floor | **Yes** |
| **Agent self-check (portable)** | CLI is present; the project's agent instructions (AGENTS.md / skill / CLAUDE.md) tell it to run `decision check` at natural checkpoints (chunk done, pre-commit) | **Yes** — works for *any* agent, zero per-harness integration |
| **Hook auto-fire** | Claude-Code PostToolUse runs it automatically after edits | **No** — this is the already-deferred slice (§5, slice-1 §9(c)); Claude-Code-only, fail-open |

**Why the local layer is allowed to be soft.** It relies on the agent *choosing* to run
it — bypassable, exactly the soft-rail class §7 documents. That's **fine by design**: its
job is not to *guarantee*, but to let a cooperative (fallible-not-adversarial) agent
**turn around cheaply** the moment it drifts. The guarantee lives in CI. This matches §6:
decisive against the common cooperative agent; the determined one is caught by the CI
floor anyway. So "the local check can be skipped" is the correct outcome of layering, not
a hole.

**Cost — `decision check --changed`.** Two cost halves: hash compares (near-free) and
running executable checks (can be slow). For high-frequency local use, `--changed` runs
**only** the checks whose anchored files moved (git diff × the slice-1 anchor map) — sub-
second feedback at each checkpoint. CI runs the **full** set so nothing is missed.

### 4.2 The counterexample mechanism — resolved (brainstorm 2026-06-16)

The one open design hole (per the Tool A plan's closing section): *a check is repo-level
(`! grep -rIn "md5(.*password" src/`) but a counterexample is an isolated bad snippet —
how does a repo-wide check get shown to "bite" a hypothetical snippet?* Resolved below.

**The real crux is pollution, not injection.** A counterexample is by construction a thing
the check fails on. If it persists anywhere the check's own scope reaches, every *real*
`decision check` finds it and fails forever. The inline counterexample lives in the `.md`
under `docs/decisions/`; a narrowly-scoped check (`grep … src/`) never sees it, but a
whole-repo check (`grep … .`) would. So the mechanism must guarantee the counterexample is
seen by the check **only transiently at ratify, in isolation** — and do so for an *opaque*
command whose scan scope we cannot parse.

**Resolution — a two-sided bite-test in a temp sandbox (call sites stay trivial):**

1. **Two asymmetric sides — pass on the real tree, bite in a sandbox.**
   - **Pass side:** run the check against the **real working tree** (`cwd=repo_root`,
     **read-only — no copy**) → must exit 0. This is *identical to a normal
     `decision check` run* of that check, so the runner is shared. Running on the real
     tree is what makes pollution self-detection work (below): the real tree contains the
     decision `.md`, so an over-wide check scans its own inline counterexample.
   - **Bite side:** build a temp sandbox = copy the **in-scope** working tree
     (`source_scope` include set; **not** `.git` / `.venv` / `node_modules`) into a temp
     dir, then materialize the counterexample at its declared relative path → run the check
     (`cwd=sandbox`) → must exit non-zero. Sandbox discarded after. On APFS `shutil.copy2`
     uses `fcopyfile` (COW) — a free speedup; correctness does not depend on it.
   - Both sides required. The **real working tree is never mutated** — a crash can never
     leave a poison snippet in `src/`, and concurrent reads never see a dirty tree (the
     rejected `real-tree-write-then-revert` alternative fails on both).
2. **Self-detecting pollution (no command parsing).** If a check's scope is wide enough to
   scan the inline counterexample in the `.md` (it lives under `docs/`, which the *default*
   `source_scope` excludes — so a scoped `src/` check never sees it, but a whole-repo
   `grep … .` does), its **pass side fails at ratify** (the real tree holds the `.md`) →
   ratify rejects with "scope your check or relocate the counterexample". The contract
   catches over-wide scope for free, without parsing the opaque command.
3. **Sandbox is ratify-only — never on the agent hot path.** The frequent
   `decision check [--changed]` an agent runs mid-work executes checks **directly against
   the live tree, read-only, zero copy**. The sandbox copy is paid once per decision
   birth/ratify (human moment, or agent `--dry-run` self-test before proposing) — invisible
   to the work loop.

**Architecture (resolved: Y — keep the pure layer pure).** `core/decision_check.py::
run_check` stays **pure** (referential integrity + Tool A hash integrity; all existing
tests untouched). A new `core/check_runner.py` owns the impure execution (subprocess,
timeout, sandbox, `--changed` scoping). `cli/decision.py::check_cmd` **composes both into
one `decision check`** — one command for all three callers (agent / human / CI). This maps
cleanly to *structural integrity* (pure) vs *behavioral conformance* (impure).

**Run semantics.** `sh -c <command>`, `cwd=repo_root` (real runs) / sandbox root
(bite-test), `timeout=30s` (per-check override deferred), `capture_output`. Exit 0 =
satisfied; **non-zero — including timeout, command-not-found, or a broken check — is
fail-closed → block** (`EXIT_VALIDATION=2`, same code as integrity/dangling-up, so the
agent's "non-zero = I broke something" model holds). Checks **should** be read-only;
this is **recommended, not enforced** — a mutating check is the author's risk (bedrock
ceiling §6: no sandbox isolation, only the timeout).

**Three callers, one command, scope via `--changed`:**

| Caller | Command | Scope | Sandbox? |
|---|---|---|---|
| code agent (mid-work, high-freq) | `decision check --changed --json` | only checks whose anchored files moved | no |
| human (manual harness check) | `decision check` | full | no |
| CI (un-bypassable gate) | `decision check` | full | no |

The **correctness default is full** (`--changed` is opt-in) — never default the guarantee
to a narrow subset, or CI could silently under-check.

**`--changed` baseline.** Working tree vs `HEAD` ∪ untracked-not-ignored
(`git diff --name-only HEAD` plus untracked) — "what I'm working on now", matching the
pre-commit checkpoint. Mapped to checks via the slice-1 anchor map (id → anchored files);
a tier-1 decision runs iff one of its anchored files is in the changed set.
**Honest limit:** `--changed` scopes by *anchor* files, but a check's real scan scope can
be wider than its anchors (`grep src/` vs a single anchored file) → `--changed` **can
miss**. It is a local speed heuristic; CI's full run is the guarantee. Do not mistake it
for soundness. (`--changed-since <ref>` override deferred.)

**hard:context ratio report.** Over **ratified** decisions only: `check` block present =
hard (tier-1), absent (incl. legacy/unhashed) = context (tier-3); proposed/superseded/
retired excluded. Pure counting (no subprocess) → **always computed and shown**, even
under `--changed`. Text: one line `hard:context = 3:7 (30% hard)`; JSON: `{"hard": 3,
"context": 7}`. **Not a gate** — visibility only (§6: keeps "how much is still un-welded"
in plain sight; never affects exit code).

**Counterexample shape & cardinality.** A `(path, content)` pair. This slice: **at most one
`check` and one `counterexample` per decision**; a `check` with no `counterexample` is
rejected at ratify (can't show biting). Inline-only (above); multi-file / binary / large
counterexamples → fixture, deferred.

- **Reviewable anchors (tier 2)** — the independent re-review against a written
  acceptance criterion, for decisions too prose-y for a runnable check.
- **The change → route-to-review trigger** — anchored code changed → route it into the
  review that must happen anyway + leave a trail. Attention routing, not detection
  (§12.4: the trigger is a mechanical signal the AI cannot control).
- **Region framing** (paired `@decision:…end` vs AST) — only earns its keep with the
  review; ships with it. *(Insight: since the verdict is inferential, a coarse
  "anchor neighbourhood changed" trigger may beat precise region-framing.)*
- **Edit-time PreToolUse feedforward reminders** — separate slice (slice-1 §9 (c)),
  fail-open, Claude-Code-only; explicitly not here.

No bespoke "semantic detection engine" is on any roadmap — it can't detect. The soft
direction is served by executable checks where possible (this slice) + forced
review-and-trail where not (deferred).

## 6. Honest limits (bedrock ceiling)

- **Text-lock:** the fingerprint lives in the same file as the body; an AI that in one
  edit also rewrites the stored hash (and forges identity/time) gets past it. Bedrock
  solo-owner ceiling — anything in an owner-controlled repo is forgeable. What it
  *does* weld: a casual body edit can't silently silence the lock; the only legal
  silencer is an attributed `ratify` whose changes all land in the git diff.
- **Executable check:** the check + counterexample are **AI-written** — they can be
  weak (pass on real violations the AI didn't think of). "Show it biting" raises the
  cost and is runnable/reviewable, but a determined AI can craft a shallow check. Real
  but bounded teeth — certain *against what the check actually tests*, no more. **No real
  sandbox here:** CI executes AI-written check code, but the solo owner can already run
  anything in their own CI — a sandbox wouldn't change the bedrock ceiling. This slice
  adds only a timeout; container/resource isolation is a separate future item, not a
  prerequisite.
- **CI gate** is only as hard as branch protection, which the solo owner controls.
- **Local sensor (§4.1)** is best-effort by design — a determined agent can skip it. This
  is not a regression: it's the early-warning layer for cooperative agents; the CI gate
  is the guarantee. Stated here so "the local check is skippable" isn't mistaken for a
  hole in the teeth.

**Unifying stance (the thesis under both tools):** the semantic layer — "does this
*mean* what the decision requires" — can never be *guaranteed*; the honest program is
to **approach** it with one lever in two moves. (1) **Squeeze as much of the semantic
question as possible into a mechanical fact**: the text-lock turns "was the decision
altered" into a hash compare; the executable check turns the checkable part of "does
code honor the decision" into an exit code. Every piece squeezed out stops being
*approached* and becomes *guaranteed*. (2) **Fence the irreducible core** (pure intent
— "why X", "be elegant") that won't squeeze: counterexample + independent re-review
raise the cost of faking, everything is trailed, the human is pulled back to judge.
Note the executable check is only *mostly* hard — its **run** is deterministic, but
**whether the check is faithful to the decision** is a one-time human semantic
judgement at ratify time (a soft root, pushed forward and frozen, not eliminated).

**Leaving an irreducible human-judged core is the premise, not a bug:** if machines
could guarantee semantics, the human — and this tool — would be unnecessary. The job is
to *shrink the surface the human must mind* so limited judgement lands where only a
human can judge. And **guard against "approaching" decaying into "pretending to have
arrived"**: a thin hard tier with most decisions in the soft/context bucket looks
rigorous but is rubber-stamp theater — hence the **hard:context ratio report** keeps
"how much is still un-welded" in plain sight.

Positioning matches the umbrella: **raise the floor, make laziness/drift impossible to
do silently, leave a trail.** Decisive vs a fallible-not-adversarial agent;
cost-raising-and-visible vs a determined one.

## 7. External grounding (verified this session)

- **Agents bypass soft rails — in the wild.** Documented coding agents using
  `--no-verify` / `git stash` / quiet flags to bypass hooks and ignore CLAUDE.md →
  prompts can't be load-bearing; the hard gate must be CI-side (both tools here gate in
  CI, immune to the `--no-verify` class). [pydevtools; tupe12334/block-no-verify;
  TheLinuxCode 2026; DEV "Branch Protection vs Rulesets"]
- **Executable-spec / fitness-function is the mature top rung** for code-conformance:
  oasdiff (OpenAPI, exit-code gate), buf (protobuf), Atlas (schema), ArchUnit (arch
  rules as tests) — "the check *is* the artifact, drift can't exist". Tool B is this
  pattern, attached to a human decision. (umbrella §13.1 ARM-1 rung-1)
- **The non-checkable residue is human-only** — design rationale must be human-recreated
  (Su 2026; Robillard ESEC/FSE 2021; van Heesch & Avgeriou) → confirms the soft residue
  has no mechanical closure; defer it as review-and-trail, not a detector.
- **Region framing has no silver bullet** (Java `@snippet` paired markers — "adopt
  late"; drift-vscode AST, symbol-adjacent only; whole-file diff) → 4b-grade, kept out.

## 8. Build-time (next, not designed here)

- writing-plans → TDD → subagent-driven-development.
- Files in scope (first pass): `core/decisions.py` (add `ratified_text_hash` + inline
  check/counterexample fields; body-normalize + fingerprint), `cli/decision.py` (ratify
  computes hash, registers + bite-tests the check), `core/decision_check.py`
  (`integrity_violations`; run executable checks; `--changed` scoping; hard:context ratio
  report).
- **Resolved 2026-06-15:** check-storage shape → **inline in the decision body** (locked
  by the body hash, no sidecar/second hash). `decision check` → **local-sensor-first + CI
  backstop**, with `--changed` for cheap local runs. Existing-decision migration →
  **lazy-warn** (no hash/check → warn, fill in on the next natural `ratify`).
- **Resolved 2026-06-16 (§4.2 — the last open hole):** counterexample = `(path, content)`
  pair; storage = two fenced body blocks (` ```check ` / ` ```counterexample path= `);
  bite-test = **two-sided test in a temp sandbox** (clonefile on APFS), self-detecting
  pollution; architecture = **Y** (`run_check` stays pure, new `core/check_runner.py` for
  execution, CLI composes one `decision check`); default full, `--changed` opt-in (base =
  working tree vs HEAD ∪ untracked); run via `sh -c`, `timeout=30s`, non-zero fail-closed
  → exit 2; ratio report over ratified (always shown, not a gate); `--dry-run` agent
  self-test. **Inline-only**; fixture spill (+ its weaker lock / digest mechanism) deferred.
- Tool B build-time file map (first pass): `core/decisions.py` (parse the two body blocks →
  `check` / `counterexample` fields), `core/check_runner.py` (new — sandbox bite-test +
  live-tree run + `--changed`), `cli/decision.py` (`ratify` runs bite-test + `--dry-run`;
  `check` composes + ratio + new JSON keys), `core/decision_check.py` (unchanged — stays
  pure).
- Sandbox is **not** resource-isolated (solo-owner bedrock: owner can run anything in CI
  anyway), only a timeout + honest limit §6; the temp sandbox exists for *correctness*
  (never mutate the real tree), not security. Container/resource isolation is future.
- Dogfood the full lifecycle on the branch before the PR (project discipline).
