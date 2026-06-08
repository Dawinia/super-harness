# Design: Decision records + rooted code anchors + mechanical dangling checks

Date: 2026-06-08
Status: spec (brainstorm-converged; revised after round-1 review). First buildable
slice of the decision-conformance design
(`2026-06-05-decision-conformance-harness-design.md`, the umbrella SSOT). This
slice is **complete within its functional scope** — not an MVP half-measure; the
things it defers are *different functional scopes* (§9), each to be done completely
in its own slice.

> NOTE: like the sibling design docs in this folder, this file deliberately
> carries **NO** `change:` / `stage:` frontmatter. The repo self-hosts on the
> SuperpowersAdapter, which discovers changes by that frontmatter in
> `docs/plans/`. With it present, the next `adapter scan-once` would auto-emit
> `intent_declared` / `plan_ready` for this doc. It stays an inert design
> artifact until `change start` is run explicitly.

---

## 1. What this slice is (and what it is deliberately not)

This is a **generic CLI capability**. The tool operates on whatever repository it
is pointed at; this repo is merely one such repository (used for validation, §8).
No design choice here is motivated by "this repo happens to have problem X."

**In scope — the `code ↔ decision` link, end to end:**

1. A **decision** becomes a first-class, ID'd, human-ratified record on disk.
2. A code **anchor** (`@decision:<id>`) declares "this code implements decision
   `<id>`", rooted in a ratified decision record.
3. A **mechanical dangling check** (`decision check`) for CI: blocks when an anchor
   points at no ratified decision (**dangling up**); warns when a ratified decision
   has no code anchor (**dangling down**).
4. The full CLI verb set: new / ratify / supersede / retire / list / show / check.
5. **Scan-scope infrastructure** the check requires: a loader for
   `.harness/source-paths.yaml` and `include`/`exclude` + `keyword` support on the
   shared scanner (none of which exists today — §3.2). Additive and
   backward-compatible, so the still-running `@capability` machinery is untouched.
6. A CI workflow step (added to the bundled `init` template) that runs the check on
   PRs, plus the same wired into this repo's own CI for self-host.

**Explicitly NOT in scope** (separate functional scopes, §9 — deferred *by
boundary*, not by laziness):

- The `doc ↔ decision` link (doc anchors + regen-and-diff ground-truth checker).
- Retiring the existing `@capability` machinery + migrating its 34 sentinels — a
  deep refactor of lifecycle plumbing (reducer/state/adapter); its own next slice.
- The edit-time PreToolUse hook (feedforward soft rail).
- The integrity-lock / betrayal escalation (the "teeth"): change-detection,
  re-check, and the re-ratification lock on edited decisions.
- The **checkability tier** of umbrella §12.3 (a decision carrying an executable
  check → "hard anchor"; checkless → "context"). This slice gates only on
  *referential integrity*, never on *satisfaction* — see §2.4.
- AI-proposes-decisions (tied to spec authoring, which this slice does not build).

This slice introduces **only** the `@decision:` keyword and its records/check. The
existing `@capability:` system is **left untouched and running**; the two keywords
coexist briefly until the next slice retires `@capability`.

## 2. The decision record

### 2.1 Storage & format

- **One file per decision**: `docs/decisions/<id>.md`.
- **Markdown + YAML frontmatter.** Frontmatter carries machine fields; the body
  carries the one-line decision + optional rationale.
- The filename stem MUST equal the frontmatter `id` (`docs/decisions/d-x.md` ⇒
  `id: d-x`); a mismatch is a malformed record (§4.4, fail-closed).
- Rationale for one-file-per-decision (not a single registry): human-reviewable in
  PR diffs, git-attributable per decision, room for rationale, scales without merge
  conflicts. Mirrors the repo's "source-of-truth files + generated index" shape.
- This is *not* an ADR (a write-once historical journal) and *not* a design doc
  (prose narrative). It is a **live, machine-checked contract**: code points at it
  by ID; CI verifies the link on every run. It borrows ADR's *form* (one record =
  one decision, stable ID) and adds *mechanical enforcement + live linkage*.

Example:

```markdown
docs/decisions/d-auth-stateless.md
---
id: d-auth-stateless
status: ratified
ratified_by: dawinialo@gmail.com
ratified_at: 2026-06-08T12:00:00Z
---
Authentication must be stateless (JWT, no server-side sessions).

(optional rationale paragraphs below)
```

### 2.2 Fields

| Field | When present | Meaning |
|-------|--------------|---------|
| `id` | always | stable slug, the join key; charset `[a-z0-9_-]+` (lowercase only, §4.4) |
| `status` | always | `proposed` \| `ratified` \| `superseded` \| `retired` |
| `ratified_by` | after ratify | identity (from `core/identity.py`) that ratified |
| `ratified_at` | after ratify | UTC `Z`-stamp (from `core/clock.py::utc_now_iso`) |
| `supersedes` | on a successor | the `id` this decision replaces |
| `superseded_by` | on a superseded one | the `id` that replaced it |
| (body) | recommended | one-line decision + optional rationale |

The body is **not read by the check** (§4 reads only frontmatter); an empty body
passes the gate but is discouraged. Unknown extra frontmatter keys are allowed
(forward-compat).

### 2.3 Lifecycle & the "ratification line"

```
            decision new            decision ratify           decision supersede --by <succ>
   (none) ───────────────▶ proposed ───────────────▶ ratified ──────────────────────────────▶ superseded
                              │                          │
                              │ (edit file freely)       │ decision retire
                              ▼                          ▼
                         (still proposed)             retired
```

The dividing line is **ratification**, and it is *mechanically detectable* (the
`status` field) — no semantic judgment needed:

- **Before ratification (`proposed`)**: edit the file freely. Same decision still
  being shaped; nothing anchors to it (proposed is not anchorable), so editing
  harms nothing. The file's git history is the drafting trail.
- **After ratification (`ratified`)**: the contract is frozen. Any *change of the
  decision* is a **follow-up** — a new decision (new `id`) that supersedes the old.
  You do not rewrite a ratified decision in place to mean something different; that
  would silently betray every anchor pointing at it. (Mechanically *welding* this —
  detecting an in-place edit of ratified text — is the deferred integrity-lock,
  §9; this slice states the rule and supports supersede, but does not police edits.)

`superseded` and `retired` are both terminal, non-anchorable states; the difference
is whether a successor exists (supersede) or not (retire — a tombstone for a removed
capability).

### 2.4 Anchorability rule — and why this does NOT contradict umbrella §12.3

**Only `ratified` decisions are valid anchor targets.** Anchoring to a `proposed`,
`superseded`, `retired`, or nonexistent `id` is a dangling-up failure (§4).

Umbrella §12.3 says "no check → no hard anchor": only a decision carrying an
executable check may *hard-gate*; checkless decisions are context, never gate. This
slice appears to let any ratified decision block (via dangling-up) without a check —
but there is no contradiction, because the two gate on **different things**:

- §12.3 governs gating on **satisfaction** — "does the code actually obey decision
  D?" That is inferential and requires D to carry a check. **This slice never checks
  satisfaction.**
- This slice's dangling-up gate is pure **referential integrity** — "the `id` this
  anchor names exists and is ratified." That is structural, deterministic, and safe
  to block on regardless of whether D is checkable.

The §12.3 checkability tier (which decisions may hard-gate on satisfaction) is
deferred with the integrity-lock / executable-check scope (§9). Citing §12.3 here
so the divergence is explicit, not silent.

## 3. The code anchor

### 3.1 Syntax & meaning

- Syntax: **`@decision:<id>`** in source. (The umbrella wrote `@implements:`;
  `@decision:` is chosen as neutral across code and the future doc leg.)
- **Anchor capture is case-preserving and permissive — NOT the lowercase record-id
  charset.** Record ids are lowercase (§4.4), but the scanner captures
  `@decision:([A-Za-z0-9_-]+)` (the existing scanner's mixed-case charset; the
  keyword itself matched literally / `re.escape`d). Rationale: a mistyped/uppercased
  anchor like `@decision:D-Auth` must be *captured* so it lands in `A − R` and
  **fails as dangling-up** — a lowercase-only capture would make it invisible
  (fail-open), the very hole §4.4 warns about. Case-mismatched anchors are a
  dangling-up failure, never silently dropped.
- Meaning: "this code implements ratified decision `<id>`." It re-roots the old
  free-floating label onto a human-ratified record — that re-rooting is the point.
- **The scanner is a raw substring matcher, not comment-aware.** It matches
  `@decision:<id>` *anywhere* in an included file's text (the existing
  `_SENTINEL_RE` does a plain regex over file text — building language-aware comment
  parsing is out of scope). Placing anchors in comments is convention (keeps them
  inert), but the scanner counts any occurrence. Consequences are handled by scope
  (§3.2): records and design docs that contain the syntax in prose are excluded.
- **Granularity is irrelevant in this slice.** The check only asks "does a
  `@decision:<id>` occurrence exist in scanned source?" — function vs. region
  markers only matter for the deferred content-change/re-check signal.
- A single code site MAY carry multiple anchors; a single decision MAY be anchored
  at many sites (the many-to-many web). This slice handles that with no special
  logic — it only checks existence, unaffected by multiplicity.

### 3.2 Scan scope (the part the round-1 review caught as unbuilt)

Today `source-paths.yaml` is **written but never read**, and `anchor_scanner.py`
has **no exclude support** and is **hard-coded to `@capability:`**. So this slice
must *build* the scope machinery (it is not a pre-existing reuse):

- **Build a loader** for `.harness/source-paths.yaml`. Keys are nested under a
  top-level `source_paths:` mapping (verified shape):
  `data["source_paths"]["include"]` / `["exclude"]` (glob lists). If the file or a
  key is absent, apply the default: include `['**/*']`, exclude `['docs/**']`. Pass
  the literal `**/*` / `**` sentinels through unchanged — do **not** normalize them;
  the include match-all short-circuit depends on them.
- **Add `exclude_globs` and `keyword` parameters** to `scan_sentinels` /
  `scan_sentinel_locations`, both **backward-compatible** (defaults reproduce
  today's `@capability:`, no-exclude behavior, so the running capability machinery
  is unaffected — honoring the scanner's own "two scanners cannot drift" invariant
  by parametrizing the one scanner, not forking it).
- **Scanned set** = `git ls-files` ∩ `include` ∖ `exclude`, computed as: a file is
  *included* if it matches any `include` glob via the existing `_matches_any` (which
  short-circuits the `**/*` / `**` match-all sentinels — why the default include
  works); it is then *dropped* if it matches any `exclude` glob via plain per-pattern
  `fnmatch` (stdlib `fnmatch`'s `*` crosses `/`, so `docs/**` matches
  `docs/decisions/d-x.md`).
- **Hard rule, applied as a separate layer AFTER include∖exclude: `docs/decisions/**`
  is ALWAYS excluded** from anchor scanning — decision records are never anchor
  sources, even if an adopter's `source-paths.yaml` includes `docs/`. This stops the
  check from matching the `@decision:` strings inside the records' own bodies and
  inside this spec. Test vector: with include `['docs/**']`, `docs/decisions/d-x.md`
  is still excluded.
- The default `exclude: docs/**` keeps design docs (which contain the syntax in
  prose/examples) out. Adopters who keep literal `@decision:` strings in scanned
  non-implementation files (e.g. test fixtures) should add their own exclude — an
  honest limitation of a non-comment-aware matcher, documented for adopters.

## 4. The mechanical dangling checks (`decision check`)

A **whole-repo invariant scan of the tracked files in the working tree**
(`git ls-files`; not diff-based — "every anchor has a root" and "every ratified
decision has code" are global properties). Pure, deterministic.

### 4.1 Procedure

1. Enumerate decision records (§4.4) under `docs/decisions/`. A malformed record or
   a duplicate `id` is a **hard error** (§4.3, §4.4).
2. `R` = the set of `ratified` decision IDs.
3. Scan source (§3.2) for `@decision:<id>` anchors → `A` (ids, with file+line).
4. **Dangling up** = `A − R` — anchors pointing at a non-ratified or nonexistent
   decision. **Block.** Unambiguous and wrong at any instant.
5. **Dangling down** = `R − ids(A)` — ratified decisions with no code anchor.
   **Warn** (does not fail). A decision ratified in one PR with code arriving in a
   later PR is a legitimate transient; making the only-warnable thing block is the
   fastest way to get the whole gate disabled. (Per-decision escalation to block is
   a deferred knob, §9.)

`superseded` / `retired` / `proposed` decisions are **not** counted as dangling
down (not expected to have live code). Anchors pointing at them fall into dangling
up (not in `R`).

**Precedence:** a record/config error (exit `3`) is detected in step 1 and
**dominates** — if records cannot be parsed, `R` is unreliable, so the check fails
with exit `3` without reporting dangling results.

### 4.2 Output & exit codes

Exit codes (per the repo's global convention in `src/super_harness/exit_codes.py`):

| Code | Constant (`exit_codes.py`) | Meaning |
|------|---------|---------|
| `0` | `EXIT_OK` | clean, or only dangling-down warnings |
| `2` | `EXIT_VALIDATION` | one or more **dangling-up** anchors (the merge-blocking gate violation) |
| `3` | `EXIT_NO_CONFIG` | **record/config error** — duplicate `id` or malformed record (fail-closed; same class as "yaml corrupt") |

These follow the repo's **global** exit-code convention
(`src/super_harness/exit_codes.py`, cli-command-surface §2.2) and match the sibling
gate `attest verify` (0 pass / 2 block / 3 no-or-corrupt config). `1`
(`EXIT_GENERIC`) is reserved for an uncaught internal failure. Errors are rendered
via `cli/errors.py::format_error`.

`--json` is the **global** flag (`ctx.obj["json"]`, per `cli-command-surface §3.4`)
and emits the repo's **frozen 6-key envelope** (`cli/output.py::json_envelope`) —
NOT a bespoke shape — so CI parsers that know super-harness keep working:

```json
{
  "command": "decision check",
  "version": "0.1.0",
  "status": "pass | warning | fail",
  "exit_code": 0,
  "data": {
    "dangling_up":   [{"id": "...", "file": "...", "line": 12}],
    "dangling_down": ["<id>", "..."]
  },
  "errors": [{"code": "duplicate_id | malformed", "message": "...", "file": "..."}]
}
```

Status rollup (`output.py` semantics): `fail` if any dangling-up or record error;
else `warning` if any dangling-down; else `pass`. `exit_code` mirrors §4.2 (0/2/3).
`data.dangling_up` is sorted by (id, file, line); `data.dangling_down` sorted by id —
deterministic for golden tests.

### 4.3 Duplicate-id rule

Two records resolving to the same `id` — including ids equal under **case folding**
(`d-auth` vs `d-Auth`, which collide as one file on case-insensitive filesystems) —
is a duplicate-id error (exit `3`). `decision new` also refuses a case-folded
collision at creation time.

### 4.4 What counts as a decision record, and what "malformed" means

- **Candidate files**: `docs/decisions/*.md`, EXCLUDING `README.md` and any file
  whose basename starts with `_` or `.` (these are reserved non-records, e.g.
  `_template.md`). A stray non-record `.md` that is *not* so named is treated as a
  candidate and will fail validation — name templates/readmes accordingly.
- A candidate is a **valid record** iff ALL hold; otherwise it is **malformed →
  exit `3`** with a per-file error:
  - a YAML frontmatter block fenced by `---` that parses to a mapping;
  - `id` present, non-empty, matching `[a-z0-9_-]+`;
  - filename stem == `id`;
  - `status` present and one of the four enum values (an out-of-enum `status:` is
    malformed).
- Fail-closed (never silently skip): a silently-dropped record could remove an id
  from `R` and mask a real dangling-up.

## 5. Edge cases

| Case | Disposition |
|------|-------------|
| Anchor's decision file deleted | anchor now in `A − R` → dangling up → **block** |
| One code site, many `@decision:` anchors | allowed, each checked independently |
| One decision anchored at many sites | allowed; dangling-down satisfied by ≥1 anchor |
| Ratified decision, zero anchors | dangling down → **warn** |
| Anchor → `proposed`/`superseded`/`retired` id | dangling up → **block** (only ratified anchorable) |
| `supersede --by <new>` (the combined post-state) | old → `superseded` (its anchors become dangling **up** → block until re-anchored); `<new>` starts dangling **down** (warn) until code is re-anchored to it. Both behaviors hold simultaneously. |
| `retire` leaves live anchors behind | those anchors become dangling up → **block** until cleaned |
| Anchor written before its decision is ratified | dangling up → **block** until ratified ("ratify before merge") |
| Empty repo (no `docs/decisions/`, no anchors) | check passes clean (exit `0`), no error |
| Duplicate `id` (incl. case-folded) | hard error → exit `3` (§4.3) |
| Malformed record / filename≠id / bad frontmatter / out-of-enum status | fail-closed → exit `3` (§4.4) |
| Stray `README.md` / `_template.md` in `docs/decisions/` | ignored (reserved, §4.4) |
| Ratified record with empty body | passes (check reads only frontmatter); discouraged |
| `decision new <id>` where file already exists | command refuses (no overwrite) |
| `supersede --by <new>` where `<new>` missing/unratified | command refuses |
| supersede cycle (a↔b) | not detected this slice (pathological); documented, deferred |

## 6. CLI surface (new `decision` group)

Each verb does exactly one thing (no hidden cross-entity side effects):

- **`decision new <id> --text "<one-line decision>"`** — create a `proposed`
  record at `docs/decisions/<id>.md`. Refuses if the file (or a case-folded
  sibling) already exists.
- **`decision ratify <id>`** — `proposed → ratified`; stamp `ratified_by`
  (`core/identity.py`) + `ratified_at` (`core/clock.py::utc_now_iso`). Ratifies
  *only* this record — no side effects on others.
- **`decision supersede <old-id> --by <new-id>`** — requires `<new-id>` already
  `ratified`; flips `<old-id>` to `superseded` and writes the bidirectional
  `supersedes` / `superseded_by` links. The follow-up handover is explicit here
  (not a side effect of `ratify`), and supports linking a supersession discovered
  after the fact.
- **`decision retire <id>`** — `→ retired`; a tombstone (kept for audit), not
  anchorable, and **not** counted as dangling down (intentional removal).
- **`decision list [--status <s>] [--dangling]`** — list decisions; `--dangling`
  shows the dangling-down set.
- **`decision show <id>`** — one decision's fields + the code anchors currently
  pointing at it (via `scan_sentinel_locations` with `keyword="@decision:"`, §3.2).
- **`decision check [--json]`** — the CI gate (§4).

Hand-editing a `proposed` record's file is allowed (it is just a file); the CLI is
the blessed, deterministic path (and the only one that auto-stamps identity/time).

## 7. CI wiring

What the **tool** provides (the spec's content):

- The `decision check` command with the exit semantics of §4.2.
- **Two distinct artifacts** (the round-1 review caught these being conflated):
  1. **Adopter-facing**: add a `decision check` step to the bundled
     `init` workflow template (`src/super_harness/templates/super_harness_workflow.yml`,
     loaded by `cli/init.py::_workflow_template`), so any repo that runs `init`
     gets it on `pull_request`.
  2. **Self-host**: wire the same into this repo's own CI (a dedicated
     `decision-check.yml`, mirroring the existing repo-internal `cli-reference-drift`
     job shape) so it runs on our PRs.
- Documentation stating the adopter must mark the check **required** via branch
  protection for it to actually block.

The honest boundary (umbrella §7.3, and `project-bedrock-solo-owner`): **a CI gate
is only as hard as branch protection, which the repo owner controls.** The tool
cannot make itself required on someone's repo; it ships the step and documents the
act. (A future `decision check` could warn when it detects it is not a required
check, but that needs the GitHub API — deferred, §9.)

## 8. Validation (this repo, as a generic instance)

Prove the slice end to end on a real repository: author a few ratified decisions,
anchor real source at them, confirm `decision check` reports up/down correctly and
the CI step blocks a deliberately-broken anchor. The existing 34 `@capability:`
sentinels are **not** migrated here — that belongs to the next slice. Enabling
branch protection + marking the check required on this repo is a validation step (it
may hit the account/token constraints noted in memory; handle then), not part of the
spec.

## 9. Deferred (each a separate functional scope) + honest limits

Registered in `private/OPEN-ITEMS.md` (umbrella item #7):

- **Doc ↔ decision leg** — doc anchors + regen-and-diff ground-truth checker.
- **Capability retirement + migration** — remove `affected_anchors` from
  reducer/state/adapter/pr_metadata, delete/replace the old anchor sensors, migrate
  the 34 sentinels. Cross-slice keyword contract: the migration MUST adopt
  **`@decision:`** (not introduce `@implements:`, not retain `@capability:`) — so
  three keywords never coexist past that slice.
- **Edit-time PreToolUse hook** — the feedforward soft rail.
- **Integrity-lock / betrayal escalation** — change-detection of anchored code, the
  re-check, and the re-ratification lock that welds "ratified is frozen."
- **Checkability tier (umbrella §12.3)** — executable-check-per-decision and the
  hard-anchor-vs-context distinction; gating on *satisfaction* (this slice gates
  only on *referential integrity*, §2.4).
- **Dangling-down → block** — per-decision escalation knob.
- **AI-proposes-decisions** — tied to spec authoring.
- **Advisory-when-not-required detection** — needs GitHub API.
- **Persisted `decision ↔ code` index** + **supersede cycle detection** — not
  load-bearing for the check; add if a consumer (e.g. the hook) needs them.
- **Comment-aware / string-literal-aware scanning** — the matcher is raw-substring
  (§3.1); language-aware parsing is a separate effort.

Honest limits of what this slice mechanically guarantees:

- `ratified_by` is **self-asserted** (git email); a solo owner can set it freely.
  This slice *records* attribution, it does not *enforce* human-ness — bedrock
  ceiling.
- **In-place editing of a ratified decision's text is not policed here** — the
  deferred integrity-lock's job. The rule is stated and structurally supported
  (supersede), not yet mechanically welded.
- The check verifies **structure** — that an anchor's `id` resolves to a ratified
  decision, that no decision dangles — never **semantics** (whether the anchored
  code actually implements the decision).
- The CI rail is only as hard as branch protection (owner-controlled).

## 10. One-line summary

> A decision becomes an ID'd, human-ratified record on disk; code anchors
> (`@decision:<id>`) root in those records; a deterministic CI check blocks anchors
> that point at no ratified decision and warns about ratified decisions with no
> code. Pure, mechanical, whole-repo. It guards referential integrity, not
> semantics; it is only as hard as branch protection — and it is the first
> complete, buildable link of the decision-conformance chain.
