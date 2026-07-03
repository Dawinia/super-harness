# Architecture-fitness adoption doc — Design

> Design rationale for a new `docs/architecture-fitness.md` guide that teaches
> adopting teams how to arm dependency-direction architecture rules with
> super-harness. Companion implementation plan: `2026-07-04-architecture-fitness-doc-plan.md`.
> Research backing: `private/research/2026-07-03-feedforward-next-target.md`.

## 1. Problem

super-harness already ships the *mechanism* to mechanically enforce an
architecture rule: `core.check_runner.run_one_check` runs any shell command in a
ratified decision's ` ```check ` block, treats a non-zero exit as a violation,
and `ratify`'s two-sided bite-test proves the check actually bites (passes on
clean code, fails when a counterexample is injected). This is generic — nothing
is hardcoded to import-linter or to the `super_harness` package. This repo
dogfoods it: `docs/decisions/d-core-is-base.md` + `.importlinter` run an
import-graph contract on every PR.

**But an adopting team cannot discover or learn this.** The only place the docs
teach "bind a decision to a runnable check" is `getting-started.md §10`, and its
sole worked example is a **`grep`-based** check (`! grep -rIn "md5(.*password"`).
The flagship use-case — dependency-direction / layering architecture rules,
which external research (Thoughtworks Radar v34, 2026) repeatedly names as a
current, endorsed mechanism against AI coding agents violating architecture — is
untaught. Worse, `grep` is a documented foot-gun for exactly this *in
rich-import languages*: as `d-core-is-base`'s own body says, grep "sees only
direct textual imports and is blind to the transitive and function-local edges
that actually break layering."
So a team reading §10 forms the mental model "super-harness checks = grep" and
writes a hollow architecture check.

## 2. Goal & non-goals

**Goal:** make "arm an architecture rule" a first-class, discoverable, taught,
language-portable adoption path — delivering value to *adopting teams*.

**Non-goals (explicit):**

- This is **not** a dogfood/bite cut. We are not arming a second super-harness-
  specific rule. `d-core-is-base` is *our* rule; arming another one adds no
  product value. This cut ships *product* value (adoption docs), so it will not
  and is not expected to produce an in-anger tripwire. The capability-convergence
  ledger entry must record it as B-track / adopter-value, not as a value-bleed.
- No new mechanism, no new CLI command, no new tier-1 decision, no code changes.
  The mechanism already works; the gap is purely teaching/discoverability.
- Not a per-language example *project* we execute in CI (see §6 honesty).

## 3. What we build

Two things — one new file plus a small set of pointers into it:

1. **A new guide `docs/architecture-fitness.md`** — the discoverable home for
   the flagship capability.
2. **Pointers into it** (three edits, all cross-links, no duplicated content):
   - a link appended to `getting-started.md §10`'s executable-check discussion
     (point 4, where the grep example lives);
   - in `README.md`: a link under `## Links`, and a one-line pointer appended to
     the existing "Decision conformance" bullet under `## What v0.1 ships`.

That's the whole surface. Deliberately lean: the disease is *undiscoverable +
untaught*, and one well-placed, well-linked doc is the proportionate cure.

## 4. Document structure (`docs/architecture-fitness.md`)

1. **Why (the pain).** Agents repeatedly violate architecture rules; human review
   is a late, non-scaling catch (cite research one-liners). Why advisory text in
   AGENTS.md/CLAUDE.md doesn't hold (agents read it and violate anyway). Why
   `grep` is a foot-gun *for rich-import languages* — and the honest nuance that
   for C/C++ (textual `#include`) grep is actually adequate. This nuance keeps the
   doc from dogmatism and makes it trustworthy.

2. **The pattern (language-agnostic skeleton).** Any dependency checker *whose CLI
   produces a rule-attributable non-zero exit on a forbidden edge* → wire it as a
   decision's ` ```check ` block + a ` ```counterexample ` → `ratify` proves it
   bites → CI `decision check` enforces it un-bypassably. Show the shape once,
   abstractly. **Must teach two non-obvious mechanics** (see §5 for why they are
   load-bearing):
   - **Read the bite output.** `ratify`'s bite-test only asserts the check exits
     non-zero when the counterexample is present — it does *not* verify the failure
     is attributable to the rule. So the adopter must eyeball the bite output and
     confirm the tool named *the forbidden edge*, not a parse/compile/config error.
     A check that fails for the wrong reason is a hollow check that passed the
     anti-hollow gate.
   - **Commit the checker config first.** The bite-test sandbox copies only
     *git-tracked* files (plus the injected counterexample). So the checker's
     config (`.importlinter` / `.dependency-cruiser.js` / `.go-arch-lint.yml`) —
     and any real source the counterexample imports — must be committed *before*
     `ratify`, or the sandboxed tool runs without its config, errors out, exits
     non-zero, and falsely certifies as "bites". This is the single most likely
     first-run failure.

3. **Worked example — Python (the CI-verified anchor).** A complete, minimal
   import-linter `forbidden` contract + a 3-line decision `.md` (` ```check ` +
   ` ```counterexample `) walked through `ratify`. The check line must reproduce
   *this repo's live* `d-core-is-base` command verbatim
   (`PYTHONPATH=src lint-imports --config .importlinter --contract <id> --no-cache`)
   and point at `d-core-is-base` + `.importlinter` as proof the pattern runs in
   real CI every PR. This is the one example we can and do execute.

4. **Language capability table (two axes).** The heart of the doc, and the most
   honest, most adopter-useful part. See §5. It sorts languages on **two distinct
   axes** kept explicitly separate: (a) does a static, compile-free CLI *fit the
   bite-test*, and (b) does a *mature, dedicated* dependency-direction tool exist.
   Languages that are yes on both axes get a worked snippet: verified command + an
   *illustrative* forbidden-rule config + counterexample (see §6 on the honesty
   label).

5. **Honest boundary.** State plainly which languages fail axis (a) — the bite-test
   fit — because their tools are compile/test-embedded (a build failure is
   indistinguishable from a rule violation → false green), and which fail axis (b)
   — no mature dedicated tool — even where a plain `grep` check does fit. Don't
   oversell; name the gaps and the dead projects.

6. **Sources.** A short appendix linking every load-bearing tool claim (verified
   2026-07-04) so a reader can check versions/maintenance themselves.

## 5. The language capability table — two axes (verified 2026-07-04)

Verified live against GitHub / crates.io / npm / PyPI by the research agents
(full data + sources in the research file). **What the bite-test actually
proves, precisely:** `check_runner.bite_test` copies the tracked tree into a
sandbox, injects one counterexample file, runs the check command, and asserts
only that it **exits non-zero**. It does *not* attribute the failure to the rule.
Two consequences drive the whole table:

- The check must produce a **rule-attributable non-zero exit** when run against a
  *copied source tree with ambient tooling*. A tool that first needs a full build
  or compile (Java/JVM) breaks this: a compile failure of the injected file exits
  non-zero too, so a check that really only detects "does the tree compile" passes
  the anti-hollow gate as a false green. This is axis (a): **bite-test fit**.
- Separately, a language may *fit* the bite-test (even with plain `grep`) yet have
  **no mature, dedicated** dependency-direction tool. That is axis (b):
  **mature-tool availability** — a different question from (a).

| Language | Static CLI fits bite-test? (a) | Mature dedicated tool? (b) | Tool / command | Doc treatment |
|---|---|---|---|---|
| Python | ✅ static graph | ✅ import-linter | `PYTHONPATH=src lint-imports --config .importlinter --contract <id> --no-cache` — exit 0 KEPT / 1 BROKEN (source-confirmed) | **Worked, CI-run** (anchors on live `d-core-is-base`) |
| TS/JS | ✅ static, bare tree | ✅ dependency-cruiser (6.8k★) | `depcruise --config .dependency-cruiser.js src` — non-zero = count of `error`-severity violations; needs Node `^22 \|\| ^24 \|\| >=26`; internal path rules need no `node_modules` | **Worked, illustrative** (command verified; config/CE not executed here) |
| Go | ✅ import-resolvable (no full build) | ✅ go-arch-lint (510★) | `go-arch-lint check` — exit 0 / 1 (doc-confirmed); needs resolvable `go.mod` | **Worked, illustrative** (command verified; config/CE not executed here) |
| Rust | ✅ (cycles) / ⚠️ (directional) | ⚠️ partial | `cargo modules dependencies --lib --acyclic` enforces **acyclicity only**; directional "A ⊥ B" exists only in young, nightly, compile-based tools (`cargo-pup` 47★, `layered-crate` 2★, exit codes undocumented) | **Cycle rule + honest gap note** |
| Java / JVM | ❌ needs full compile / test-embedded | ✅ ArchUnit (3.8k★) | ArchUnit is a JUnit library run via `mvn`/`gradle test` — powerful but a build/compile failure is indistinguishable from a rule violation in the sandbox | **Boundary note: "test-embedded / CI" pattern, not a bite-test** |
| C / C++ | ✅ via plain `grep` on `#include` | ❌ none (cpp-dependencies abandoned 2016; CppDepend commercial; Bazel `layering_check` needs full build) | `! grep -rE '#include\s+["<]ui/' core/` — compile-free, rule-attributable, bites cleanly | **Worked grep check + "no dedicated tool" note** |

Two honest nuances the table makes explicit:

- **C/C++ fits the bite-test** (axis a ✅ via `grep`) even though it has *no*
  dedicated tool (axis b ❌). It is not "no fit" — it is "grep is the realistic
  state-of-the-art", and here grep is legitimate because C/C++ deps genuinely
  *are* textual `#include` lines (unlike rich-import languages where grep misses
  transitive / function-local / aliased edges).
- **Java has a great tool but fails the bite-test axis** — the opposite corner
  from C/C++. Keeping (a) and (b) separate is what lets the table state both
  truths without contradiction.

The table's honesty *is* the value: it tells adopters the real per-language tool
landscape (gaps, dead projects, and the compile-vs-static boundary), rather than
pretending uniform coverage. It also serves the project's portability direction:
not language-locked, but not overclaiming either.

## 6. Honesty & verification handling

The core discipline of this cut (mirrors the project's "no hollow checks" ethos).
There are **three distinct honesty grades**, and the doc must not blur them:

- **Grade A — CI-run (Python only).** The Python walkthrough anchors on the live
  `d-core-is-base` contract this repo runs every PR — a real, executed proof, not
  a fresh snippet.
- **Grade B — verified invocation (TS / Go / Java / C-C++ tool facts).** Command,
  exit-code semantics, prerequisites, and maintenance were fetched from primary
  sources 2026-07-04. These are *facts about the tool*, confirmed.
- **Grade C — illustrative, not executed (the TS/Go `config` + `counterexample`
  snippets).** The forbidden-rule config and counterexample files for TS/Go are
  hand-authored to show the shape; they are **not run anywhere in this repo**, so
  they could be subtly wrong. They must be labeled as illustrative. Research
  verified the *command and exit codes*, not a working config/CE — do not let
  Grade-B verification bleed into a Grade-C claim.
  - *Optional strengthening (plan may choose):* run the TS/Go config+CE once in a
    throwaway local sandbox (Node/Go installed ad hoc, not in CI) and relabel those
    specific snippets Grade B-plus ("executed once locally 2026-07-04, not in CI").
    Only worth it if the toolchains are readily available; otherwise Grade C stands.
- We do **not** add Node/Go toolchains to our *CI* to run doc examples — that is
  scope-creep for a v0.1 Python tool.
- **Every external tool claim is sourced** in the doc's appendix with the date
  verified, so the reader can independently confirm currency.

## 7. Integration & self-gate considerations

- **Pointers:** one link at the end of `getting-started.md §10`; and in `README.md`
  a link under `## Links` plus a one-line pointer appended to the existing
  "Decision conformance" bullet under `## What v0.1 ships`. Keep
  `getting-started`'s 10-minute walkthrough lean — the deep guide lives in its own file.
- **Our own `doc check` (dead-reference gate) — concrete strategy, not
  hope.** Verified against `core/doc_refs.py`: the gate extracts only *inline*
  single-backtick spans line-by-line (`_BACKTICK_RE`) and flags a span iff it is a
  single identifier with a `_` or camelCase boundary that is absent from the source
  identifier set. Two consequences the plan must follow:
  1. **Keep API symbols in fenced ``` blocks (authoring convention, not a
     scanner feature).** The gate is not fenced-block-aware; it simply extracts
     *inline single-backtick* spans, and fenced content is not wrapped in inline
     backticks, so it is never extracted. Therefore all tool-specific config keys
     and API method names (`mayDependOn`, `resideInAPackage`, `dependOnClassesThat`,
     `noClasses`, `layering_check`, `restrict_imports`, `source_modules`,
     `forbidden_modules`, `dependencyTypes`, …) MUST appear only inside fenced code
     blocks — never in inline prose backticks. (A stray inline backtick pair *inside*
     a fence would still be extracted, so the rule is "no inline backticks around
     these symbols," not "fences are magic.")
  2. **Hyphen/dot names and plain lowercase words are already safe** — `lint-imports`,
     `go-arch-lint`, `dependency-cruiser`, `.importlinter`, and `depcruise` all fail
     the single-identifier / camelCase-or-snake heuristic, so inline backticks around
     them do not trip the gate.
  The plan still runs `super-harness doc check` locally as the backstop, but the
  authoring rule above is what keeps it green by construction.
- **No `sync --agents-md` impact:** the new doc is hand-written prose, not a
  derived artifact; AGENTS.md regeneration is unaffected.
- **Lifecycle:** runs through the normal super-harness change lifecycle. The
  declared scope must cover `docs/architecture-fitness.md`, `docs/getting-started.md`,
  and `README.md`. Tier hint: `Normal`.

## 8. How this change is verified

- `super-harness doc check`, `decision check`, and the full CI suite green. The
  doc-refs gate stays green *by construction* via the fenced-block authoring rule
  (§7), with `doc check` as the backstop.
- The Python example (Grade A) reproduces the live `d-core-is-base` command
  verbatim; confirm the walkthrough matches real output by running it here.
- TS/Go command facts (Grade B) were verified against primary sources 2026-07-04;
  their config+counterexample snippets (Grade C) are labeled illustrative and not
  executed in CI (plan may optionally run them once locally — §6).
- Spec-document-reviewer pass on this design; then plan review (two actors) on the
  implementation plan.

## 9. Risks & mitigations

- **Tool facts go stale.** Versions/maintenance move. Mitigation: date-stamp the
  sources appendix ("verified 2026-07-04") so staleness is visible, not silent.
- **Doc drift vs `getting-started §10`.** The grep example stays in §10 as the
  security-rule example; the new doc owns the architecture-rule story and §10 just
  links to it — no duplicated content to drift.
- **Perceived overclaim of portability.** Mitigated by the two-axis table
  explicitly showing the gaps (Rust directional, C/C++ no dedicated tool, Java
  bite-test mismatch) rather than pretending uniform coverage.
