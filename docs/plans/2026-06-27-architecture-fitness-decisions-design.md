# Architecture-fitness as rung-1 decision enforcement (G-FITNESS) — design

**Date:** 2026-06-27
**Status:** design (brainstorm + spike validated; ready for implementation)
**Dimension:** G-FITNESS — Böckeler's "architecture fitness" governed dimension, currently empty.

---

## 1. One-line thesis

Architecture-type decisions are the first (and one of the few) decision classes that
can reach **rung-1** of the strength ladder: bound to an **executable fitness function**
where the check *is* the artifact, so drift cannot exist. G-FITNESS arms our first
architecture-class decision with a real architecture-analysis engine (import-linter),
**reusing the existing tier-1 executable-check spine** rather than building a new
subsystem. The novel, super-harness-specific part is the *enforced, machine-checkable
provenance link* from rule → ratified decision — verified absent from all surveyed
tooling (§6).

## 2. Positioning — what this is, and what it is NOT

**Is:** the rung-1 enforcement tier for **architecture-class decisions**. Today all 9
ratified decisions sit at rung-2/3 (text-lock + human review): text-lock protects
"nobody silently reworded the decision," it does **not** protect "the code still obeys
it." A layering decision like *"`core/` must not import the orchestration layers"* must
be defended by an executable check or it is only as strong as vigilance.

**Is NOT:**
- a generic check-runner ("we also have a linter") — the differentiator is the
  *decision binding*, not the linter.
- an operational drift-sensor that flags erosion against rules nobody ratified
  (Böckeler/OpenAI "garbage-collection" framing). Decision-*un*bound. **Rejected.**
- a new subsystem. The umbrella design (§12.x of
  `2026-06-05-decision-conformance-harness-design.md`) already folds architecture-fitness
  into the decision machinery: "the architecture" is a *class of decisions*; "fits
  architecture" = passes the check against arch-type decisions.

## 3. Mechanism — reuse the tier-1 spine (pivot from the original subsystem design)

**Key discovery during planning:** the tier-1 executable-check mechanism already provides
everything a fitness function needs. A tier-1 decision (`decision_tier()==1`) carries a
` ```check ``` ` block = an arbitrary shell command, and the spine already:

- **executes** it — `core/check_runner.py::run_one_check` (`shell=True`);
- **gates** it — `decision check` runs every ratified tier-1 and fails CI on any
  unsatisfied check (already a merge-gate signal);
- **proves it bites** — `bite_test` runs the check on the real tree (must PASS) and in a
  sandbox with an injected counterexample (must FAIL) at ratify time — the anti-hollow
  proof;
- **text-locks** the check command via the decision body hash.

So "architecture decision → run import-linter" needs **no new executor, no new event, no
new gate, no new baseline**. The original design's `fitness:` frontmatter block + engine
adapters + new verification baseline (former §3–§6) **reinvented the tier-1 spine** and is
dropped (anti-gold-plating).

**G-FITNESS = tier-1 specialized to the architecture class, with a real graph engine in
the check slot instead of a fragile `grep`.** Relationship: **G-FITNESS ⊂ tier-1**.

### 3.1 Why a real engine, not grep

`grep 'from ...cli'` catches only *direct textual* imports. A real architecture rule
needs the *import graph* (transitive, aliased, re-exported, function-local imports).
import-linter (via grimp) builds that graph. **Proven in spike:** grep declared
`core ⊥ {cli,sensors,gates}` clean, but import-linter immediately found
`core.review_bundle → adapters → sensors` (a transitive coupling through a function-local
import) — exactly the class of violation grep is blind to. grep is a *fake* fitness
function; the dimension needs a real one.

### 3.2 The bite-test wrinkle and its zero-code fix (spike-validated)

import-linter analyzes the *importable* package, not the cwd's files. The existing
bite-test injects the counterexample into a sandbox tempdir copy and runs with
`cwd=sandbox`; with `super_harness` installed editable, import-linter would analyze the
**real** src (clean) and **not bite** — breaking the anti-hollow proof.

**Fix (no mechanism code):** the check command carries a *relative* `PYTHONPATH=src`.
Since the pass side runs with `cwd=workspace_root` and the bite side with `cwd=sandbox`,
`PYTHONPATH=src` resolves to `workspace/src` (real, clean → PASS) and `sandbox/src`
(counterexample → FAIL) respectively. One command string works on both sides; the
bite-test and check_runner are untouched. Spike: PASS exit 0, BITE exit 1. ✓

The engine-specific concern (PYTHONPATH, src-layout) lives in the **check command
string**, which is the engine-agnostic shell interface — the portability seam (§6).

### 3.3 The decision's check block

For `d-core-is-base`:
```
PYTHONPATH=src lint-imports --config .importlinter --contract core-is-base --no-cache
```
`--contract` scopes the run to this decision's contract (binding precision);
`--no-cache` avoids cache artifacts in the working tree and stale cross-run results.

## 4. Data model — option A (binding owned by super-harness, rule format delegated)

The decision ↔ rule binding is the ` ```check ``` ` command naming the native config +
contract. The *rule expression* is delegated to import-linter's native `.importlinter`
(INI) — we do **not** invent a DSL. The binding is enforced by execution: a missing
config / contract makes `lint-imports` exit non-zero → the tier-1 check fails → the gate
blocks. (No separate "dangling fitness pointer" check is needed; broken bindings fail
loudly through execution.)

`.importlinter` (repo root):
```ini
[importlinter]
root_package = super_harness

[importlinter:contract:core-is-base]
name = core is the base layer (must not import cli/gates)
type = forbidden
source_modules =
    super_harness.core
forbidden_modules =
    super_harness.cli
    super_harness.gates
```

## 5. Three faces of the decision (kept in sync, super-harness's existing pattern)

1. **Decision record** `docs/decisions/d-core-is-base.md` (ratified, text-locked) — anchor.
2. **AGENTS.md** — the generated "Decision conformance" section routes agents to
   `docs/decisions/` + `decision check` generically; it does **not** enumerate per-decision
   guide lines, so adding this decision produces **no AGENTS.md diff** (verified: `sync
   --check` clean, AGENTS.md unchanged). The decision record's own prose (face 1) is the
   human-readable guide; agents discover it via `decision check`, never via the INI. **No
   effect on LLM reasoning.**
3. **`.importlinter` contract** executed by the tier-1 check — sensor/rung-1.

## 6. Portability seam (axis A) and engine choice

The check command is a shell string, so the engine seam is the shell itself — **zero
adapter code**. Other languages plug a different command:

| Language | Engine | Check command shape |
|---|---|---|
| Python | import-linter | `PYTHONPATH=src lint-imports --config .importlinter --contract X` |
| JS/TS | dependency-cruiser | `depcruise --config .dependency-cruiser.js src` |
| Ruby | packwerk | `bin/packwerk check` |
| Java | ArchUnit | (runs as a JUnit test) |

**v1 builds only the import-linter case** (self-host is Python) — per the axis-A
verify-first discipline (don't ship speculative adapters with no real adopter). The
shell-command seam is *proven general by construction* (any tool that exits non-zero on
violation slots in), so no adapter abstraction is built.

## 7. Prior-art research (2026-06-27, primary-source verified)

Surveyed import-linter, dependency-cruiser, ArchUnit, packwerk, ts-arch, Spectral, ADR
tools. Findings:

- **Rule-expression is mature & solved per language.** All gate CI via non-zero exit.
- **import-linter is the weakest on adoption ergonomics** — no `init`, no baseline-freeze
  (the other three have freeze). *Implication:* any rule we weld must already pass today
  or CI goes red — there is no "freeze existing violations." (Drove the §8 rule choice.)
- **`init` scaffolds only generate trivial defaults** (no-circular, no-orphans); real
  layering rules are always hand-authored. So out-of-box-ness is orthogonal to who owns
  the rule format.
- **Decision-record binding is novel.** Conceptual pairing is canonical (Ford/Parsons/Kua,
  *Building Evolutionary Architectures*), but every surveyed tool expresses the link as
  **inert free text never used in rule logic** (dependency-cruiser `comment` explicitly
  "not used"; ArchUnit `.because("…")` is a string; "ADR" appears nowhere in its docs).
  A rule whose decision link is *mechanically enforced* (check fails → gate blocks, bound
  by a text-locked tier-1 decision) is **not matched by any tool found**. Scope the
  novelty to the *enforced provenance link*, not the idea of pairing.

Sources: import-linter.readthedocs.io; github.com/sverweij/dependency-cruiser;
archunit.org; github.com/Shopify/packwerk; github.com/ts-arch/ts-arch;
nealford.com/books/buildingevolutionaryarchitectures.html.

## 8. Self-host v1 — what we weld

Spike-verified against the real import graph (2026-06-27, transitive analysis):
- `core → cli` — clean (transitive). ✓
- `core → gates` — clean (transitive). ✓
- `core → sensors` — **BROKEN** transitively via `core.review_bundle → adapters →
  sensors`. Cannot be welded today (import-linter has no freeze). Recorded as a finding;
  fixing the adapters coupling is out of scope.

**v1 flagship rule:** `forbidden: core → {cli, gates}` — green today under transitive
analysis, one new ratified tier-1 decision `d-core-is-base`, threaded fully: decision
(text-locked) → AGENTS.md line (guide) → `.importlinter` contract (rung-1) → `decision
check` gate. **Value bleeds** the first time a human or agent adds `import cli`/`gates`
inside `core/` — and, unlike grep, transitively.

> The narrower-than-expected rule (cli+gates, not +sensors) is itself the dogfood payoff:
> the real engine found a real transitive coupling on the first run.

## 9. Deliverables (zero Python mechanism code)

1. `import-linter` added as a dev/CI dependency (so `decision check` can run it locally
   and in CI; not a runtime dependency of the shipped product).
2. `.importlinter` with the `core-is-base` contract.
3. `docs/decisions/d-core-is-base.md` — tier-1 decision: ` ```check ``` ` block (§3.3) +
   ` ```counterexample ``` ` (a core file importing `cli`), ratified (bite-test passes).
4. AGENTS.md regenerated via `sync --agents-md` (new decision/guide line).
5. CI: ensure the workflow installs import-linter before `decision check` (covered by 1
   if CI installs the dev group).
6. `.gitignore`: only if any import-linter artifact escapes `--no-cache` (none expected).

## 10. Out of scope

- dependency-cruiser / packwerk / ArchUnit support (future, on real adopter — §6).
- baseline-freeze for import-linter (none; not our job; v1 rule is clean).
- fixing the `core → adapters → sensors` transitive coupling (recorded finding — §8).
- `fitness:` frontmatter block / engine adapters / new baseline (reinvented tier-1;
  dropped — §3).

## 11. Success criteria

- `d-core-is-base` is a ratified tier-1 decision; `decision check` runs its import-linter
  check and is green on the current tree.
- The bite-test passed at ratify (proven non-hollow): the check FAILS with a counterexample
  `core → cli` import injected.
- Introducing a real `core → cli`/`gates` import (incl. transitive) makes `decision check`
  exit non-zero and blocks the merge gate.
- AGENTS.md carries the guide line and `sync --check` is green.
- The full lifecycle (change → plan → review → implement → verify → done → review → attest
  → PR → CI → merge → on-merge) passes with attestation scope covering every changed file.
