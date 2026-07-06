# Docs information architecture — narrative layer — Design

> Restructure the project's human-facing documentation so the README stops
> conflating three things (shortest path / concepts / disclaimers). Slim the
> README to a real Quickstart, and move the narrative "what is this / how do I
> adopt it" layer into in-repo `docs/`. Companion plan:
> `2026-07-06-docs-ia-narrative-layer-plan.md`. This is a B-track / adopter-value
> cut (follows `docs/architecture-fitness.md` #71 and the discovery skill #72),
> aimed at the first invited collaborators/adopters who read the repo during the
> v0.1 private phase.

## 1. Problem

The current `README.md` (190 lines) overloads its Quickstart section by mixing
three distinct kinds of content:

1. **The real shortest path** — install + the commands that actually get you started.
2. **Concepts** — "super-harness does not run the review for you", "it does not
   spawn an agent", per-reviewer `strategy` (`subagent`/`human`/`hybrid`), the
   lifecycle state machine.
3. **Disclaimers / edge caveats** — plain-mode cold start stops at
   `INTENT_DECLARED`, unattended CI auto-review is deferred, the whole "What v0.1
   does NOT ship yet" block.

A reader who just wants to try the tool has to wade through (2) and (3) before
they can copy-paste anything. The Quickstart is not quick. There is no single
navigable "narrative layer" that answers *what is this / why / how do I adopt it*
for a new developer — that content is scattered across an overgrown README and a
flat pile of reference docs (`cli-reference.md`, `ARCHITECTURE.md`,
`state-machine.md`, `architecture-fitness.md`, `adapters/`, `decisions/`).

## 2. Prior decision this reverses: no GitHub wiki

The originating ask (2026-07-06) was to build a **GitHub wiki** as the
human-facing narrative layer, on the theory that a wiki lives outside
super-harness's own drift gate. Research into how active, serious CLI /
dev-infra projects actually structure docs updates that plan:

- **pre-commit** (the closest analog — itself a git-hook harness with a
  non-fully-scriptable quickstart): README is a ~5-line redirect to
  `pre-commit.com`; all docs on a separate site. No wiki.
- **uv** (astral): README = tagline + highlights + minimal install + a few
  command examples that end in real terminal output, then links to
  `docs.astral.sh/uv` for depth (mkdocs over in-repo markdown). No wiki.
- **just**: README = the entire manual (~15k words), with a parallel mdBook from
  the same source. No wiki.

None of these use a GitHub wiki. The verified rule of thumb across the sample:
serious tooling keeps its narrative layer either in the README or as **in-repo
markdown** (optionally rendered to a site), never in a GitHub wiki — because a
wiki can't be versioned with the code, can't go through PR review, and creates a
second home that drifts.

Two facts collapse the original motivation for a wiki:

- **The drift gate does not gate prose.** `super-harness doc check` only
  regenerates *derivable* docs (those with a generator, registered in
  `.harness/derived-docs.yaml`). Prose docs are out of scope by design (README
  line 113). So a narrative layer in `docs/` is *already* exempt from the drift
  gate — the wiki bought nothing there.
- **Visibility is identical.** A GitHub wiki inherits repo visibility, exactly
  like in-repo `docs/`. No advantage during the private phase.

**Decision:** narrative layer lives in in-repo `docs/`. No GitHub wiki. One home.
(User confirmed 2026-07-06.)

## 3. Design

### 3.1 The real Quickstart

Model it on uv: **minimal install + a handful of copy-paste commands that end in
a visible result.** For *this* tool, the most honest and compelling visible
result is not "green all the way to merge" (that path inherently needs a
human/agent to author review verdicts and edit code) but **the gate actually
blocking an out-of-lifecycle edit.** That is the product's whole point, and it
is reproducible in four commands:

```bash
pipx install super-harness
cd your-repo && super-harness init          # create the .harness/ data plane
super-harness change start "my-change"      # → INTENT_DECLARED
# now have your agent (or you) try to edit code → the gate blocks it
# (no plan review has happened yet — that is the point)
```

The payoff line is visible and honest: an edit attempted out of lifecycle is
blocked (exit 2). The Quickstart does **not** try to script the full arc
(adapter install → plan review → implementation → done → code review → merge);
it links to the existing `docs/getting-started.md`, which is already a full
10-minute end-to-end walkthrough. The plain-mode caveat (a pure-`plain` cold
start stops at `INTENT_DECLARED`) drops out of the Quickstart body and moves to
Concepts/Limitations as a single sentence. The Quickstart ends with a one-line
pointer to `examples/demo-openspec-claude/` for readers who want to inspect a
pre-seeded non-trivial `.harness/` state.

### 3.2 Narrative layer structure

Redistribute the README's overloaded content into a small, navigable set of
in-repo narrative docs. New files unless noted:

| Location | Content | Source |
|---|---|---|
| `README.md` (**slimmed**) | one-line positioning + "What is" (current top ~13 lines) + minimal install + 4-command Quickstart + Links | keep the essence; cut the long feature-list detail and the disclaimer block |
| `docs/overview.md` | what it is / why / relationship to neighboring tools (the current README "Relationship to neighboring tools" table moves here) | moved out of README |
| `docs/concepts.md` | lifecycle state machine narrative + "does not review for you / does not spawn an agent" + per-reviewer strategy (`subagent`/`human`/`hybrid`) | README concept content moved out |
| `docs/getting-started.md` (**edited**) | the full 10-minute end-to-end arc; add a short intro noting it is the full version of the README Quickstart | already exists |
| `docs/adopting.md` | how to apply it to your own project: lock an architecture rule (links `architecture-fitness.md` #71) + discover which rules to lock (links the discovery skill #72) | new; ties the B-track cuts together |
| `docs/limitations.md` | v0.1 boundaries (the README "What v0.1 does NOT ship yet" block moves here) + the plain-mode caveat + a short FAQ of recurring "why does X" questions | README disclaimer content moved out; FAQ folded in (no separate `faq.md`) |
| `docs/README.md` | docs landing page / index that routes to the narrative docs above **and** the existing reference docs | new navigation index |

**Reference docs stay put and unchanged in content:** `cli-reference.md`,
`ARCHITECTURE.md`, `state-machine.md`, `architecture-fitness.md`, `adapters/`,
`decisions/`. The narrative layer *links* to them; it never copies their
content. One home per fact.

Note vs. the brainstorm sketch: FAQ and Limitations are merged into a single
`docs/limitations.md` (a short FAQ subsection) rather than two files — the v0.1
"what's missing" and the recurring-questions content overlap heavily and are
both small.

### 3.3 Content-migration map (what moves where)

- README "Quickstart" prose (lines ~37–53, the strategy/auto-review explanation)
  → split: the runnable commands become the new 4-line Quickstart; the concept
  prose → `docs/concepts.md`.
- README "What v0.1 ships" (lines ~55–118) → the value is real but too long for
  a landing README; condense to a short bulleted "What is" + move the detailed
  enumeration into `docs/overview.md` (features) and keep decision-conformance
  depth pointing at `docs/architecture-fitness.md`.
- README "What v0.1 does NOT ship yet" (lines ~120–163) → `docs/limitations.md`.
- README "Relationship to neighboring tools" table (lines ~165–176) →
  `docs/overview.md`.
- README "Links" (lines ~178–186) → kept in README (trimmed) and mirrored/expanded
  in `docs/README.md`.

## 4. Non-goals

- No GitHub wiki (see §2).
- No docs-site generator (mkdocs/mdBook) in this cut — the in-repo markdown is
  the deliverable; rendering to a site later is a zero-migration follow-up
  because the source is already in-repo markdown.
- No changes to reference-doc *content* (`cli-reference.md`, `ARCHITECTURE.md`,
  etc.); only new links into them.
- No CLI/behavior changes. This is a documentation cut. The single Python touch,
  if any, is limited to keeping generated docs / AGENTS pointers consistent (see
  §5), not new features.
- Not re-litigating the drift-gate model (prose is already out of scope).

## 5. Landing / lifecycle plan (self-host)

`README.md` and `docs/*.md` are gate-managed, so this cut goes through the full
self-host lifecycle. Scope must list **every** new and edited file up front
(missing files get caught by `attest verify`). Expected scope:

- New: `docs/overview.md`, `docs/concepts.md`, `docs/adopting.md`,
  `docs/limitations.md`, `docs/README.md`,
  `docs/plans/2026-07-06-docs-ia-narrative-layer-design.md`,
  `docs/plans/2026-07-06-docs-ia-narrative-layer-plan.md`.
- Edited: `README.md`, `docs/getting-started.md`.
- Possibly edited (verify during implementation): any generated pointer that
  references the README/docs structure — e.g. a docs index the derived-docs gate
  knows about, or an AGENTS.md docs pointer. Confirm with `super-harness doc
  check` and the decision/dead-ref gates before finalizing scope; add to scope if
  touched.

Lifecycle order (per repo discipline): `change start` → `adapter scan-once
superpowers` (reads the plan-doc fence → `plan_ready`) → `review approve
--reviewer plan-reviewer` → `implementation start` → edit files → `done` →
`review prepare` → two-actor review (Claude subagent + `codex exec
--sandbox read-only`) → `review approve --reviewer code-reviewer
--verdict-file` → `attest write` + commit → `attest verify --base main --head
HEAD` → push + PR (metadata block `Change: <slug>`) → CI → merge → `on-merge`.

## 6. Verification

- **Dead-ref / link integrity:** every new narrative doc's links to reference
  docs must resolve; run the dead-ref gate (`doc check` / decision check) so no
  broken relative links ship. The reciprocal risk — a reference doc or README
  linking a *moved* section — is checked by re-running the same gate after the
  README slim.
- **No content duplication:** manually confirm moved blocks are *cut* from the
  README, not copied (one home per fact) — the migration map in §3.3 is the
  checklist.
- **Quickstart actually runs:** the 4-command Quickstart is copy-paste-run on a
  throwaway repo to confirm it reaches `INTENT_DECLARED` and that a subsequent
  edit is blocked, before claiming the visible result.
- **Existing suite green + all gates:** full test run + CI (the doc cut should
  not perturb the 1600+ test suite; any Python pointer touch keeps its unit test
  green).

## 7. Open questions

- Whether any generated doc/AGENTS pointer references the README structure and
  thus must be in scope — resolved empirically during implementation by running
  the doc/decision gates (§5), not guessed here.
