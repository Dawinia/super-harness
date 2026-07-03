<!--
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
-->

# Architecture-fitness Adoption Guide — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **Design:** see `docs/plans/2026-07-04-architecture-fitness-doc-design.md` for the rationale behind every decision below (two-axis language table, three honesty grades, the two adopter-teaching mechanics, the doc-refs authoring rule).

**Goal:** Ship a discoverable, language-portable guide teaching adopting teams to arm dependency-direction architecture rules with super-harness's existing decision-check + bite-test mechanism, plus two cross-link pointers.

**Architecture:** Docs-only. One new file `docs/architecture-fitness.md`; three cross-link edits (getting-started §10, README ×2). No code, no new decision, no new mechanism. Runs through the normal super-harness self-host lifecycle; the merge gate is satisfied by a plan-declared scope covering all five changed files + an attestation.

**Tech Stack:** Markdown prose. Verification via `super-harness doc check` / `decision check` and the existing CI suite. External tool facts verified 2026-07-04.

---

## File structure

| File | Responsibility |
|---|---|
| `docs/architecture-fitness.md` (new) | The guide: why → pattern → Python worked example → two-axis language table → boundary → sources. |
| `docs/getting-started.md` (modify) | One pointer sentence in §10 point-4 linking to the new guide. |
| `README.md` (modify) | A `## Links` entry + a one-line pointer on the "Decision conformance" bullet. |

## Authoring invariants (apply to every task)

- **doc-refs gate rule (verified against `core/doc_refs.py`):** the gate extracts only *inline single-backtick* spans and flags single identifiers with a `_`/camelCase boundary absent from source. Therefore **all tool config keys and API method names** (mayDependOn, source_modules, forbidden_modules, severity, resideInAPackage, dependOnClassesThat, noClasses, layering_check, restrict_imports, dependencyTypes) MUST appear **only inside fenced ``` code blocks**, never in inline prose backticks. Hyphen/dot names (`lint-imports`, `go-arch-lint`, `.importlinter`, `dependency-cruiser`) and plain lowercase words (`depcruise`) are safe inline.
- **Honesty grades (label in-doc):** Grade A = CI-run (Python, anchored on live `d-core-is-base`); Grade B = verified tool invocation (commands/exit codes fetched 2026-07-04); Grade C = illustrative-not-executed (the TS/Go config + counterexample snippets). Do not let B bleed into C.
- **Python worked example must reproduce the live command verbatim:** `PYTHONPATH=src lint-imports --config .importlinter --contract <id> --no-cache`.

---

## Task 1: Create `docs/architecture-fitness.md`

**Files:**
- Create: `docs/architecture-fitness.md`

- [ ] **Step 1: Write the full guide.** Create `docs/architecture-fitness.md` with the content specified in the appendix "GUIDE CONTENT" at the end of this plan (reproduced verbatim; it contains the exact verified per-language snippets and honesty labels).

- [ ] **Step 2: Verify the doc-refs gate stays green.**

Run: `PATH="$(pwd)/.venv/bin:$PATH" super-harness doc check 2>&1 | grep -v "unknown event type"`
Expected: no dead-reference finding naming `docs/architecture-fitness.md`. If an API/config identifier is flagged, it leaked into an inline backtick — move it inside a fenced block.

- [ ] **Step 3: Verify the Python worked example is real.**

Run: `grep -n "lint-imports" docs/decisions/d-core-is-base.md`
Expected: the live line `PYTHONPATH=src lint-imports --config .importlinter --contract core-is-base --no-cache` — confirming the guide's command form is verbatim-faithful (only the contract id differs).

- [ ] **Step 4: Commit.** `git add docs/architecture-fitness.md && git commit -m "docs: add architecture-fitness adoption guide"`

---

## Task 2: Link the guide from `getting-started.md` §10

**Files:**
- Modify: `docs/getting-started.md` (§10, after the context-only-decisions blockquote, before "**Attestation trail.**")

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

**Files:**
- Modify: `README.md` (`## Links` list; and the "Decision conformance" bullet under `## What v0.1 ships`)

- [ ] **Step 1: Add a `## Links` entry** immediately after the "Getting started" line so the list reads:

```
- [Getting started](docs/getting-started.md)
- [Arm an architecture rule](docs/architecture-fitness.md)
- [Architecture](docs/ARCHITECTURE.md)
- [CLI reference](docs/cli-reference.md)
- [Adapter docs](docs/adapters/)
- [Demo: OpenSpec + Claude Code](examples/demo-openspec-claude/)
```

- [ ] **Step 2: Append a sub-bullet to the "Decision conformance" bullet** under `## What v0.1 ships`, at the end of that bullet's sub-list (match the existing two-space indent):

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

- [ ] **Step 1: Emit `plan_ready` from this plan's scope.** `PATH="$(pwd)/.venv/bin:$PATH" super-harness adapter scan-once superpowers` then `super-harness status`. Expected: change in `AWAITING_PLAN_REVIEW` with scope = the five frontmatter files.
- [ ] **Step 2: Plan review (two actors, out-of-band: Claude subagent + `codex exec --sandbox read-only`), then:** `super-harness review approve 2026-07-04-architecture-fitness-doc --reviewer plan-reviewer` && `super-harness implementation start 2026-07-04-architecture-fitness-doc`.
- [ ] **Step 3: Full local gate suite.** `super-harness doc check && super-harness decision check` (both exit 0; no decision delta). Run the test suite + `mypy` if `done` runs them — docs-only, so no delta.
- [ ] **Step 4: `done` → code review → attest.** `super-harness done 2026-07-04-architecture-fitness-doc`; two-actor code review out-of-band; `super-harness review approve ... --reviewer code-reviewer`; `super-harness attest write 2026-07-04-architecture-fitness-doc`; commit `.harness/attestations/`.
- [ ] **Step 5: Confirm merge gate satisfiable.** `super-harness attest verify --base main --head HEAD`. Expected pass — five scope files match diff subjects. If a "not in scope" blocker appears, add the path to frontmatter `scope.files`, re-attest, re-run.
- [ ] **Step 6: Open the PR** with the metadata block naming the slug (getting-started §7).

---

## Self-review (author checklist — completed)

- **Spec coverage:** guide (Task 1) covers design §4; pointers (Tasks 2–3) cover design §3/§7; lifecycle+scope+attest (Task 4) covers design §7/§8. ✓
- **Placeholder scan:** GUIDE CONTENT appendix has concrete verified snippets; no TBD/TODO. ✓
- **doc-refs safety:** all config keys / API names inside fenced blocks; only hyphen/dot/lowercase tool names inline. ✓
- **Honesty grades:** Python = CI-run self-example; TS/Go carry "Illustrative (not executed)" notes; Rust/Java = boundary notes. ✓
- **Scope covers all changed files:** five files in `scope.files`; `attest verify` (Task 4 Step 5) is the backstop. ✓

---

## Appendix: GUIDE CONTENT (verbatim body of `docs/architecture-fitness.md`)

See the design doc §4/§5 and the verified snippets below. The implementer writes this as the file body:

- **# Arm an architecture rule** — intro: agents violate architecture rules; human review is late/non-scaling (Thoughtworks Radar v34); prose in AGENTS.md doesn't hold; super-harness ships a mechanical check.
- **## Why not just grep?** — transitive/function-local/aliased imports defeat grep for rich-import languages; C/C++ is the exception (deps are textual `#include`).
- **## The pattern** — decision record carries check + counterexample; `ratify` bite-test (pass on clean, fail on counterexample); `decision check` enforces. Plus the two mechanics: **Read the bite output** (bite-test only asserts exit≠0, confirm rule attribution) and **Commit the checker config first** (sandbox copies only git-tracked files).
- **## Worked example — Python (import-linter)** [Grade A]:

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

check + counterexample (inside the decision `.md`):

    ```check
    PYTHONPATH=src lint-imports --config .importlinter --contract core-independent --no-cache
    ```

    ```counterexample path=src/myapp/core/_ce.py
    from myapp.web import handler  # forbidden: core importing the web layer
    ```

Note it is the same mechanism this repo runs on itself via `d-core-is-base`.

- **## Language support** — two-axis explanation + table:

| Language | (a) fits bite-test? | (b) mature dedicated tool? | Tool / command |
|---|---|---|---|
| Python | yes (static graph) | yes — import-linter | `lint-imports` (exit 0 kept / 1 broken) |
| TypeScript / JS | yes (static, bare tree) | yes — dependency-cruiser | `depcruise` (non-zero = error-severity count) |
| Go | yes (import-resolvable) | yes — go-arch-lint | `go-arch-lint check` (exit 0 / 1) |
| Rust | cycles yes / directional needs compile | partial | `cargo modules … --acyclic` (cycles only) |
| Java / JVM | no — needs full compile | yes — ArchUnit | build-run test library |
| C / C++ | yes — via grep on #include | no mature static tool | a grep check |

- **### TypeScript / JavaScript — dependency-cruiser** [Grade C: "Illustrative (not executed in this repo); command + exit-code verified 2026-07-04"]:

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

check `depcruise --config .dependency-cruiser.js src`; counterexample `src/core/_ce.ts`:

```ts
import { handler } from "../web/handler";  // forbidden: core → web
export const x = handler;
```

Exits non-zero per error-severity violation; internal path rules need no node_modules; needs Node `^22 || ^24 || >=26`.

- **### Go — go-arch-lint** [Grade C: same illustrative label]:

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

check `go-arch-lint check`; counterexample `internal/core/ce.go`:

```go
package core

import "example.com/app/internal/web" // forbidden: core → web

var _ = web.Handler
```

Exits 0 respected / 1 violation; needs a resolvable `go.mod`, not a full build.

- **### C / C++ — a grep check** — no mature static enforcer (cpp-dependencies abandoned; CppDepend commercial; Bazel needs full build); grep is legitimate here:

```sh
! grep -rIn '#include[[:space:]]*["<]web/' src/core/
```

counterexample `src/core/ce.c`:

```c
#include "web/handler.h"
```

The leading `!` inverts the exit code so the check fails when a forbidden include is present.

- **### Rust and Java — current gaps** — Rust: `cargo-modules` enforces acyclicity only; directional layering only via young nightly/compile tools (verify exit code in a sandbox first). Java: ArchUnit is a JUnit test library run through the build — a compile failure is indistinguishable from a rule violation, so it does not fit the compile-free bite-test; use it as an ordinary CI test.

- **## Sources** (verified 2026-07-04): import-linter, dependency-cruiser, go-arch-lint, cargo-modules, ArchUnit official repos/docs; Thoughtworks Technology Radar v34 (2026).
