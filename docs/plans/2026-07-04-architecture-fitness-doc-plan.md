---
# super-harness ⇄ superpowers integration marker (parsed by SuperpowersAdapter):
change: 2026-07-04-architecture-fitness-doc
stage: plan
scope:
  files:
    - docs/architecture-fitness.md
    - docs/getting-started.md
    - README.md
    - docs/plans/2026-07-04-architecture-fitness-doc-design.md
    - docs/plans/2026-07-04-architecture-fitness-doc-plan.md
tier_hint: Normal
---

# Architecture-fitness Adoption Guide — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Design:** see `docs/plans/2026-07-04-architecture-fitness-doc-design.md` for the rationale behind every decision below.

**Goal:** Ship a discoverable, language-portable guide teaching adopting teams to arm dependency-direction architecture rules with super-harness's existing decision-check + bite-test mechanism, plus cross-link pointers.

**Architecture:** Docs-only. One new file `docs/architecture-fitness.md`; three cross-link edits (getting-started §10, README ×2). No code, no new decision, no new mechanism. Runs through the normal super-harness self-host lifecycle; the merge gate is satisfied by a plan-declared scope covering all five changed files + an attestation.

**Tech Stack:** Markdown prose. Verification via `super-harness doc check` / `decision check` and the existing CI suite. External tool facts verified 2026-07-04.

---

## File structure

| File | Responsibility |
|---|---|
| `docs/architecture-fitness.md` (new) | The guide: why → pattern → Python worked example → two-axis language table → boundary → sources. |
| `docs/getting-started.md` (modify) | One pointer in §10 point-4 linking to the new guide. |
| `README.md` (modify) | A `## Links` entry + a one-line pointer on the "Decision conformance" bullet. |

## Authoring invariants (apply to every task)

- **doc-refs gate rule (verified against `core/doc_refs.py`).** The gate extracts only *inline single-backtick* spans and flags a span that is a single identifier with an internal case boundary OR a leading capital (regex `[a-z][A-Z]|[A-Z][a-z]`) OR an underscore, when that token is absent from the source identifier set. Therefore, inside prose, do **NOT** wrap in inline backticks: (a) config keys / API names — mayDependOn, source_modules, forbidden_modules, severity, dependencyTypes, layering_check, resideInAPackage, restrict_imports, node_modules; (b) capitalized tool/language proper nouns — Node, ArchUnit, Bazel, CppDepend, Python, Go, Rust, Java, TypeScript, GitHub, PyPI. Put all of these inside fenced ``` code blocks or write them as plain text. Hyphen/dot names (`lint-imports`, `go-arch-lint`, `.importlinter`, `dependency-cruiser`) and plain lowercase words (`depcruise`, `grep`, `ratify`) are safe inline. `super-harness doc check` (Task 1 Step 2) is the hard backstop.
- **Honesty grades (label in-doc):** Grade A = CI-run (Python, anchored on live `d-core-is-base`); Grade B = verified tool invocation (commands/exit codes fetched 2026-07-04); Grade C = illustrative-not-executed (TS/Go/C-C++ config + counterexample snippets). Do not let B bleed into C.
- **Python worked example reproduces the live command verbatim:** `PYTHONPATH=src lint-imports --config .importlinter --contract <id> --no-cache`.

---

## Task 1: Create `docs/architecture-fitness.md`

**Files:** Create `docs/architecture-fitness.md`

- [ ] **Step 1: Write the guide.** Create `docs/architecture-fitness.md` with EXACTLY the literal Markdown between the `=== BEGIN GUIDE BODY ===` / `=== END GUIDE BODY ===` markers below (copy the content, not the markers). It is publishable prose with the verified per-language snippets and honesty labels already in place.

=== BEGIN GUIDE BODY ===
# Arm an architecture rule

Teams using AI coding agents keep hitting the same wall: the agent quietly
violates an architecture rule — a layer reaches sideways, the core imports the
web layer — and nobody notices until code review, if then. Thoughtworks' 2026
Technology Radar names this repeatedly ("architecture drift", "codebase
cognitive debt", "complacency with AI-generated code"). Human review doesn't save
you here for structural reasons: it is late, it doesn't scale to agent-sized
diffs, and vigilance drops after a few good experiences.

Writing the rule into an AGENTS.md or CLAUDE.md file as prose does not hold —
agents read the instruction and violate it anyway. What holds is a *mechanical*
check the agent cannot talk its way past. super-harness already ships that
mechanism; this guide shows how to point it at your architecture rules.

## Why not just grep?

For most languages, `grep` is a foot-gun for architecture rules. A dependency can
be transitive (a → b → c), function-local, or aliased on import — a textual
`grep` sees none of these and waves through code that actually breaks the layer.
You want a tool that understands the *dependency graph*, not the text.

The exception is C and C++, where a dependency genuinely *is* a textual
`#include` line. There, a `grep` check is both legitimate and, as it happens, the
realistic state of the art (see the table below).

## The pattern

super-harness lets a ratified *decision record* carry an inline runnable check and
a counterexample. Any dependency checker whose CLI **exits non-zero on a forbidden
edge** can be wired in:

1. Add an executable check and a counterexample to the decision's Markdown body.
2. At `ratify`, super-harness runs a two-sided *bite-test*: the check must pass on
   your current code **and** fail when the counterexample is injected. If it
   doesn't bite, ratify is refused — no hollow checks.
3. `decision check` (local and in CI) then runs the check on every invocation;
   violating code is blocked.

See the decision-record lifecycle in [getting started](getting-started.md) §10;
this guide is about the *check* you put inside it.

### Two mechanics that trip up first-time users

- **Read the bite output.** The bite-test only asserts your check *exits
  non-zero* with the counterexample present — it does not verify *why*. Eyeball
  the failure and confirm the tool named the forbidden edge, not a parse, compile,
  or config error. A check that fails for the wrong reason is a hollow check that
  slipped past the anti-hollow gate.
- **Commit the config and any imported source first — and keep them in scope.**
  The bite-test copies only *git-tracked* files that fall inside your configured
  source scope into its sandbox, then injects the counterexample. Before you
  `ratify`: commit your checker config (`.importlinter`, `.dependency-cruiser.js`,
  `.go-arch-lint.yml`, …) **and** any real source module your counterexample
  imports, and make sure both sit inside your source scope. Otherwise the
  sandboxed tool runs without its config or import target, errors out, and falsely
  certifies as "bites."

## Worked example — Python (import-linter)

This is the example super-harness runs on *itself*: the `d-core-is-base` decision
(`docs/decisions/d-core-is-base.md`) enforces an import-graph contract on every PR
via CI. Reproduce the shape in your repo.

Declare the contract in `.importlinter`:

```ini
[importlinter]
root_package = myapp

[importlinter:contract:core-independent]
name = core must not import the web layer
type = forbidden
source_modules =
    myapp.core
forbidden_modules =
    myapp.web
```

Put the check and counterexample in the decision body (the outer four-backtick
fence is only to display the nested blocks):

````markdown
```check
PYTHONPATH=src lint-imports --config .importlinter --contract core-independent --no-cache
```

```counterexample path=src/myapp/core/_ce.py
from myapp.web import handler  # forbidden: core importing the web layer
```
````

`lint-imports` exits 0 when the contract is kept and 1 when broken, so a forbidden
edge fails the check. import-linter is a static import-graph analyzer — it needs
no build, and it catches the transitive and function-local edges `grep` misses.

## Language support

super-harness's check runner is language-agnostic — it runs any shell command and
treats a non-zero exit as a violation. What varies by language is whether a mature
*static* dependency-checker CLI exists that fits the bite-test. Two independent
questions decide the fit:

- **(a) Does a static, compile-free CLI fit the bite-test?** The bite-test injects
  one counterexample file into a copy of your source tree and requires the check
  to fail *because of the rule*. A tool that first needs a full build — where a
  compile failure is indistinguishable from a rule violation — does not fit.
- **(b) Is there a mature, dedicated dependency-direction tool?** A different
  question: a language can pass (a) with plain `grep` yet have no dedicated tool.

Verified 2026-07-04:

| Language | (a) fits bite-test? | (b) mature dedicated tool? | Tool / command |
|---|---|---|---|
| Python | yes — static graph | yes — import-linter | `lint-imports` (exit 0 kept / 1 broken) |
| TypeScript / JS | yes — static, bare tree | yes — dependency-cruiser | `depcruise` (non-zero = error-severity count) |
| Go | yes — import-resolvable | yes — go-arch-lint | `go-arch-lint check` (exit 0 / 1) |
| Rust | cycles yes / directional needs compile | partial | `cargo modules … --acyclic` (cycles only) |
| Java / JVM | no — needs full compile | yes — ArchUnit | build-run test library |
| C / C++ | yes — via `grep` on #include | no mature static tool | a `grep` check |

### TypeScript / JavaScript — dependency-cruiser

> Illustrative, not executed in this repo. The command and exit-code behaviour
> were verified 2026-07-04; adapt paths to your project.

`.dependency-cruiser.js`:

```js
module.exports = {
  forbidden: [
    {
      name: "core-not-web",
      severity: "error",
      from: { path: "^src/core/" },
      to:   { path: "^src/web/" }
    }
  ]
};
```

Check `depcruise --config .dependency-cruiser.js src`; counterexample
`src/core/_ce.ts`:

```ts
import { handler } from "../web/handler";  // forbidden: core → web
export const x = handler;
```

depcruise exits non-zero once per error-severity violation. Internal path-based
rules need no installed dependencies; it does need a modern Node runtime (22, 24,
or ≥ 26).

### Go — go-arch-lint

> Illustrative, not executed in this repo. The command and exit-code behaviour
> were verified 2026-07-04; adapt paths to your project.

`.go-arch-lint.yml` uses an allow-list model — a component may depend only on what
you list, so omitting web from core's list forbids it:

```yaml
version: 3
workdir: internal
components:
  core: { in: core/** }
  web:  { in: web/** }
deps:
  core:
    mayDependOn: []
```

Check `go-arch-lint check`; counterexample `internal/core/ce.go`:

```go
package core

import "example.com/app/internal/web" // forbidden: core → web

var _ = web.Handler
```

`go-arch-lint check` exits 0 when the architecture is respected and 1 on a
violation. It needs a resolvable go.mod, not a full build.

### C / C++ — a grep check

> Illustrative, not executed in this repo. grep semantics are self-evident.

No mature open-source *static* C/C++ architecture enforcer exists today:
cpp-dependencies doesn't enforce and is abandoned, CppDepend is commercial, and
Bazel's layering check needs a full build. But because a C/C++ dependency *is* a
textual `#include`, a `grep` check is legitimate and adequate:

```sh
! grep -rIn '#include[[:space:]]*["<]web/' src/core/
```

Counterexample `src/core/ce.c`:

```c
#include "web/handler.h"
```

The leading `!` inverts grep's exit code, so the check fails (exits non-zero) when
a forbidden include is present — exactly what the bite side of the bite-test
needs.

### Rust and Java — current gaps

- **Rust.** The mature tool cargo-modules (`cargo modules dependencies --lib
  --acyclic`) enforces only *acyclicity*, not a directional "A must not depend on
  B" rule, and exits non-zero on a cycle. Directional layering exists only in
  young, low-adoption, nightly-only, compile-based tools (cargo-pup,
  layered-crate) whose process exit codes aren't documented — smoke-test one in a
  throwaway sandbox before gating on it.
- **Java / JVM.** ArchUnit is powerful and mature, but it is a JUnit *test
  library* run through your build — a compile failure of an injected
  counterexample is indistinguishable from a rule violation, so it does not fit
  the compile-free bite-test. Use it as an ordinary CI test instead; the
  decision-check bite-test is the wrong home for it.

## Sources

Tool facts verified 2026-07-04 against primary sources:

- import-linter — https://import-linter.readthedocs.io/ · https://github.com/seddonym/import-linter
- dependency-cruiser — https://github.com/sverweij/dependency-cruiser
- go-arch-lint — https://github.com/fe3dback/go-arch-lint
- cargo-modules — https://github.com/regexident/cargo-modules
- ArchUnit — https://www.archunit.org/
- Thoughtworks Technology Radar v34 (2026) — "architecture drift reduction with LLMs", "codebase cognitive debt", "complacency with AI-generated code"
=== END GUIDE BODY ===

- [ ] **Step 2: Verify the doc-refs gate stays green.** Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check 2>&1 | grep -v "unknown event type"`. Expected: no dead-reference finding naming `docs/architecture-fitness.md`. If one appears, an identifier leaked into an inline backtick — move it into a fence or make it plain text (see Authoring invariants).
- [ ] **Step 3: Verify the Python example is real.** Run: `grep -n "lint-imports" docs/decisions/d-core-is-base.md`. Expected: `PYTHONPATH=src lint-imports --config .importlinter --contract core-is-base --no-cache` — the guide's command form is verbatim-faithful (only the contract id differs).
- [ ] **Step 4: Commit.** `git add docs/architecture-fitness.md && git commit -m "docs: add architecture-fitness adoption guide"`

---

## Task 2: Link the guide from `getting-started.md` §10

**Files:** Modify `docs/getting-started.md` (§10, after the context-only-decisions blockquote, before "**Attestation trail.**")

- [ ] **Step 1: Insert the pointer.** After this existing blockquote in §10:

```
> Decisions you can't reduce to a runnable check are recorded as **context** — they
> show up in the `hard:context` ratio `decision check` prints, but never gate. This
> is deliberate: there is no ground truth to mechanically judge prose intent against.
```

insert a blank line then:

```
> **Arming architecture rules.** The grep example above is a security rule; the
> flagship use is dependency-direction / layering rules ("core must not import the
> web layer"), where grep is a foot-gun and an import-graph checker is the right
> tool. See [Arm an architecture rule](architecture-fitness.md) for a
> language-by-language guide (Python / TypeScript / Go, and the honest gaps for
> Rust / Java / C-C++).
```

- [ ] **Step 2: Verify gates.** `PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check 2>&1 | grep -v "unknown event type"` — no finding for `docs/getting-started.md`.
- [ ] **Step 3: Commit.** `git add docs/getting-started.md && git commit -m "docs(getting-started): link architecture-fitness guide from §10"`

---

## Task 3: Add README pointers

**Files:** Modify `README.md` (`## Links` list; and the "Decision conformance" bullet under `## What v0.1 ships`)

- [ ] **Step 1: Add a `## Links` entry** immediately after the "Getting started" line so the list reads:

```
- [Getting started](docs/getting-started.md)
- [Arm an architecture rule](docs/architecture-fitness.md)
- [Architecture](docs/ARCHITECTURE.md)
- [CLI reference](docs/cli-reference.md)
- [Adapter docs](docs/adapters/)
- [Demo: OpenSpec + Claude Code](examples/demo-openspec-claude/)
```

- [ ] **Step 2: Append a sub-bullet to the "Decision conformance" bullet** under `## What v0.1 ships`. The bullet's sub-list ends with this line:

```
    vs recorded-as-context-only.
```

Immediately after that line (and before the next top-level bullet `- **Derivable-doc drift gate**`), insert:

```
  - **Architecture fitness** — the executable-check layer's flagship use is
    dependency-direction / layering rules via an import-graph checker; see
    [Arm an architecture rule](docs/architecture-fitness.md).
```

- [ ] **Step 3: Verify gates.** `PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check 2>&1 | grep -v "unknown event type"` — no finding for `README.md`.
- [ ] **Step 4: Commit.** `git add README.md && git commit -m "docs(readme): link architecture-fitness guide"`

---

## Task 4: Lifecycle + full verification (self-host)

**Files:** none (lifecycle events + gates)

- [ ] **Step 1: `plan_ready` is already emitted** (this plan drove the change to `AWAITING_PLAN_REVIEW`). If re-running from scratch: `PATH="$(pwd)/.venv/bin:$PATH" super-harness adapter scan-once superpowers` then `super-harness status`.
- [ ] **Step 2: Plan review (two actors, out-of-band: Claude subagent + `codex exec --sandbox read-only`), then approve + start implementation.** `super-harness review approve 2026-07-04-architecture-fitness-doc --reviewer plan-reviewer` (bare is fine — only code-reviewer requires a verdict file) && `super-harness implementation start 2026-07-04-architecture-fitness-doc`.
- [ ] **Step 3: Full local gate suite** (after Tasks 1–3 committed). `PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check && super-harness decision check` — both exit 0 (no decision delta). Run the test suite + `mypy` only if `done` runs them; docs-only, so no delta.
- [ ] **Step 4: `done` → code review (verdict-file flow) → attest.**
  - `super-harness done 2026-07-04-architecture-fitness-doc` → `AWAITING_CODE_REVIEW`.
  - `super-harness review prepare 2026-07-04-architecture-fitness-doc --reviewer code-reviewer` — writes the bundle (with `bundle_digest`) to `.harness/pending-reviews/2026-07-04-architecture-fitness-doc/code-reviewer.bundle.json`. Requires a clean in-scope tree (Tasks 1–3 committed).
  - Two-actor code review against that bundle; produce a verdict JSON (copy `bundle_digest` from the bundle; mark every checklist item pass/na; list findings) per `docs/cli-reference.md` → sections "review prepare" / "review approve".
  - `super-harness review approve 2026-07-04-architecture-fitness-doc --reviewer code-reviewer --verdict-file <verdict.json>` → `READY_TO_MERGE`. (Bare `review approve --reviewer code-reviewer` is REJECTED — `--verdict-file` is mandatory for code-reviewer.)
  - `super-harness attest write 2026-07-04-architecture-fitness-doc` then `git add .harness/attestations/ && git commit -m "chore: attest architecture-fitness-doc"`.
- [ ] **Step 5: Confirm merge gate satisfiable.** `PATH="$(pwd)/.venv/bin:$PATH" super-harness attest verify --base main --head HEAD`. Expected pass — the five scope files match the diff subjects (`.harness/attestations/*` is exempt; `.harness/state.yaml` is gitignored). If a "not in scope" blocker appears, add the path to frontmatter `scope.files`, re-attest, re-run.
- [ ] **Step 6: Open the PR** with the metadata block naming the slug (getting-started §7).

---

## Self-review (author checklist — completed)

- **Spec coverage:** guide (Task 1, full literal body) covers design §4/§5; pointers (Tasks 2–3) cover design §3/§7; lifecycle+scope+attest (Task 4) covers design §7/§8. ✓
- **Placeholder scan:** the guide body is literal publishable Markdown (not an outline); all per-language snippets are concrete + verified; no TBD/TODO. ✓
- **doc-refs safety:** config keys / API names + capitalized proper nouns are inside fenced blocks or plain text; only hyphen/dot/lowercase tool names appear in inline backticks. Task 1 Step 2 is the backstop. ✓
- **Honesty grades:** Python = CI-run self-example; TS/Go/C-C++ carry "Illustrative, not executed" notes; Rust/Java = boundary notes. ✓
- **Lifecycle mechanics:** code-review uses `review prepare` + `--verdict-file` (mandatory for code-reviewer); plan-review bare-approve is allowed. ✓
- **Scope covers all changed files:** five files in `scope.files`; `attest verify` (Task 4 Step 5) is the backstop. ✓
