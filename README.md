# super-harness

> The missing CI layer for spec-driven AI coding workflows.

## What is super-harness?

An open-source, CI-first, framework-agnostic, agent-agnostic harness that makes
AI coding deterministic and reliable. Spec-driven tools like Spec Kit, OpenSpec,
and Superpowers describe rules in markdown templates that agents read and
(probabilistically) comply with. A harness embeds those constraints in the
environment itself — hooks, CI, git, processes — so violations are blocked
deterministically, not just discouraged. super-harness sits on top of your
existing spec framework and agent; it is not a replacement for either.

## Install

```bash
pipx install super-harness
brew install gh && gh auth login   # gh is a prerequisite for init --setup-github
```

## Quickstart

```bash
super-harness init --setup-github                          # bootstrap repo + CI workflow
super-harness adapter install openspec                     # framework adapter
super-harness adapter install claude-code                  # agent adapter
super-harness change start "my-first-change"               # → INTENT_DECLARED
# framework adapter emits plan_ready from tasks.md         # → AWAITING_PLAN_REVIEW
super-harness review approve my-first-change --reviewer plan-reviewer   # → PLAN_APPROVED
super-harness implementation start my-first-change         # → IMPLEMENTATION_IN_PROGRESS
# ... your agent edits the code (the gate now allows it) ...
super-harness done                                         # verify → AWAITING_CODE_REVIEW
super-harness review approve my-first-change --reviewer code-reviewer   # → READY_TO_MERGE
```

The lifecycle now advances **end-to-end via the CLI**. The three reviewer-driven
transitions ship as verbs: `review approve | reject | skip` (emit `plan_approved` /
`code_review_passed`) and `implementation start` (emits `implementation_started`).
**super-harness does not run the review for you** — it enforces, via the gate, that a
review verdict is recorded before the lifecycle proceeds; you (or, per the injected
`AGENTS.md` protocol, your agent's own reviewer subagent) produce the verdict. The
per-reviewer **strategy** (`subagent` / `human` / `hybrid`) is set in
`.harness/policy.yaml` — pick `human` when a token budget rules out subagent review.
Unattended CI auto-review (a headless reviewer that emits the verdict with no human or
interactive agent present) is deferred — see "What v0.1 does NOT ship yet". The in-tree
demo [`examples/demo-openspec-claude/`](examples/demo-openspec-claude/) ships a
pre-seeded `.harness/` so you can inspect the harness in a non-trivial state without
running the flow yourself.

> Plain-mode (no framework adapter) `plan_ready` still has no public CLI emitter, so a
> pure-`plain` cold start stops at `INTENT_DECLARED`; use a framework adapter (OpenSpec
> emits `plan_ready` from `tasks.md`) for the full CLI path.

## What v0.1 ships

- **21 CLI command groups** spanning lifecycle (`init` / `change` / `plan` /
  `review` / `implementation` / `done` / `on-merge` / `status` / `sync`), gating
  (`verify` / `gate` / `pr` / `attest`), decision conformance (`decision` / `doc`),
  and infrastructure (`observe` / `event` / `state` / `adapter` / `sensor` /
  `verification`).
- **Reviewer verdict verbs** — `review approve | reject | skip` (emit
  `plan_approved` / `plan_rejected` / `code_review_passed` / `code_review_failed`)
  and `implementation start` (emits `implementation_started`). These advance the
  lifecycle deterministically; the gate enforces a verdict exists, the agent/human
  produces it. Per-reviewer strategy (`subagent` / `human` / `hybrid`) is
  configurable in `.harness/policy.yaml` and surfaced by `super-harness status`.
- **Hot-path PreToolUse gate** decided **in-process** (one `state.yaml` snapshot
  → the pure `PreToolUseGate`; no resident process on the decision path) — blocks
  Edit / Write tool calls in Claude Code when the current lifecycle state forbids
  them. An optional `observe` host watches framework artifacts out-of-band.
- **Cold-path PR gates** via CI — `pr validate` checks the PR metadata block
  and lifecycle state, `verify` runs the verification-runner sensor, `done`
  advances a change from `IMPLEMENTATION_IN_PROGRESS` to
  `AWAITING_CODE_REVIEW`, and `on-merge` emits the `merged`
  event after the PR lands on main (advancing the change directly to `ARCHIVED`).
- **Framework adapters** — OpenSpec (detects `openspec/changes/` and
  `openspec/specs/`, emits `intent_declared` from `proposal.md` and
  `plan_ready` from `tasks.md`, provides verification check `openspec validate
  <slug> --strict --json`), Superpowers (version-agnostic: discovers design/plan
  artifacts by a `change:` frontmatter marker rather than a fixed path, emits
  `intent_declared` from `stage: design` and `plan_ready` from `stage: plan`,
  lifting `scope` / `tier_hint` from the plan frontmatter),
  and Plain (fallback for repos without a framework).
- **Agent adapter** — Claude Code (writes hooks to `.claude/settings.json` for
  PreToolUse + SessionStart and injects an `AGENTS.md` subsection).
- **Three-layer verification** — baseline checks (lifecycle-ordering,
  scope-vs-plan-final) + adapter-provided checks (e.g.,
  OpenSpec strict validate) + user-defined `.harness/verification.yaml`.
- **Decision conformance — make a human decision actually bind the AI's code.**
  Ratified decision records live in `docs/decisions/` with `@decision:<id>` code
  anchors. `super-harness decision check` enforces three layers, deepest first:
  - **Referential integrity** — a code anchor naming no ratified record blocks;
    a ratified record with no anchor warns.
  - **Text-lock (the decision can't be silently rewritten)** — `ratify` freezes a
    hash of the decision body; if a ratified body later changes without a fresh
    re-ratify, `decision check` blocks. Re-ratify (re-stamping identity + time, all
    in the git diff) is the only unlock — an AI cannot self-clear.
  - **Executable checks (the code can't silently violate the decision)** — a
    decision may carry an inline runnable check + a counterexample; `ratify` proves
    the check *bites* (passes on current code, fails on the counterexample) before
    accepting it, and `decision check` runs it so violating code is blocked (exit 2).
    `--changed` runs only the checks whose anchored files moved (local speed; CI runs
    the full set); a `hard:context` ratio reports how much is mechanically enforced
    vs recorded-as-context-only.
  - **Architecture fitness** — the executable-check layer's flagship use is
    dependency-direction / layering rules via an import-graph checker; see
    [Arm an architecture rule](docs/architecture-fitness.md).
- **Derivable-doc drift gate** — `super-harness doc check` regenerates docs that
  have a generator (e.g. the CLI reference, the state-machine diagram) and blocks
  if the committed copy drifted; `--fix` rewrites them. Registered in
  `.harness/derived-docs.yaml`. (Prose docs without a generator are out of scope by
  design — there is no ground truth to diff them against.)
- **Bundled CI workflow** — `super-harness init --setup-github` deploys
  `.github/workflows/super-harness.yml` (7 jobs: pr-decorate, pr-validate,
  verification, attest-verify, decision-check, doc-check, on-merge) and
  `.github/pull_request_template.md` with the required metadata block. All GitHub
  operations go through `gh` — no webhooks, no PATs, no bot account.

## What v0.1 does NOT ship yet

Trust is built on honesty about scope. The following are not in v0.1 — most
are deliberately deferred to a later version; one is blocked by an upstream
bug (flagged explicitly).

**Framework adapters (v0.2+):**
- Spec Kit framework adapter
- Superpowers framework adapter
- BMAD framework adapter

**Agent adapters (v0.2+):**
- Cursor / Codex / Aider agent adapters — platform hook capabilities vary;
  Claude Code is the reference adapter for v0.1.

**Process / orchestration:**
- Unattended CI auto-review — a headless reviewer that produces the verdict
  with no human or interactive agent present (e.g. shelling out to a headless
  Claude run). v0.1 ships the verdict *recording* path (`review approve | reject |
  skip`) and the agent-driven review protocol (the injected `AGENTS.md` tells the
  Claude Code agent to dispatch its own reviewer subagent and record the verdict),
  but it does not itself run an LLM review. Tracked as a follow-up.
- Plain-mode `plan_ready` emitter — OpenSpec auto-emits `plan_ready` from
  `tasks.md`; a pure-`plain` repo has no public CLI emitter for it yet, so a
  plain cold start stops at `INTENT_DECLARED`.
- Daemon-autonomous event-driven dispatch — v0.1 uses CLI one-shot
  dispatchers (e.g., `super-harness on-merge` dispatches the merged-event
  sensors).

**Gates not yet wired:**
- Cold-path pre-commit / pre-push gates — need git-hook install
  infrastructure.
- `gate check pr-open` / `gate check pr-merge` — the underlying machinery
  ships via `pr validate`, but the `gate check` wiring is deferred.

**Blocked by upstream:**
- JSON `permissionDecision: deny` for Edit / Write — super-harness currently
  uses exit-2 block instead, because of an upstream Claude Code fail-open bug
  on Edit/deny.

**Platform / integration:**
- MCP integration — v0.2+.
- Windows support — v0.2+; the optional observer host currently uses POSIX
  `os.fork` and `fcntl.flock`.

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

## Links

- [Getting started](docs/getting-started.md)
- [Arm an architecture rule](docs/architecture-fitness.md)
- [Architecture](docs/ARCHITECTURE.md)
- [CLI reference](docs/cli-reference.md)
- [Adapter docs](docs/adapters/)
- [Demo: OpenSpec + Claude Code](examples/demo-openspec-claude/)

## License

MIT — see [`LICENSE`](LICENSE).
