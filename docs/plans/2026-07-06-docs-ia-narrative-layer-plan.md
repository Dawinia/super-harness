---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: 2026-07-06-docs-ia-narrative-layer
stage: plan
scope:
  files:
    - README.md
    - docs/README.md
    - docs/overview.md
    - docs/concepts.md
    - docs/adopting.md
    - docs/limitations.md
    - docs/getting-started.md
    - docs/adapters/claude-code.md
    - docs/adapters/plain.md
    - docs/plans/2026-07-06-docs-ia-narrative-layer-design.md
    - docs/plans/2026-07-06-docs-ia-narrative-layer-plan.md
tier_hint: Normal
---

# Docs Information Architecture — Narrative Layer — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Design:** see `docs/plans/2026-07-06-docs-ia-narrative-layer-design.md` for the rationale behind every decision below.

**Goal:** Slim `README.md` to a real 4-command Quickstart and move the "what is this / concepts / how to adopt / limitations" narrative into a small navigable set of in-repo `docs/` files, with reference docs linked (never copied).

**Architecture:** Docs-only. Five new narrative files + a `docs/` index; README slimmed; getting-started gains a one-paragraph intro; three docs repoint a moved README-section reference. No GitHub wiki, no docs-site generator, no CLI/behavior change, no Python touch, no AGENTS.md change (verified: AGENTS.md carries no README/docs-structure pointer; only `docs/cli-reference.md` and `docs/state-machine.md` are derived docs and neither is touched). Runs through the normal self-host lifecycle; the merge gate is satisfied by a plan-declared scope covering all eleven changed files + an attestation.

**Tech Stack:** Markdown prose. Verification via `super-harness doc check` / `decision check`, a copy-paste Quickstart smoke run, and the existing CI suite.

---

## File structure

| File | Responsibility |
|---|---|
| `README.md` (modify) | Slim landing: positioning + What is + minimal install + 4-command Quickstart + Links. Concept/disclaimer/feature-detail blocks removed (moved out). |
| `docs/README.md` (new) | Docs index / landing page routing to narrative docs + existing reference docs. |
| `docs/overview.md` (new) | What it is / why / relationship to neighboring tools (table moved from README) + the condensed "What v0.1 ships" feature enumeration. |
| `docs/concepts.md` (new) | Lifecycle state-machine narrative + "does not review for you / does not spawn an agent" + per-reviewer strategy. |
| `docs/adopting.md` (new) | How to apply it to your own project: lock a rule (→ architecture-fitness) + discover which rules (→ discovery skill). |
| `docs/limitations.md` (new) | v0.1 boundaries ("does NOT ship yet" moved from README) + plain-mode caveat + short FAQ. |
| `docs/getting-started.md` (modify) | Add a one-paragraph intro framing it as the full version of the README Quickstart; repoint the prose reference from the README's moved section to `docs/limitations.md`. Existing inbound links preserved (path and anchors untouched). |
| `docs/adapters/claude-code.md` (modify) | Repoint the prose reference to the README's moved "does NOT ship yet" section -> `docs/limitations.md`, and destale the one clause it sits in (the `AWAITING_PLAN_REVIEW -> PLAN_APPROVED` verb now ships). |
| `docs/adapters/plain.md` (modify) | Repoint the same moved-section reference -> `docs/limitations.md`; leave the deeper plain-mode shipped-surface staleness (pre-existing debt) to a separate cut, logged to OPEN-ITEMS. |

## Authoring invariants (apply to every task)

- **doc-refs / dead-ref gate rule.** The gate flags an *inline single-backtick* span when it is a single identifier with an internal case boundary (`[a-z][A-Z]` / `[A-Z][a-z]`) or an underscore, **and** that token is absent from the repo's source identifier set. Practical rule for these docs:
  - **Safe inline** (present in source or hyphen/lowercase): lifecycle state names that exist as enum values (INTENT_DECLARED, AWAITING_PLAN_REVIEW, PLAN_APPROVED, IMPLEMENTATION_IN_PROGRESS, AWAITING_CODE_REVIEW, READY_TO_MERGE, ARCHIVED), CLI verbs (`change start`, `init`, `done`, `review approve`, `doc check`), file names with hyphens/dots (`.harness/policy.yaml`, `derived-docs.yaml`), plain lowercase words (`subagent`, `human`, `hybrid`, `plain`).
  - **Do NOT inline-backtick** external proper nouns / tool names / non-source tokens — write them as plain text or inside fenced code blocks: OpenSpec, Superpowers, Spec Kit, BMAD, Archon, SpecFact, Claude Code, Cursor, Codex, Aider, GitHub, Python, Rust, PyPI, MCP, Windows, mkdocs, mdBook, pre-commit, uv, just, asciinema.
  - `super-harness doc check` (Task 8) is the hard backstop; if it flags a span, unwrap it.
- **One home per fact.** Content moved out of the README must be *cut*, not copied. The migration map (design §3.3) is the checklist. Do not leave the moved prose in both places.
- **Links must resolve.** Every relative link in a new doc must point at a file that exists at the committed path. Reference docs keep their current paths; getting-started keeps its path and anchors.

---

## Task 1: Create `docs/overview.md`

**Files:** Create `docs/overview.md`

- [ ] **Step 1: Write the file.** Create `docs/overview.md` with EXACTLY the literal Markdown between the BEGIN/END markers (copy the content, not the markers).

=== BEGIN overview.md ===
# Overview

super-harness is an open-source, CI-first, framework-agnostic, agent-agnostic
harness that makes AI coding deterministic and reliable.

## The problem

Spec-driven tools like Spec Kit, OpenSpec, and Superpowers describe rules in
markdown templates that an agent reads and (probabilistically) complies with.
That is advisory: the agent can drift, skip the review, or edit code the spec
said was out of bounds, and nothing stops it. Human review catches some of this,
but it is late, it doesn't scale to agent-sized diffs, and vigilance drops after
a few good experiences.

## What super-harness adds

A harness embeds those constraints in the environment itself — hooks, CI, git,
processes — so violations are blocked deterministically, not just discouraged.
super-harness sits on top of your existing spec framework and agent; it is not a
replacement for either.

## What v0.1 ships

- **Lifecycle CLI** — `init` / `change` / `plan` / `review` / `implementation` /
  `done` / `on-merge` / `status` / `sync` drive a change end-to-end through a
  fixed state machine (see [Concepts](concepts.md)).
- **Hot-path PreToolUse gate** — decided in-process from one state snapshot;
  blocks Edit / Write tool calls in Claude Code when the current lifecycle state
  forbids them.
- **Cold-path PR gates** — via CI: PR metadata + lifecycle-state validation, the
  verification-runner sensor, and a merge gate.
- **Framework adapters** — OpenSpec, Superpowers (marker-driven, version-agnostic
  discovery), and Plain (fallback). See [Adapter docs](adapters/).
- **Agent adapter** — Claude Code (PreToolUse + SessionStart hooks, injects an
  AGENTS.md subsection).
- **Decision conformance** — make a human decision actually bind the AI's code:
  referential integrity, text-lock (a ratified decision can't be silently
  rewritten), and executable checks (the code can't silently violate it). The
  flagship use is architecture-fitness rules — see [Adopting](adopting.md).
- **Derivable-doc drift gate** — regenerates docs that have a generator (CLI
  reference, state-machine diagram) and blocks if the committed copy drifted.
  Prose docs have no generator and are out of scope by design.
- **Bundled CI workflow** — `super-harness init --setup-github` deploys a GitHub
  Actions workflow and a PR template. All GitHub operations go through the `gh`
  CLI — no webhooks, no PATs, no bot account.

For the full command surface see the [CLI reference](cli-reference.md); for the
internals see [Architecture](ARCHITECTURE.md).

## Relationship to neighboring tools

super-harness is complementary to, not a replacement for, the spec-driven and
agent-wrapping projects in the ecosystem:

| Project | Relationship |
|---|---|
| GitHub Spec Kit / OpenSpec | Complementary — super-harness is an upper CI control layer that can stack on top. |
| Superpowers (obra) | Complementary — ships as a built-in framework adapter (marker-driven, version-agnostic discovery), or run in plain mode. |
| Archon | Different axis — Archon wraps agents (agent-wrapper); super-harness is cross-cutting and CI-first. |
| SpecFact | Complementary — SpecFact adds runtime contracts to OpenSpec specifically; super-harness provides cross-framework lifecycle above. |
| Anthropic Managed Agents | Closed-source hosted; super-harness is open-source self-hosted. |
=== END overview.md ===

- [ ] **Step 2: Verify links resolve.**

Run: `ls docs/concepts.md docs/adopting.md docs/adapters docs/cli-reference.md docs/ARCHITECTURE.md`
Expected: all paths exist (concepts.md / adopting.md are created in later tasks; if running Task 1 in isolation, defer this check to Task 8's gate).

- [ ] **Step 3: Commit.**

```bash
git add docs/overview.md
git commit -m "docs: add overview narrative (moved from README)"
```

---

## Task 2: Create `docs/concepts.md`

**Files:** Create `docs/concepts.md`

- [ ] **Step 1: Write the file.** Create `docs/concepts.md` with EXACTLY the literal Markdown between the BEGIN/END markers.

=== BEGIN concepts.md ===
# Concepts

## The lifecycle state machine

A *change* moves through a sequence of states. Each transition is caused by a
recorded event; the gate reads the current state to decide what is allowed. The
happy path:

```
INTENT_DECLARED
  → AWAITING_PLAN_REVIEW      (plan_ready, from the framework adapter or `plan ready`)
  → PLAN_APPROVED             (plan_approved, from a reviewer verdict)
  → IMPLEMENTATION_IN_PROGRESS (implementation_started)
  → AWAITING_CODE_REVIEW      (done, after verification)
  → READY_TO_MERGE            (code_review_passed, from a reviewer verdict)
  → ARCHIVED                  (merged, after the PR lands on main)
```

Reviews can send a change back: a rejected plan goes to PLAN_REJECTED (re-emit
`plan_ready` to retry) and a failed code review goes to CODE_REVIEW_REJECTED; a
change can also be ABANDONED. The state machine is fixed, not configurable — see
the generated [state-machine diagram](state-machine.md) for the authoritative
transition matrix.

## super-harness does not review your code for you

The gate enforces that a review *verdict is recorded* before the lifecycle
proceeds. It does **not** run the review. You — or, per the injected AGENTS.md
protocol, your agent's own reviewer subagent — produce the verdict; the gate only
checks one exists. This is deliberate: the harness is a governor, not a reviewer.

The per-reviewer **strategy** is set in `.harness/policy.yaml`:

- `subagent` — an interactive agent dispatches its own reviewer subagent.
- `human` — a person records the verdict (pick this when a token budget rules out
  subagent review).
- `hybrid` — a mix.

`super-harness status` surfaces the active strategy.

## super-harness does not spawn your agent

The harness never launches a coding agent. The relationship is inverted: your
agent calls the harness (via hooks and CLI), and the harness gates what the agent
is allowed to do. Reviews happen because the gate *requires* a verdict before
advancing — the content of the review is produced by the agent or human, the
*occurrence* of the review is enforced mechanically.

## Two gate paths

- **Hot path** — the PreToolUse gate, decided in-process from a single
  `state.yaml` snapshot, blocks Edit / Write tool calls in Claude Code when the
  current state forbids them. No resident process is on the decision path.
- **Cold path** — CI gates on the PR: metadata + lifecycle-state validation, the
  verification-runner sensor, and the merge gate.
=== END concepts.md ===

- [ ] **Step 2: Commit.**

```bash
git add docs/concepts.md
git commit -m "docs: add concepts narrative (lifecycle, does-not-review, does-not-spawn)"
```

---

## Task 3: Create `docs/adopting.md`

**Files:** Create `docs/adopting.md`

- [ ] **Step 1: Write the file.** Create `docs/adopting.md` with EXACTLY the literal Markdown between the BEGIN/END markers.

=== BEGIN adopting.md ===
# Adopting super-harness in your project

super-harness earns its keep when it binds a rule *your team actually cares
about* so an AI agent can't quietly break it. Two guides walk the two halves of
that:

## 1. Discover which rules to lock

Before you can lock a rule you have to know which rules matter. For a mature
codebase the architecture already exists implicitly in the code, and the
maintainer's mental model has usually drifted from what the code actually does.

The **discovering-architecture-norms** skill mines your codebase for candidate
norms (dependency-direction / layering rules) and hands you a ranked list of
hypotheses to ratify. Your own agent runs it — super-harness does not spawn it.

- Skill: [`skills/discovering-architecture-norms/SKILL.md`](https://github.com/Dawinia/super-harness/blob/main/skills/discovering-architecture-norms/SKILL.md)
  (private repo during v0.1; needs repo access until the public release).

## 2. Lock a rule so the agent can't break it

Once you know the rule, arm it with the decision-conformance mechanism: a
ratified decision record + an executable check that *bites* (passes on current
code, fails on a counterexample). From then on, violating code is blocked in CI.

- Guide: [Arm an architecture rule](architecture-fitness.md).

## Where this fits the lifecycle

Adopting is orthogonal to the per-change lifecycle in [Concepts](concepts.md):
you lock rules once (they live in `docs/decisions/`), and every subsequent change
is checked against them by `super-harness decision check`. New to the lifecycle
itself? Start with the [Getting started](getting-started.md) walkthrough.
=== END adopting.md ===

- [ ] **Step 2: Commit.**

```bash
git add docs/adopting.md
git commit -m "docs: add adopting guide linking architecture-fitness + discovery skill"
```

---

## Task 4: Create `docs/limitations.md`

**Files:** Create `docs/limitations.md`

- [ ] **Step 1: Write the file.** Create `docs/limitations.md` with EXACTLY the literal Markdown between the BEGIN/END markers. This block is the "What v0.1 does NOT ship yet" content moved verbatim in substance from the current README (README lines ~120–163), plus a short FAQ.

> **Migration note (surface in the PR body):** the current README's "does NOT
> ship yet" list contains a stale "Superpowers framework adapter" item — the
> Superpowers framework adapter ships (`src/super_harness/adapters/framework/superpowers.py`;
> the README "ships" section and `overview.md` both list it). This block
> intentionally omits it, and likewise omits the stale plain-mode `plan_ready`
> item (`super-harness plan ready` ships). These are documented corrections, not
> silent edits.

=== BEGIN limitations.md ===
# Limitations & FAQ

Trust is built on honesty about scope. The following are not in v0.1 — most are
deliberately deferred to a later version; one is blocked by an upstream bug
(flagged explicitly).

## What v0.1 does NOT ship yet

**Framework adapters (v0.2+):**
- Spec Kit framework adapter
- BMAD framework adapter

**Agent adapters (v0.2+):**
- Cursor / Codex / Aider agent adapters — platform hook capabilities vary;
  Claude Code is the reference adapter for v0.1.

**Process / orchestration:**
- Unattended CI auto-review — a headless reviewer that produces the verdict with
  no human or interactive agent present (e.g. shelling out to a headless Claude
  run). v0.1 ships the verdict *recording* path (`review approve | reject | skip`)
  and the agent-driven review protocol (the injected AGENTS.md tells the Claude
  Code agent to dispatch its own reviewer subagent and record the verdict), but it
  does not itself run an LLM review. Tracked as a follow-up.
- Daemon-autonomous event-driven dispatch — v0.1 uses CLI one-shot dispatchers
  (e.g., `super-harness on-merge` dispatches the merged-event sensors).

**Gates not yet wired:**
- Cold-path pre-commit / pre-push gates — need git-hook install infrastructure.
- `gate check pr-open` / `gate check pr-merge` — the underlying machinery ships
  via `pr validate`, but the `gate check` wiring is deferred.

**Blocked by upstream:**
- JSON `permissionDecision: deny` for Edit / Write — super-harness currently uses
  exit-2 block instead, because of an upstream Claude Code fail-open bug on
  Edit/deny.

**Platform / integration:**
- MCP integration — v0.2+.
- Windows support — v0.2+; the optional observer host currently uses POSIX
  `os.fork` and `fcntl.flock`.

## FAQ

**Does super-harness run the review or write the verdict?**
No. It enforces that a verdict exists before the lifecycle advances; the agent or
human produces it. See [Concepts](concepts.md).

**Does it spawn or manage my coding agent?**
No. Your agent calls the harness; the harness gates the agent. See
[Concepts](concepts.md).

**How does a plain-mode change advance past INTENT_DECLARED?**
Plain mode advances with the manual verb `super-harness plan ready`
(INTENT_DECLARED → AWAITING_PLAN_REVIEW); framework adapters emit `plan_ready`
automatically from their artifacts (OpenSpec from `tasks.md`).

**Does the drift gate check my prose docs?**
No. `super-harness doc check` only regenerates docs that have a generator (the CLI
reference, the state-machine diagram). Prose has no ground truth to diff against
and is out of scope by design.
=== END limitations.md ===

- [ ] **Step 2: Commit.**

```bash
git add docs/limitations.md
git commit -m "docs: add limitations + FAQ (moved from README 'does NOT ship yet')"
```

---

## Task 5: Create `docs/README.md` (docs index)

**Files:** Create `docs/README.md`

- [ ] **Step 1: Write the file.** Create `docs/README.md` with EXACTLY the literal Markdown between the BEGIN/END markers.

=== BEGIN docs/README.md ===
# super-harness documentation

Start here.

## Narrative — what is this and how do I use it

- [Overview](overview.md) — what super-harness is, the problem it solves, and how
  it relates to neighboring tools.
- [Getting started](getting-started.md) — a 10-minute end-to-end walkthrough (the
  full version of the README Quickstart).
- [Concepts](concepts.md) — the lifecycle state machine, why the harness doesn't
  review your code or spawn your agent, and reviewer strategy.
- [Adopting](adopting.md) — apply it to your own project: discover which
  architecture rules to lock, then lock them.
- [Limitations & FAQ](limitations.md) — v0.1 boundaries and recurring questions.

## Reference

- [CLI reference](cli-reference.md) — the full command surface.
- [Architecture](ARCHITECTURE.md) — internals and module layering.
- [State machine](state-machine.md) — the authoritative transition matrix.
- [Arm an architecture rule](architecture-fitness.md) — the decision-conformance
  worked example.
- [Adapter docs](adapters/) — OpenSpec, Claude Code, Plain.
- [Decision records](decisions/) — the ratified architecture decisions this repo
  binds on itself.
=== END docs/README.md ===

- [ ] **Step 2: Commit.**

```bash
git add docs/README.md
git commit -m "docs: add docs/ index routing narrative + reference"
```

---

## Task 6: Slim `README.md`

**Files:** Modify `README.md`

- [ ] **Step 1: Replace the README body.** Overwrite `README.md` with EXACTLY the literal Markdown between the BEGIN/END markers. This keeps the positioning, "What is", install, and adds the 4-command Quickstart + Links; it removes the long "What v0.1 ships" enumeration (→ overview.md), the "What v0.1 does NOT ship yet" block (→ limitations.md), the concept prose (→ concepts.md), and the Relationship table (→ overview.md).

=== BEGIN README.md ===
# super-harness

> The missing CI layer for spec-driven AI coding workflows.

## What is super-harness?

An open-source, CI-first, framework-agnostic, agent-agnostic harness that makes
AI coding deterministic and reliable. Spec-driven tools describe rules in
markdown that agents read and (probabilistically) comply with; a harness embeds
those constraints in the environment itself — hooks, CI, git, processes — so
violations are blocked deterministically, not just discouraged. It sits on top of
your existing spec framework and agent; it is not a replacement for either.

See the [Overview](docs/overview.md) for the problem it solves, what v0.1 ships,
and how it relates to neighboring tools like Spec Kit, OpenSpec, and Superpowers.

## Install

```bash
pipx install super-harness
brew install gh && gh auth login   # gh is a prerequisite for init --setup-github
```

## Quickstart

Bootstrap a repo and watch the gate block an out-of-lifecycle edit — the whole
point of the tool:

```bash
pipx install super-harness
cd your-repo && super-harness init          # create the .harness/ data plane
super-harness change start "my-change"      # → INTENT_DECLARED
# now have your agent (or you) try to edit code → the gate blocks it,
# because no plan review has happened yet. That block is the product.
```

That is the shortest path to *seeing* super-harness work. The full arc — install
a framework adapter, get the plan reviewed, implement, verify, review, merge — is
the 10-minute [Getting started](docs/getting-started.md) walkthrough. To inspect a
pre-seeded non-trivial `.harness/` state without running anything, see the in-tree
demo [`examples/demo-openspec-claude/`](examples/demo-openspec-claude/).

## Links

- [Documentation index](docs/README.md)
- [Overview](docs/overview.md) — what it is, what v0.1 ships, neighboring tools
- [Getting started](docs/getting-started.md) — full end-to-end walkthrough
- [Concepts](docs/concepts.md) — lifecycle, and what the harness does *not* do
- [Adopting](docs/adopting.md) — lock architecture rules in your own project
- [Limitations & FAQ](docs/limitations.md)
- [CLI reference](docs/cli-reference.md)
- [Architecture](docs/ARCHITECTURE.md)

## License

MIT — see [`LICENSE`](LICENSE).
=== END README.md ===

- [ ] **Step 2: Confirm moved content is cut, not duplicated.**

Run: `grep -n "does NOT ship\|Relationship to neighboring\|per-reviewer" README.md`
Expected: no matches (all three moved out).

- [ ] **Step 3: Commit.**

```bash
git add README.md
git commit -m "docs: slim README to a real 4-command Quickstart + Links"
```

---

## Task 7: Add intro to `docs/getting-started.md`

**Files:** Modify `docs/getting-started.md`

- [ ] **Step 1: Insert a framing paragraph.** After the existing opening block (the "By the end you'll have:" list and the "This guide assumes a Unix shell…" paragraph, immediately before the first `---` separator), insert this paragraph verbatim:

```markdown
> **This is the full version of the README Quickstart.** The README shows the
> shortest path to *seeing* the gate work (ending at `INTENT_DECLARED` with a
> blocked edit). This guide takes a change all the way through every gate to a
> merged, archived PR.
```

- [ ] **Step 2: Repoint the moved-section reference.** The guide points readers
at the README's "What v0.1 does NOT ship yet" section, which this cut moves to
`docs/limitations.md`. Replace this exact string:

```
   project README's "What v0.1 does NOT ship yet" / OPEN-ITEMS #2.
```

with:

```
   [Limitations](limitations.md) / OPEN-ITEMS #2.
```

- [ ] **Step 3: Verify the anchor structure is unchanged.**

Run: `grep -n "^## " docs/getting-started.md | head`
Expected: the numbered section headings are unchanged (only prose was inserted above section 1), so existing inbound links (from architecture-fitness.md, adapters/*.md, examples/demo-openspec-claude/README.md, README.md) still resolve.

- [ ] **Step 4: Commit.**

```bash
git add docs/getting-started.md
git commit -m "docs: frame getting-started as full README Quickstart + repoint moved reference"
```

---

## Task 8: Repoint moved-section references in adapter docs

Both adapter docs point readers at the README's "What v0.1 does NOT ship yet"
section, which this cut moves to `docs/limitations.md`. `docs/adapters/*.md` link
to a docs-root sibling with `../limitations.md`. `claude-code.md` also carries a
now-stale clause (the `AWAITING_PLAN_REVIEW → PLAN_APPROVED` verb ships), fixed in
the same edit. The deeper plain-mode shipped-surface staleness in `plain.md`
(its "intended v0.2+ lifecycle" framing predates the shipped `plan ready` verb) is
a pre-existing debt left to a separate cut — log it to OPEN-ITEMS; do NOT expand
this task into a plain-adapter rewrite.

**Files:** Modify `docs/adapters/claude-code.md`, `docs/adapters/plain.md`

- [ ] **Step 1: Destale + repoint `docs/adapters/claude-code.md`.** Replace this
exact string:

```
  `PLAN_APPROVED`, `IMPLEMENTATION_IN_PROGRESS`, or `CODE_REVIEW_REJECTED`
  (see `src/super_harness/gates/decisions.py`). v0.1 has no public CLI
  verb to advance `AWAITING_PLAN_REVIEW → PLAN_APPROVED`; multi-stage
  plan-reviewer is deferred to v0.2 (see project README's "What v0.1
  does NOT ship yet"). Framework adapters auto-emit `plan_ready` when
```

with:

```
  `PLAN_APPROVED`, `IMPLEMENTATION_IN_PROGRESS`, or `CODE_REVIEW_REJECTED`
  (see `src/super_harness/gates/decisions.py`). `AWAITING_PLAN_REVIEW →
  PLAN_APPROVED` advances via `review approve --reviewer plan-reviewer`;
  multi-stage (multiple sequential reviewers) plan review is deferred to
  v0.2 (see [Limitations](../limitations.md)). Framework adapters auto-emit
  `plan_ready` when
```

- [ ] **Step 2: Repoint `docs/adapters/plain.md`.** Replace this exact string:

```
> plan-reviewer is v0.2 (see project README's "What v0.1 does NOT ship
> yet"). The references to `plan ready` below mirror what Plain's
```

with:

```
> plan-reviewer is v0.2 (see [Limitations](../limitations.md)). The
> references to `plan ready` below mirror what Plain's
```

- [ ] **Step 3: Confirm no dangling README-section references remain.**

Run: `grep -rn "does NOT ship yet" docs/getting-started.md docs/adapters/`
Expected: no matches (all three prose references now point at `docs/limitations.md`).

- [ ] **Step 4: Commit.**

```bash
git add docs/adapters/claude-code.md docs/adapters/plain.md
git commit -m "docs: repoint adapter-doc references to docs/limitations.md + destale plan-approved verb"
```

---

## Task 9: Verification — gates + smoke run

**Files:** none (verification only)

- [ ] **Step 1: Dead-ref / doc-refs gate.**

Run (venv on PATH): `super-harness doc check`
Expected: exit 0, no drift and no flagged inline-backtick spans. If a span is
flagged (an external proper noun wrapped in backticks slipped through), unwrap it
per the Authoring invariants and re-run.

- [ ] **Step 2: Decision-conformance gate (dead-ref layer).**

Run: `super-harness decision check`
Expected: exit 0 (benign `unknown event type` warnings on stderr are ignored;
judge by exit code, never by piping through grep).

- [ ] **Step 3: All narrative links resolve.**

Run:
```bash
for f in docs/overview.md docs/concepts.md docs/adopting.md docs/limitations.md docs/README.md README.md docs/adapters/claude-code.md docs/adapters/plain.md; do
  echo "== $f =="; grep -oE '\]\(([^)]+\.md)[^)]*\)' "$f" | sed -E 's/.*\(([^)#]+).*/\1/'
done
```
Then eyeball that every relative `.md` target exists (resolve paths relative to
the file's directory). Expected: no broken targets.

- [ ] **Step 4: Quickstart smoke run (copy-paste-runnable claim).**

In a throwaway git repo with the venv on PATH:
```bash
super-harness init
super-harness change start "my-change"
super-harness status            # shows INTENT_DECLARED
```
Then attempt a gated edit via the Claude Code PreToolUse hook path (or the
in-process gate check) and confirm it is blocked. Expected: the change reaches
INTENT_DECLARED and an out-of-lifecycle edit is refused. Document the observed
output in the PR description as evidence.

- [ ] **Step 5: Full test suite.**

Run: `PYTHONPATH=src pytest -q`
Expected: the existing suite stays green (this cut adds no code; the count should
match the pre-change baseline, ~1628 passed).

---

## Self-review checklist (run before requesting review)

- [ ] **Spec coverage:** every design §3.2 table row has a task (overview→T1,
  concepts→T2, adopting→T3, limitations→T4, docs/README→T5, README slim→T6,
  getting-started→T7). Plan-review addition: repoint 3 moved-section references
  (getting-started in T7, adapter docs in T8). Verification §6 → T9. ✅
- [ ] **No duplication:** design §3.3 migration map — each moved block appears in
  exactly one home (T6 Step 2 grep enforces the README side; T8 Step 3 confirms no
  dangling README-section references remain).
- [ ] **Links resolve:** T9 Step 3 enumerates them; getting-started path/anchors
  unchanged (T7 Step 3).
- [ ] **doc-refs gate clean:** external proper nouns are plain text throughout;
  T9 Step 1 is the backstop.
- [ ] **No stale claims propagated:** the shipped `plan ready` / `review approve`
  verbs are reflected (limitations.md drops the stale plain-mode bullet + fixes the
  FAQ; concepts.md notes plain-mode `plan ready`; claude-code.md destaled). Deeper
  plain.md staleness deferred to OPEN-ITEMS.
- [ ] **Scope complete:** the marker fence lists all eleven changed files (5 new
  docs + docs/README + README + getting-started + 2 adapter docs + design + plan).
  No AGENTS.md / no derived-doc / no Python (verified in this session).

---

## Landing (self-host lifecycle)

Per repo discipline, after the plan is approved by the user:

1. `super-harness change start 2026-07-06-docs-ia-narrative-layer`
2. `super-harness adapter scan-once superpowers` (reads this plan's fence → `plan_ready`)
3. `super-harness review approve 2026-07-06-docs-ia-narrative-layer --reviewer plan-reviewer`
4. `super-harness implementation start 2026-07-06-docs-ia-narrative-layer`
5. Execute Tasks 1–8 (edit files).
6. `super-harness done`
7. `super-harness review prepare`
8. Two-actor review: Claude subagent (superpowers:code-reviewer) + `codex exec --sandbox read-only`.
9. `super-harness review approve 2026-07-06-docs-ia-narrative-layer --reviewer code-reviewer --verdict-file <yaml>`
10. `super-harness attest write` + commit; `super-harness attest verify --base main --head HEAD`
11. Push + PR (metadata block `Change: 2026-07-06-docs-ia-narrative-layer`) → CI → merge → `super-harness on-merge --commit <sha> --change 2026-07-06-docs-ia-narrative-layer`.
