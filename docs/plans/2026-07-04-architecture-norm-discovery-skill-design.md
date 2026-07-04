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

- **The skill does not decide what is a *good* rule.** Discovery is descriptive;
  reasonableness is normative and stays a human judgment at ratify. The skill
  proposes, the human disposes. (Zero-import ≠ a good rule — do not ossify
  accidents.)
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
`/plugin install` path does not apply, and we do **not** build a custom installer
(the ecosystem doesn't copy-to-`~/.claude/skills` either — that would be new,
per-agent, fiddly surface = gilding). Distribution is doc-pointer:

- The skill lives in the repo at `skills/discovering-architecture-norms/`.
- `README.md` tells new adopters they can point their Code Agent at this skill to
  discover their architecture norms.
- `docs/getting-started.md` + the injected `AGENTS.md` super-harness section carry
  a pointer. The adopter installs it into their agent's skill directory by the
  ecosystem convention (or points their agent at the repo path directly).

## 5. The method (skill body) — multi-source sweep + strength ranking

Proven live on OpenScreen (a TS/React/Electron app). The crown-jewel norm there was
**not** a src-directory dependency rule — it was the Electron renderer/main
process-isolation boundary (renderer `src/` must not import `electron`/`fs`/`path`/
`child_process`/`os`; currently clean; security-critical), plus a cross-cutting
i18n-completeness norm already mechanized as `scripts/i18n-check.mjs`. An
import-matrix-only tool misses both. So the skill directs the agent to sweep **four
sources**, in rough order of yield:

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
4. **Directory / naming patterns** → placement/naming conventions (lock cautiously;
   ceremony risk).

## 6. Output format

A candidate list **grouped/ranked by architectural strength**:

> framework-boundary + cross-cutting-correctness  >  layering  >  naming

Each candidate carries: the norm (as a forbidden/required statement), the
**evidence**, a **clean-vs-violated** marker, and is forced through the **"why +
what breaks"** test (what capability it protects; what breaks if violated). Every
candidate is explicitly a **hypothesis for the human to judge**, never an
auto-lockable rule. The skill closes by pointing the human at the lock mechanism:
`decision new` → write the check + counterexample per `docs/architecture-fitness.md`
→ `ratify` (bite-test) → `decision check`. For already-clean candidates: lock now
(prevent regression). For violated-but-real (e.g. `lib ⊥ components`): fix-first or
use a known-violations baseline/ratchet.

## 7. Greenfield branch (detect + short route)

The skill's core is *recover from existing code*. A greenfield repo has nothing to
mine, so the skill must **detect "little/no code" first and route**, rather than
mining an empty graph and emitting garbage. The greenfield branch is short: don't
mine; instead derive a few high-confidence **intent** norms from product docs /
vision / chosen stack; **text-lock** them as decisions (a ratified decision needs no
check — it becomes a body-frozen `context` decision); **arm the checks that already
fit** (a validator script → a decision check today); **defer** layering rules until
a skeleton exists, then switch back to recover mode; ratify sparingly (early
architecture churns). This is a routing branch, not a co-equal mode — the declare
path mostly reuses the existing mechanism, so it stays brief.

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
  is not excluded), and so are the README/getting-started/AGENTS.md edits. So the
  SKILL.md and the pointers must follow the same authoring rule as the
  architecture-fitness guide — tool/API identifiers and capitalized proper nouns
  (Electron, Biome, …) only inside fenced blocks, never inline backticks. Run
  `super-harness doc check` as the backstop.

## 10. Implementation approach & verification

- Author the SKILL.md via the **writing-skills** skill, which is TDD for skills:
  baseline pressure-test (does an agent, without the skill, mine badly / miss the
  framework boundary / ossify a zero?) → write the skill → verify an agent *with* it
  discovers the OpenScreen-style candidate set correctly and respects the
  "hypothesis not rule" framing → refactor to close loopholes.
- Concrete acceptance: a fresh agent given the skill + the OpenScreen repo produces
  a ranked candidate list that (a) surfaces the Electron process-isolation boundary
  as top-tier, (b) flags `lib ⊥ components` as violated, (c) ranks by asymmetry not
  zeros, (d) presents everything as hypotheses and hands off to `decision`/the guide.
- Self-host lifecycle: docs+skill change; declared scope covers the SKILL.md, the
  three pointer files, the AGENTS.md-template source, the design doc, and the plan.
  `doc check` + `decision check` + `verify` + `attest verify` green. Tier: `Normal`.

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
