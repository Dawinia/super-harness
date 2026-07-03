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
