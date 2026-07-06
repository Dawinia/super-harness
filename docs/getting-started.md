# Getting started with super-harness

A 10-minute walkthrough of the full v0.1 lifecycle — from installing the CLI
to landing a PR with all gates green. By the end you'll have:

- A repo bootstrapped with the `.harness/` data plane and the bundled CI
  workflow.
- Two adapters installed (the OpenSpec framework adapter + the Claude Code
  agent adapter).
- One change driven through every gate: declared, implemented, verified,
  reviewed, merged, and archived.

This guide assumes a Unix shell (macOS or Linux). All commands are
copy-paste runnable. For the full CLI surface see
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
project — `super-harness init` is non-destructive (it never deletes your
code, only creates `.harness/` + writes a CI workflow file).

```bash
cd path/to/your/repo
super-harness init --setup-github
```

What `init --setup-github` does:

1. Creates `.harness/` with the lifecycle data plane: `events.jsonl` (the
   append-only event log) + `state.yaml` (the derived current-state cache).
2. Writes `AGENTS.md` (or extends an existing one) with a `super-harness`
   section your AI agent will read.
   - If a `.claude/` directory is detected, `init` also auto-installs the
     Claude Code agent adapter's gate hook (see step 3). Pass `init
     --no-agent` to skip this.
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
  enforces (via the gate) that a verdict is recorded, and you (or the agent's
  own reviewer subagent, per the `AGENTS.md` protocol) produce it:

  ```bash
  super-harness review approve my-first-change --reviewer plan-reviewer   # → PLAN_APPROVED
  super-harness implementation start my-first-change                      # → IMPLEMENTATION_IN_PROGRESS
  ```

  (Use `review reject ... --reason "<why>"` to send the plan back, or
  `review skip ...` as an escape hatch. The per-reviewer strategy —
  `subagent` / `human` / `hybrid` — is set in `.harness/policy.yaml` and shown by
  `super-harness status`.)
- Now in `IMPLEMENTATION_IN_PROGRESS`, the agent can edit source code. If it
  tries to `Edit` before the lifecycle permits it, the `PreToolUseGate` blocks
  the tool call.
- After `done` (→ `AWAITING_CODE_REVIEW`), record the code-review verdict the
  same way: `super-harness review approve my-first-change --reviewer code-reviewer`
  (→ `READY_TO_MERGE`).

> **Note**: the three reviewer-driven transitions (`plan_approved`,
> `implementation_started`, `code_review_passed`) now ship as the CLI verbs above —
> the lifecycle runs end-to-end via the CLI. What is *not* yet shipped is an
> unattended CI auto-reviewer (a headless LLM that produces the verdict with no
> human/agent present); that is tracked as a follow-up. Plain-mode advances past
> `INTENT_DECLARED` with the manual verb `super-harness plan ready`; framework
> adapters emit `plan_ready` automatically from their artifacts.

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
