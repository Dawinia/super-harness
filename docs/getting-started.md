# Getting started with super-harness

A 10-minute walkthrough of the full v0.1 lifecycle — from installing the CLI
to landing a PR with all gates green. By the end you'll have:

- A repo bootstrapped with the `.harness/` data plane and the bundled CI
  workflow.
- Two adapters installed (the OpenSpec framework adapter + the Claude Code
  agent adapter).
- One change driven through every gate: declared, implemented, verified,
  reviewed, merged, and archived.

The package-install examples in this guide assume a Unix shell (macOS or
Linux). Runtime support for `init` is broader and is described separately in
[Bootstrap a repo](#2-bootstrap-a-repo). For the full CLI surface see
[`cli-reference.md`](./cli-reference.md). For a runnable end-to-end example,
see [`examples/demo-openspec-claude/`](../examples/demo-openspec-claude/).

> **This is the full version of the README Quickstart.** The README shows the
> shortest path to *seeing* the gate work (ending at `INTENT_DECLARED` with a
> blocked edit). This guide takes a change all the way through every gate to a
> merged, archived PR.

---

## 1. Install the CLI

`super-harness` ships as a Python package; we recommend `pipx` so its
dependencies don't collide with your project's environment.

```bash
pipx install super-harness
super-harness --version
```

If you plan to use the bundled CI workflow (`init --setup-github`), you
also need the GitHub CLI:

```bash
brew install gh        # or your platform's package manager
gh auth login          # log in once per machine
```

That's it for one-time setup. Everything below is per-repo.

---

## 2. Bootstrap a repo

`cd` into the repo you want to harness. It can be brand new or an existing
project. `super-harness init` never deletes project code; it plans its managed
file changes and, in either interactive mode, shows the complete plan before
the first write.

```bash
cd path/to/your/repo
super-harness init --setup-github
```

The guided setup has five stages:

1. **Preflight** resolves the workspace and detects coding-agent integrations
   and review-producer executables without writing.
2. **Configuration** selects integrations and producers, chooses from reviewer
   models already configured in the relevant CLI, and resolves existing-file
   conflicts.
3. **Review before writes** shows the selected integrations, producers, models,
   GitHub choice, grouped create/update/preserve actions, and any local settings
   files that will receive a backup. In an interactive mode, the workspace is
   unchanged until this plan is accepted.
4. **Apply** performs the named operations. Fast writes become completed rows;
   genuinely long or external operations may show activity, but the wizard does
   not invent percentages.
5. **Outcome** reports the real elapsed time and one next command on success.
   On partial failure, the ledger keeps completed writes visible, names the
   failed step and exit code, and gives a recovery command such as
   `super-harness init --force` after the named problem is corrected; it does not
   attempt a broad rollback of user-owned files.

On a full interactive terminal, use the arrow keys to move, Space to toggle a
choice, and Enter to accept it. The filled or empty indicator shows whether an
option is selected; with color enabled, only the selected indicator turns green
so labels remain easy to scan. At the final review, **Back** returns to
configuration. **Ctrl+C** interrupts setup. Detected integrations and producers
are preselected and labeled `detected · recommended` on a fresh init. An
unavailable coding integration remains selectable but is not preselected; an
unavailable review producer is disabled, and selecting it explicitly is a
pre-write validation error. For each available reviewer, the wizard reads model
identifiers from the existing workspace profile and that reviewer's native CLI
configuration. One candidate is selected automatically; multiple candidates
are presented as a choice with their origin. If no model is configured, that
reviewer is disabled and setup can continue with another reviewer or human-only
review. The wizard never asks users to type a model identifier.

Interactive discovery reads `.harness/review-profiles.local.yaml`,
`~/.codex/config.toml`, and `~/.claude/settings.json` as applicable. It retains
only model identifiers and their display origins: credentials and unrelated
provider settings are never copied or shown. A malformed provider file disables
only that provider, remains byte-for-byte unchanged, and does not prevent init
from continuing. Scripts can bypass discovery with explicit
`--review-model SOURCE=MODEL` values; non-interactive init uses explicit flags
and persisted workspace defaults and does not inspect user CLI configuration.

A representative narrow terminal session looks like this (paths and selections
will reflect your machine):

```text
$ super-harness init --setup-github
┌ super-harness init
●  preflight: Inspected /work/my-project
│  Detection is read-only
◆  configuration: Choose integrations and reviews
◆ Integrations  (↑/↓ move · space select · enter confirm)
› ● Codex  detected · recommended
  ○ Claude Code  not detected
◆ Automated reviewers  (↑/↓ move · space select · enter confirm)
› ● Codex reviewer — runs via Codex CLI  gpt-5.2-codex
●  configuration: Configuration collected
◆  review: Review planned setup
│  Integrations
│    Codex
│  Automated reviewers
│    Codex  gpt-5.2-codex
│  GitHub
│    Ensure workflow and PR template
│  Files
│    Create    14 files
◆ Apply this plan?  Confirm and continue
●  review: Plan confirmed
◆  apply: Applying setup
✓  Harness configuration ready
✓  Configured integrations: codex.
●  outcome: Setup complete in 152ms
│  Next: super-harness status
└
```

If the reviewed plan will change an existing `.codex/hooks.json` or
`.claude/settings.local.json`, the **Files** group includes a **Back up** row
with that path.
The adapter transaction is frozen at review time: the original settings bytes,
desired bytes, and resolved `super-harness` executable paths are checked again
immediately before apply. If any of them changed, init stops before backing up or
writing and asks you to rerun configuration and review.

Running `super-harness init` again without `--force` keeps the recovery message
inside the same frame: run `super-harness status` first to inspect the current
setup. Use `super-harness init --force` only when you want to review and apply a
reconfiguration; it does not silently overwrite the existing setup.

TTY input with redirected output, `TERM=dumb`, or another cursor-limited
terminal uses the same stages in deterministic plain text. It asks exactly one
yes/no question per option and never asks for comma-separated input. If Unicode
is unsafe, the rail uses `|`, `+`, `*`, and `x`, while plain status rows use
words such as `OK`, `WARN`, and `FAIL`; color and glyphs never carry meaning by
themselves.

`--yes` skips only the final confirmation in an interactive mode. It does not
select integrations or producers, choose among multiple model candidates, or
resolve conflicts with existing files. When stdin is not a TTY, `init` preserves
the scriptable behavior: it does not prompt or read user CLI configuration and
applies immediately from explicit flags and existing workspace defaults, so CI
and redirected scripts do not need `--yes`.

The installed `init` entrypoint is designed for native Windows (including
Windows Terminal and PowerShell), macOS, Linux, and WSL. Its ASCII fallback,
Windows entrypoint, and stdlib settings-lock/liveness paths have automated test
coverage. A real Windows TTY session has not yet been manually verified, so
report terminal-specific rendering or key-handling differences if you encounter
them. This boundary is specifically for `super-harness init`; it does not claim
that every lifecycle, observer, or daemon command runs natively on Windows. The
Unix package-install commands in [Install the CLI](#1-install-the-cli) are
examples for that shell environment, not the boundary of `init` runtime support.

What `init --setup-github` applies after interactive confirmation (or
immediately when stdin is not a TTY):

1. Creates `.harness/` with `events.jsonl` (the append-only lifecycle log),
   tracked skeleton configuration, and `review-governance.yaml`. The derived
   `state.yaml` cache appears after the first lifecycle event, while
   `adapters.yaml` is created only when an integration is selected. Explicit
   selected review models are written to the gitignored, user-editable
   `review-profiles.local.yaml`. Selecting no producer creates a fully usable
   human-only review configuration. Init never installs a third-party agent or
   producer binary.
2. Writes `AGENTS.md` (or extends an existing one) with a `super-harness`
   section your AI agent will read.
   Selected integrations install their existing local gate hooks as one atomic
   settings transaction. A fresh local settings file creates no backup; changing
   an existing file creates exactly one sibling backup containing its original
   bytes; an unchanged reinstall neither writes nor backs up. Pass `--no-agent`
   to skip integration configuration.
3. Writes `.github/workflows/super-harness.yml` — the 7-job CI workflow
   (pr-decorate / pr-validate / verification / attest-verify / decision-check /
   doc-check / on-merge).
4. Writes `.github/pull_request_template.md` with the required metadata
   block that links a PR to a change.
5. Best-effort enables repo `auto-merge` + `squash` settings on GitHub
   (skipped silently if you don't have admin on the repo).
6. Lists `pre-commit` / `pre-push` in `.harness/gates.yaml` as planned
   cold-path gates. Actual `.git/hooks/` install is v0.2 — see the
   [Limitations](limitations.md) / OPEN-ITEMS #2.

Verify:

```bash
ls -la .harness/
cat .github/workflows/super-harness.yml | head -10
super-harness status
```

If you're not yet ready to wire up CI, run plain `super-harness init`
(without `--setup-github`) — everything still works locally; you just don't
get the CI workflow yet.

---

## 3. Install adapters

An *adapter* tells super-harness how to translate between its lifecycle
events and a specific framework (e.g. OpenSpec, Spec Kit) or agent (e.g.
Claude Code, Cursor). v0.1 ships:

- **Framework adapters:** `openspec`, `superpowers`, and `plain`.
- **Agent adapters:** `claude-code` and `codex` (experimental).

For this walkthrough we'll wire up the canonical pair:

```bash
super-harness adapter install openspec
super-harness adapter install claude-code
```

(If `init` detected a `.claude/` directory it already auto-installed
`claude-code`, so `adapter install claude-code` is just an idempotent re-run
here — run it explicitly only if you used `init --no-agent` or added `.claude/`
later.)

`adapter install openspec` does:

- Registers OpenSpec hooks (watches `openspec/changes/` for `proposal.md`
  and `tasks.md`).
- Adds adapter-provided verification checks to `.harness/verification.yaml`
  (e.g. `openspec validate <slug> --strict --json`).
- Persists the entry in `.harness/adapters.yaml` so future commands know
  it's enabled.

`adapter install claude-code` does:

- Writes `PreToolUse` + `SessionStart` hooks into `.claude/settings.local.json`
  (the per-machine, conventionally-gitignored settings file — not the committed
  `.claude/settings.json` — because the hook command pins a machine-specific
  absolute path) so Claude Code consults super-harness before every `Edit` /
  `Write` tool call.
- Extends `AGENTS.md` with a Claude-Code-specific subsection.

Confirm both registered:

```bash
super-harness adapter list
```

You should see two rows, both with `enabled: true`.

---

## 4. Declare a change

Every code modification flows through a **change** — a kebab-case slug
that's also the git branch name. Declare one before you start editing:

```bash
super-harness change start "2026-06-01-add-greeting"
```

What happens:

1. Slug is validated (kebab-case, 3-80 chars, ASCII).
2. An `intent_declared` event is appended to `.harness/events.jsonl`.
3. `state.yaml` updates: this slug is now in state
   `INTENT_DECLARED`.

The recommended slug shape is `YYYY-MM-DD-<topic>` — it isn't enforced but
keeps your change list time-sortable.

> For the full lifecycle state/transition reference, see [state-machine.md](state-machine.md).

Check status:

```bash
super-harness status
```

You should see the new change in state `INTENT_DECLARED`.

---

## 5. Let the agent implement

This is where Claude Code (or your agent of choice) takes over. The agent
sees the `AGENTS.md` super-harness section + the active change context and
starts editing. The hot-path gate enforces lifecycle rules:

- In `INTENT_DECLARED`, the agent can author `proposal.md` / `tasks.md` (the
  OpenSpec adapter watches for these and emits `plan_ready` automatically →
  `AWAITING_PLAN_REVIEW`).
- The plan is then reviewed. super-harness **does not run the review** — it
  compiles immutable contracts and enforces that all configured independent
  sources produce valid receipts. Reviewer **roles** are lifecycle positions
  such as `plan-reviewer` and `code-reviewer`; reviewer **sources** are evidence
  labels, not commands or subagent APIs.

  Shared requirements live in tracked `.harness/review-governance.yaml`:

  ```yaml
  version: 1
  review:
    base_branch: main
    sources:
      codex:
        kind: automated
      claude:
        kind: automated
      human:
        kind: human
    roles:
      plan-reviewer:
        participants: [codex, claude]
        min_independent: 2
        max_automatic_rounds_per_epoch: 2
      code-reviewer:
        participants: [codex, claude]
        min_independent: 2
        max_automatic_rounds_per_epoch: 2
        blocking_severity: major   # optional; blocker|major|minor (default major)
    require_distinct_model_families: false
  ```

  Each user's explicit producer choices stay out of Git:

  ```yaml
  # .harness/review-profiles.local.yaml
  version: 1
  sources:
    codex:
      protocol: codex-cli
      model: <your-codex-model>
      cost_class: standard
      agent_options:
        reasoning_effort: medium
        sandbox: read-only
    claude:
      protocol: claude-cli
      model: <your-claude-model>
      cost_class: standard
      agent_options:
        effort: medium
  ```

  Model and option names are producer-specific and explicit. A profile marked
  `cost_class: expensive` requires one-shot human authorization before begin;
  unavailable token telemetry never blocks review.

  `blocking_severity` (per role, default `major`) tunes how strict a
  **code-review** round is: it rejects only when a finding is at or above that
  severity, and findings below it pass with the finding left open — still
  recorded and surfaced by `super-harness report`, but no longer forcing a full
  re-review round. Set it to `minor` to reject on any finding, or `blocker` to
  let `major` findings pass-with-open too. Plan review always rejects on any
  checklist fail regardless (its findings are not tracked in the report), so
  `blocking_severity` on `plan-reviewer` has no effect.

  The automated plan-review protocol is prepare → begin → caller execution →
  import/fail:

  ```bash
  super-harness review prepare my-first-change --reviewer plan-reviewer
  super-harness review begin my-first-change --reviewer plan-reviewer
  # Run every argv/stdin contract printed by begin outside super-harness, unchanged.
  super-harness review result import my-first-change --reviewer plan-reviewer \
    --run-id <codex-run-id> --result-file <codex-output-path>
  super-harness review result import my-first-change --reviewer plan-reviewer \
    --run-id <claude-run-id> --result-file <claude-output-path>
  super-harness implementation start my-first-change  # after → PLAN_APPROVED
  ```

  `begin` never launches Codex, Claude, a Task subagent, or any other producer.
  If an external process crashes, record that exact run with `review run fail`;
  the harness never retries it silently. Wait for every issued run before editing,
  even after an early blocker. `super-harness status` reports pending/failed/
  retained sources, round budget, authorizations, packet digests, and the next
  legal command. Direct `review approve|reject` cannot create new evidence.

  For a human participant, use `review human inspect`, write the structured
  verdict, validate it with `review human draft`, and have the human run
  TTY-only `review human confirm --nonce <nonce>`. A code agent must not confirm
  that nonce. `review skip` remains the disclosed escape hatch; code-review skip
  requires `--override --reason <why>` to pass attestation.
- Now in `IMPLEMENTATION_IN_PROGRESS`, the agent can edit source code. If it
  tries to `Edit` before the lifecycle permits it, the `PreToolUseGate` blocks
  the tool call.
- After `done` (→ `AWAITING_CODE_REVIEW`), code review uses the same source
  protocol. Commit the in-scope files first, then freeze the round:

  ```bash
  super-harness review prepare my-first-change --reviewer code-reviewer
  super-harness review begin my-first-change --reviewer code-reviewer
  # Caller runs each frozen invocation, then imports every result as above.
  ```

  Each run binds source, explicit requested model/options, exact target commit,
  Git range/files/argv, prompt/checklist, and contract digest. Reviewers may read
  unchanged repository material for architecture context, but findings stay on
  the frozen target. A reviewer that cannot complete the target returns
  `scope_sufficient: false` with a finding; it does not widen to the whole PR.

  After a code-review rejection, batch all finding fixes and docs follow-ups into
  commits, then run `review prepare` once. Each source receives everything since
  its latest trustworthy baseline. Do not repeat `done` or plan review for a
  code-only fix. Use `plan redeclare` only when the approved plan, scope, or
  requirements changed; the CLI rejects undeclared plan/spec drift. A scoped
  A started round consumes the automatic-round budget even if a producer crashes.
  The default ceiling is two automated rounds per epoch; exhaustion requires a
  human reviewer or one-shot authorization for an exact additional round.

> **Note**: the three reviewer-driven transitions (`plan_approved`,
> `implementation_started`, `code_review_passed`) are still lifecycle milestones;
> review milestones are emitted by deterministic round closure after valid
> receipts, not by direct approve/reject. super-harness deliberately does not ship
> a headless reviewer executor. Plain-mode advances past
> `INTENT_DECLARED` with the manual verb `super-harness plan ready`; framework
> adapters emit `plan_ready` automatically from their artifacts.

> **Revising a rejected plan without leaving the gate.** `plan ready --scope`
> records any scope file that is a marked `.md` (its frontmatter carries
> `change: <slug>`) as the change's *plan artifact*. In `PLAN_REJECTED` the gate
> then allows editing that plan document through the normal `Edit`/`Write` tools —
> so the reject → revise → re-submit loop needs no shell workaround. Source files
> remain blocked. Recording currently happens only for the manual `plan ready`
> verb; framework-adapter auto-recording is deferred. (Codex has no per-file hook
> input, so Codex-driven plan revision still uses the draft-before-`change start`
> path.)

You don't have to do anything — the hooks installed by
`adapter install claude-code` handle this transparently. The gate enforces
in-process (no background daemon required). If you want to
inspect what the gate would decide right now:

```bash
super-harness gate check pre-tool-use --tool Edit --file src/foo.py
```

If you want to manually walk events (e.g. when no framework adapter is
emitting them yet), you can sync them:

```bash
super-harness adapter scan-once openspec    # one-shot read of openspec/changes/
super-harness event log 2026-06-01-add-greeting    # see what's been emitted
```

---

## 6. Verify locally

Once your agent reports the implementation is done, run the verification
runner to confirm the change passes all three layers — baseline checks,
adapter-provided checks, and user checks:

```bash
super-harness verify
```

Exit codes:

- `0` — all `must_pass` checks passed; you can move on.
- `1` — a sensor crashed or timed out (see stderr).
- `2` — at least one `must_pass` check failed; fix and re-run.
- `3` — `.harness/verification.yaml` is missing (re-run `init`).
- `4` — `--pr <num>` resolution failed (gh fetch / no metadata block / missing Change field).

See [`cli-reference.md`](./cli-reference.md) for the full semantics.

`verify` is read-only — it doesn't advance the lifecycle. To advance the
change from `IMPLEMENTATION_IN_PROGRESS` to `AWAITING_CODE_REVIEW`, use
`done`:

```bash
super-harness done
```

`done` runs `verify` internally, and on pass emits an
`implementation_complete` event that flips state to
`AWAITING_CODE_REVIEW`. If verify fails, `done` exits 2 without advancing.

---

## 7. Open a PR

Create the PR with your normal git workflow (`gh pr create`, `git push`,
etc.). The pull request template (installed in step 2) already contains the
super-harness metadata block:

> **If your GitHub repo is brand-new (no commits on `main` yet)**: push an
> initial commit to `main` before opening a feature-branch PR — `gh repo
> create --private --add-readme` (or any `git push origin main`) sets up
> the base branch the PR will target. Otherwise `gh pr create` fails with
> "remote has no branches".

```markdown
<!-- super-harness:metadata
Change: 2026-06-01-add-greeting
-->
```

Make sure the `Change:` line names your slug. The bundled CI workflow uses
this to link the PR to the change.

When the PR opens, the CI workflow runs seven jobs:

1. **`pr-decorate`** — calls `super-harness pr emit-opened` to emit a
   `pr_opened` event and inject the metadata block if missing.
2. **`pr-validate`** — calls `super-harness pr validate <PR>` to check the
   metadata block + lifecycle position.
3. **`verification`** — runs `super-harness verify <slug>` (same checks as
   the local `verify` you ran in step 6, but in CI for reviewer
   confidence).
4. **`attest-verify`** — runs `super-harness attest verify --base ... --head ...`;
   blocks unless every changed file is covered by a complete, ordered lifecycle
   attestation (see [§10](#10-bind-decisions-to-code-optional) on the attestation
   trail).
5. **`decision-check`** — runs `super-harness decision check` (referential
   integrity + text-lock + executable checks; see §10).
6. **`doc-check`** — runs `super-harness doc check`; blocks if a derivable doc
   drifted from its generator.
7. **`on-merge`** — gated on the merge event; runs `super-harness on-merge`
   after the PR lands.

If any non-`on-merge` job fails, the PR cannot be merged (assuming you've
enabled branch protection). All jobs are visible as required checks on the PR.

---

## 8. Merge

A reviewer approves; you (or auto-merge) squash-merges to `main`. The
`on-merge` job fires:

```bash
super-harness on-merge --commit ${{ github.sha }}
```

What `on-merge` does:

1. Emits a `merged` event tying the change to the merge commit SHA.
2. State advances directly to `ARCHIVED` — the merge is the terminal beat;
   there is no post-merge follow-up step.

`on-merge` always exits 0 — the merge already happened, so it never blocks
or fails the merged PR.

---

## 9. Inspect after the fact

A few read-only commands that are useful for debugging or auditing:

```bash
super-harness status                                  # all active changes
super-harness status --all                            # include ARCHIVED + ABANDONED
super-harness event log 2026-06-01-add-greeting      # this change's event history
super-harness event log --type pr_opened --limit 20  # global filter
super-harness decision list                           # all ratified decisions
super-harness decision show d-some-decision           # one decision + its anchors
super-harness state verify                            # invariant-check events.jsonl
```

---

## 10. Bind decisions to code (optional)

The lifecycle above governs *how a change moves*. A second, independent layer
governs *whether the code honors the decisions a human ratified* — so a decision
can't be silently overturned and the code can't silently violate it. It runs
through one command, `super-harness decision check` (a local sensor your agent can
call at any checkpoint, and the CI `decision-check` job as the un-bypassable
backstop).

**1. Record + ratify a decision.**

```bash
super-harness decision new d-passwords --text "Passwords must be stored with bcrypt — never MD5."
super-harness decision ratify d-passwords      # stamps who/when + freezes a body hash
```

**2. Anchor the code to it.** Drop a `# @decision:d-passwords` comment next to the
code that implements it. Now `decision check` enforces **referential integrity**: an
anchor naming no ratified decision blocks; a ratified decision with no anchor warns.

**3. (text-lock) The ratified text can't be silently rewritten.** `ratify` froze a
hash of the body. If anyone edits a ratified decision's body without re-ratifying,
`decision check` blocks — re-ratify (which re-stamps identity + time, all visible in
the git diff) is the only unlock.

**4. (executable check) The code can't silently violate the decision.** Give the
decision a runnable check + a counterexample, inline in its `.md` body:

````markdown
```check
! grep -rIn "md5(.*password" src/
```

```counterexample path=src/legacy.py
pw = md5(user.password)
```
````

At `ratify`, super-harness proves the check *bites* — it must pass on your current
code **and** fail with the counterexample injected — or it refuses to ratify
(no hollow checks). Then `decision check` runs it on every invocation; code that
trips it is blocked (exit 2). Test a check before proposing it with
`super-harness decision ratify <id> --dry-run`, and run only the checks whose
anchored files changed with `super-harness decision check --changed` (CI runs the
full set).

```bash
super-harness decision check            # referential + text-lock + executable, full
super-harness decision check --changed  # local fast path: only touched anchors
```

> Decisions you can't reduce to a runnable check are recorded as **context** — they
> show up in the `hard:context` ratio `decision check` prints, but never gate. This
> is deliberate: there is no ground truth to mechanically judge prose intent against.

> **Arming architecture rules.** The grep example above is a security rule; the
> flagship use is dependency-direction / layering rules ("core must not import the
> web layer"), where grep is a foot-gun and an import-graph checker is the right
> tool. See [Arm an architecture rule](architecture-fitness.md) for a
> language-by-language guide (Python / TypeScript / Go, and the honest gaps for
> Rust / Java / C-C++).

**Attestation trail.** When you open a PR, the CI `attest-verify` job requires every
changed file to be covered by a complete, ordered lifecycle attestation
(`super-harness attest write <slug>` snapshots it to `.harness/attestations/`). This
is what makes the merge gate refuse work that skipped a lifecycle step.

---

## 11. Next steps

- **Adapt for your framework**: if you don't use OpenSpec, install the
  `plain` framework adapter instead and define your own verification checks
  in `.harness/verification.yaml`.
- **Add custom verification checks**: edit `.harness/verification.yaml` and
  add entries under `user_checks`. They run alongside baseline + adapter
  checks.
- **Tier-tag your changes**: `Micro` / `Normal` / `Large` tiers change how
  strictly some checks fail (more lenient on `Micro`, must-pass on
  `Normal`+).
- **Read the full reference**: every command's flags, defaults, and exit
  codes are documented in [`cli-reference.md`](./cli-reference.md).
- **Discover which rules to arm**: point your Code Agent at the
  [discovering-architecture-norms skill](https://github.com/Dawinia/super-harness/blob/main/skills/discovering-architecture-norms/SKILL.md)
  to sweep your codebase and propose candidate architecture norms (hypotheses you
  then judge and ratify). The super-harness repo is private during v0.1, so the
  link requires repo access until the public release.

---

## 12. Tuning the dead-reference gate for non-C-family languages

`super-harness doc refs` flags backtick code-symbols in your prose docs that no
longer resolve in source. It recognizes a "code symbol" with a default identifier
pattern that fits C-family languages (Python, JavaScript/TypeScript, Go, Rust, Java,
C#, …): `[A-Za-z_][A-Za-z0-9_]*` with snake_case / camelCase shape. These work with
zero configuration.

A language with other identifier conventions — Ruby's `valid?` / `save!` methods, or
`@ivar` / `$global` — can tune the pattern in an optional `.harness/language.yaml`:

```yaml
doc_refs:
  identifier_pattern: '[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?'   # Ruby
```

The single pattern drives both source tokenization and doc-span recognition, so they
stay consistent. A missing, malformed, or un-compilable config silently falls back to
the C-family default — it never breaks the gate.

---

## Common issues

**`super-harness: command not found` after `pipx install`**

`pipx` installs into `~/.local/bin` (Linux) or `~/Library/Application Support/...`
(macOS). Make sure that directory is on your `$PATH`. `pipx ensurepath`
adds it for you.

**`init --setup-github` fails with `gh: command not found`**

Install GitHub CLI (`brew install gh`) and run `gh auth login`. You can
re-run `init --setup-github --force` to retry once gh is set up.

**Hot-path gate is too strict — I want to disable it for one tool call**

First check whether the gate is simply right: if it's blocking a legitimate
action, the underlying cause is often a stale `state.yaml` — run
`super-harness state rebuild` to regenerate from `events.jsonl`. If the gate is
genuinely wrong, use the file-based kill switch: from the repo root, `touch
.harness/gate-disabled` to disable enforcement immediately, and `rm
.harness/gate-disabled` to re-enable. `Bash` is never gated, so this works even
when edits are blocked. Do not hand-edit `.claude/settings.local.json` to
disable it. Note: if you disable the gate while a change is in flight, the bypass
is recorded and surfaced at the merge gate (`attest verify`).

**Verify is failing but I don't see why**

`super-harness --json verify` prints the structured verdict including
per-check details. For full debug traces, also check `.harness/events.jsonl`
for any `sensor_crashed` or `verification_failed` events.

**Multiple active changes**

`super-harness change list --active` shows them all. Most slug-default
commands (`status`, `verify`, `done`, `change resume`) pick the FIRST
non-terminal change. Pass an explicit `<slug>` argument to override.

---

Done. You now have a fully harnessed repo. The whole lifecycle from
`change start` to `archived` is event-sourced — every decision is in
`.harness/events.jsonl`, and you can reproduce any state by replaying the
log.
