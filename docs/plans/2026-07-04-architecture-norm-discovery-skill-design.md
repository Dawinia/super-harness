# Architecture-norm discovery skill — Design

> Design rationale for a portable agent skill that helps a team adopting
> super-harness *discover* the candidate architecture norms in their codebase, so
> they can ratify+lock the good ones via the existing mechanism. Companion plan:
> `2026-07-04-architecture-norm-discovery-skill-plan.md`. Sibling guide (already
> shipped, teaches how to LOCK a norm): `docs/architecture-fitness.md`.

## 1. Problem

super-harness ships the *mechanism* to lock an architecture norm (`decision
new/ratify/check` + bite-test) and — as of the architecture-fitness guide — teaches
*how to write a check once you know the rule*. But it does **not** help an adopter
answer the prior question: **which rules should I lock?** For a mature codebase the
architecture already exists implicitly in the code; the maintainer's mental model
drifts from what the code actually does (e.g. OpenScreen's maintainer likely
believes `lib ⊥ components`, but 5 runtime imports leak). There is no on-ramp that
turns "I installed super-harness, now what do I lock?" into a concrete candidate
list. Today that step is tribal knowledge — the user has to know to ask their agent,
and the agent has to know how to mine per-language.

## 2. Goal & non-goals

**Goal:** a portable skill the adopter's *own* Code Agent invokes to **discover
candidate architecture norms** from their codebase, ranked by architectural
strength, presented as hypotheses for the human to judge — then hand off to the
existing lock mechanism.

**Non-goals (explicit):**

- **The skill does not decide what is a *good* rule.** In recover-mode (§5)
  discovery is *descriptive* (read from code); in greenfield-mode (§7) it is
  *intent-prescriptive* (read from product intent). Either way the skill only
  **proposes candidates for the human to judge and ratify** — it never
  auto-locks and never asserts a candidate is correct. (Zero-import ≠ a good
  rule — do not ossify accidents.)
- **Not a CLI command / no code.** super-harness never spawns an agent; the skill is
  invoked *by* the adopter's agent. This is a skill file + doc pointers, no Python.
- **Not a re-implementation of the lock mechanism.** The skill ends by handing off to
  `decision new/ratify/check` + `docs/architecture-fitness.md`.
- **Not an import-graph-only miner.** The most valuable norms live *outside* the
  import graph (see §5).

## 3. Form & location

A single portable **SKILL.md** at `skills/discovering-architecture-norms/SKILL.md`
in the super-harness repo — the ecosystem-standard layout (verified 2026-07-04
against superpowers and andrej-karpathy-skills: both use `skills/<name>/SKILL.md`).

- Frontmatter: `name` + `description` (a "Use when…" triggering rule), per
  writing-skills conventions.
- Written **agent-agnostic** (Claude / Codex / Cursor / … can all read and follow
  it), matching super-harness's Claude+Codex support and superpowers' rule that a
  skill "must work across all the coding agents we support."
- It is a *judgment* skill (discovery + ranking + the why/what-breaks test), which
  is exactly what writing-skills says skills are for — mechanical enforcement is
  left to the shipped check mechanism.

## 4. Distribution

super-harness is a **pipx CLI, not a plugin/marketplace**, so the ecosystem's
`/plugin install` path does not apply, and we do **not** build a custom installer or
a CLI verb (that would be new, per-agent, fiddly surface = gilding, and breaks the
no-code scope). **Critically, the pip wheel packages only `src/super_harness`
(pyproject.toml), so a `pipx`-installed adopter never gets a `skills/` directory on
disk** — repo-relative pointers would resolve to a nonexistent path in the adopter's
tree. Therefore all pointers are **absolute GitHub URLs**, not repo-relative paths:

- The skill lives in the super-harness repo at
  `skills/discovering-architecture-norms/SKILL.md` (source of truth).
- `README.md`, `docs/getting-started.md`, and the injected `AGENTS.md` super-harness
  section each carry a one-line pointer **as a GitHub URL** to that SKILL.md. The
  adopter's agent fetches/reads it from GitHub (or the user copies it into their
  agent's skill directory by the ecosystem convention).
- **Honest limitation:** the super-harness repo is private during v0.1 (public flip
  is a Phase-15 backlog item), so the URL requires repo access until then — it fully
  lands for arbitrary adopters at the public flip. This is called out in the pointer
  text rather than pretending pipx adopters have the file locally.

## 5. The method (skill body) — multi-source sweep + strength ranking

Illustrated on OpenScreen (a TS/React/Electron app), where the highest-value norm
was **not** a src-directory dependency rule — it was the Electron renderer/main
process-isolation boundary (renderer `src/` must not import `electron`/`fs`/`path`/
`child_process`/`os`; currently clean; security-critical), plus a cross-cutting
i18n-completeness norm already mechanized as `scripts/i18n-check.mjs`. The
generalizable lesson is **"the import graph alone is insufficient"** — NOT that a
fixed category always outranks layering (in a hexagonal backend the domain⊥framework
*import* rule may well be the load-bearing boundary). So the skill directs the agent
to sweep **four sources**, in rough order of typical yield (a default, not a law):

1. **Framework / stack** (`package.json`, manifests, lockfiles) → the known
   architectural boundaries *that stack implies* (Electron process isolation, Next
   server/client, hexagonal/onion domain⊥framework, layered backends…). Heuristics
   + a "what does your stack imply?" prompt so it stays portable.
2. **Existing lint / type / script config** (biome/eslint, tsconfig strict, custom
   `scripts/*`) → norms **already encoded but not ratified** — the *cheapest* to
   bind (e.g. wrap an existing `i18n-check` script as a decision's check).
3. **Import dependency graph** → dependency-direction candidates, **ranked by
   asymmetry, not raw zeros**:
   - strong one-way `A→B` with reverse ≈ 0 → a real intended layer (lock the reverse);
   - a pure sink (all edges point in, e.g. `utils`) → "this layer imports nothing";
   - symmetric zero → coincidence, skip (don't ossify);
   - symmetric large → a cycle smell (flag, not a lockable direction rule).
   - *Discovery-time graphing may be approximate.* A grep/heuristic import scan is
     fine here — the skill only emits hypotheses; the precise per-language tool
     (import-linter / dependency-cruiser, per `docs/architecture-fitness.md`) is
     used later at the *lock* step. The skill should flag when its graph is
     approximate rather than silently under-reporting.
4. **Directory / naming patterns** → placement/naming conventions (lock cautiously;
   ceremony risk).

## 6. Output format

A candidate list **ranked by architectural strength**, where strength is scored on
four factors, not a fixed category order:

- **protected capability** — does it guard a real quality (security/isolation,
  testability, replaceability, no-cycles)?
- **evidence strength** — asymmetry / sink-shape / already-mechanized-in-config vs a
  bare coincidental zero;
- **blast radius** — how much breaks if violated;
- **clean vs violated** — currently held (lockable now) vs leaking (fix-first).

The category order *framework-boundary + cross-cutting-correctness > layering >
naming* is a **useful default prior**, not a law: a load-bearing layering rule (e.g.
domain⊥framework in a hexagonal backend) can and should outrank a weak framework
hint. Each candidate carries the norm (as a forbidden/required statement), its
**evidence**, the clean-vs-violated marker, and is forced through the **"why + what
breaks"** test. Every candidate is explicitly a **hypothesis for the human to
judge**, never an auto-lockable rule — the skill presents, it does not recommend
locking. It closes by pointing the human at the lock mechanism: `decision new` →
write the check + counterexample per `docs/architecture-fitness.md` → `ratify`
(bite-test) → `decision check`, and by noting the two *options the human may choose*:
for a clean candidate, locking can happen immediately (prevents regression); for a
violated-but-real one (e.g. `lib ⊥ components`), fix-first or a known-violations
baseline/ratchet.

## 7. Greenfield branch (detect + short route)

The skill's core is *recover from existing code*. A greenfield repo has nothing to
mine, so the skill must **detect "little/no code" first and route**, rather than
mining an empty graph and emitting garbage. **This is a deliberate single-
responsibility risk**: greenfield is a *different-shaped job* — its input is product
intent, not code; its mode is **intent-prescriptive, not descriptive** (see §2). The
mitigation is to keep it a short *routing branch*, not a co-equal mode, so the
recover method stays the skill's center of gravity. The branch: don't mine; instead
**offer** a few high-confidence **intent** norms derived from product docs / vision /
chosen stack **as candidate decisions for the human to ratify** (a ratified decision
needs no check — it can be a body-frozen `context` decision); surface any **check
that already fits** (a validator script → a candidate decision check today);
**defer** layering rules until a skeleton exists, then switch back to recover mode;
advise ratifying sparingly (early architecture churns). Note the philosophy still
holds — the skill *offers*, the human ratifies — it is just sourced from intent
rather than observation.

## 8. Relationship to the shipped guide and mechanism

- `docs/architecture-fitness.md` = *how to lock a norm you already chose* (write the
  check, ratify, the bite-test mechanics, per-language tools).
- This skill = *how to discover which norms to choose* (the prior step).
- Clean seam: the skill's final step hands off to the guide + `decision` verbs. No
  duplicated content — the skill references the guide rather than restating it.

## 9. Integration

- New file: `skills/discovering-architecture-norms/SKILL.md`.
- Pointers: `README.md`, `docs/getting-started.md`, and the AGENTS.md super-harness
  section renderer (`engineering/agents_md_render.py` template) — a one-line pointer
  each. (Editing the AGENTS.md template changes what `init`/`sync` inject into every
  adopter repo, so its wording must be agent-agnostic and stable.)
- doc-refs gate: the SKILL.md **is** doc-scanned (the default doc scope is
  `**/*.md` minus `docs/plans/**` / `examples/**` / the two generated docs — `skills/**`
  is not excluded), and so are the README/getting-started/AGENTS.md edits. The
  **precise** authoring rule (per `core/doc_refs.py`): the gate extracts only inline
  single-backtick spans and flags a span that is a single identifier with a camelCase
  / TitleCase / snake_case shape absent from source. So the real risks are
  TitleCase/camelCase proper nouns and snake_case symbols (`Electron`, `Biome`,
  `child_process`, `mayDependOn`) — those go inside fenced blocks or as plain text.
  Hyphenated / dotted / lowercase names (`lint-imports`, `dependency-cruiser`,
  `.importlinter`, `depcruise`) are **safe inline** (they fail the identifier shape).
  Run `super-harness doc check` as the backstop.

## 10. Implementation approach & verification

- Author the SKILL.md via the **writing-skills** skill, which is TDD for skills:
  baseline pressure-test (does an agent, without the skill, mine badly / miss the
  framework boundary / ossify a zero?) → write the skill → verify an agent *with* it
  produces the right candidate set and respects the "hypothesis not rule" framing →
  refactor to close loopholes.
- **Reproducible acceptance via a vendored fixture** (not an external repo). Commit a
  trimmed multi-layer sample repo under `examples/arch-norm-fixture/` — deliberately
  chosen because `examples/**` is excluded from BOTH the doc-scan and source-scan
  scopes, so the fixture cannot trip `doc check` / `decision check`. The fixture
  encodes the OpenScreen-style shape (a framework-boundary norm currently clean; a
  layering rule with a planted leak; a pure sink; a coincidental symmetric zero) and
  ships a committed **golden candidate list** (`examples/arch-norm-fixture/EXPECTED.md`).
  Acceptance = a fresh agent given the skill + the fixture produces a ranked list
  that (a) surfaces the framework-boundary norm top-tier, (b) flags the planted
  layering leak as violated, (c) ranks by asymmetry/strength not raw zeros, (d)
  skips the coincidental zero, (e) presents everything as hypotheses and hands off to
  `decision`/the guide — matching EXPECTED.md. OpenScreen stays an *illustrative,
  not-in-repo* real-world check, referenced but not depended on.
- Self-host lifecycle: docs+skill+fixture change; declared scope covers the SKILL.md,
  the fixture files, the three pointer files (README, getting-started, the AGENTS.md-
  template source `agents_md_render.py` / its inline-primitives module), the design
  doc, and the plan. `doc check` + `decision check` + `verify` + `attest verify` +
  `sync --check` (AGENTS drift) green. Tier: `Normal`.

## 11. Risks & mitigations

- **Skill drifts into deciding reasonableness.** Mitigation: the "hypothesis, human
  judges" framing is load-bearing text; pressure-test that the agent doesn't
  auto-recommend locking.
- **Framework heuristics go stale / miss a stack.** Mitigation: heuristics +
  a general "what does your stack imply?" prompt, so an unknown stack still gets a
  reasoned pass rather than silence.
- **Ceremony risk from naming rules.** Mitigation: ranking puts naming last and the
  why/what-breaks test filters ceremony.
- **AGENTS.md template edit blast radius.** It re-injects into every adopter repo —
  keep the pointer one line, agent-agnostic, and covered by the AGENTS.md sync gate.
