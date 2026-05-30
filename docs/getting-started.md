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
3. Writes `.github/workflows/super-harness.yml` — the 4-job CI workflow
   (pr-decorate / pr-validate / verification / on-merge).
4. Writes `.github/pull_request_template.md` with the required metadata
   block that links a PR to a change.
5. Best-effort enables repo `auto-merge` + `squash` settings on GitHub
   (skipped silently if you don't have admin on the repo).
6. Lists `pre-commit` / `pre-push` in `.harness/gates.yaml` as planned
   cold-path gates. Actual `.git/hooks/` install is v0.2 — see the
   project README's "What v0.1 does NOT ship yet" / OPEN-ITEMS #2.

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

- **Framework adapters:** `openspec` and `plain`.
- **Agent adapters:** `claude-code`.

For this walkthrough we'll wire up the canonical pair:

```bash
super-harness adapter install openspec
super-harness adapter install claude-code
```

`adapter install openspec` does:

- Registers OpenSpec hooks (watches `openspec/changes/` for `proposal.md`
  and `tasks.md`).
- Adds adapter-provided verification checks to `.harness/verification.yaml`
  (e.g. `openspec validate <slug> --strict --json`).
- Persists the entry in `.harness/adapters.yaml` so future commands know
  it's enabled.

`adapter install claude-code` does:

- Writes `PreToolUse` + `SessionStart` hooks into `.claude/settings.json`
  so Claude Code consults super-harness before every `Edit` / `Write` tool
  call.
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
  OpenSpec adapter watches for these and emits `plan_ready` automatically).
- After `plan_ready` and approval (which advances state to
  `PLAN_APPROVED` → `IMPLEMENTATION_IN_PROGRESS`), the agent can edit
  source code.
- If the agent tries to `Edit` source code before the lifecycle permits it,
  the `PreToolUseGate` returns `deny` and Claude Code blocks the tool call.

You don't have to do anything — the daemon + hooks installed by
`adapter install claude-code` handle this transparently. If you want to
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

```markdown
<!-- super-harness:metadata
Change: 2026-06-01-add-greeting
-->
```

Make sure the `Change:` line names your slug. The bundled CI workflow uses
this to link the PR to the change.

When the PR opens, the CI workflow runs four jobs:

1. **`pr-decorate`** — calls `super-harness pr emit-opened` to emit a
   `pr_opened` event and inject the metadata block if missing.
2. **`pr-validate`** — calls `super-harness pr validate <PR>` to check the
   metadata block + lifecycle position.
3. **`verification`** — runs `super-harness verify <slug>` (same checks as
   the local `verify` you ran in step 6, but in CI for reviewer
   confidence).
4. **`on-merge`** — gated on the merge event; runs `super-harness on-merge`
   after the PR lands.

If any of the first three fail, the PR cannot be merged (assuming you've
enabled branch protection). All four jobs are visible as required checks
on the PR.

---

## 8. Merge

A reviewer approves; you (or auto-merge) squash-merges to `main`. The
`on-merge` job fires:

```bash
super-harness on-merge --commit ${{ github.sha }}
```

What `on-merge` does:

1. Emits a `merged` event tying the change to the merge commit SHA.
2. Dispatches the L1-updater sensor to compute changes to L1 capability
   docs (`docs/reference/capabilities/**`) and open a follow-up PR.
3. Dispatches the anchor-index-rebuilder to refresh
   `.harness/anchors/index.yaml`.
4. State advances to `MERGED`, then `ARCHIVED` once the L1 follow-up PR
   lands.

If the L1-updater succeeds, a follow-up PR appears under your account; if
it fails (e.g. transient `gh` error), a pending file lands at
`.harness/pending-l1-updates/<slug>.md` so a human can rerun later. Either
way `on-merge` itself exits 0 — the merge already happened.

---

## 9. Inspect after the fact

A few read-only commands that are useful for debugging or auditing:

```bash
super-harness status                                  # all active changes
super-harness status --all                            # include ARCHIVED + ABANDONED
super-harness event log 2026-06-01-add-greeting      # this change's event history
super-harness event log --type pr_opened --limit 20  # global filter
super-harness anchor list                             # all L1 sentinels
super-harness state verify                            # invariant-check events.jsonl
```

---

## 10. Next steps

- **Adapt for your framework**: if you don't use OpenSpec, install the
  `plain` framework adapter instead and define your own verification checks
  in `.harness/verification.yaml`.
- **Add custom verification checks**: edit `.harness/verification.yaml` and
  add entries under `user_checks`. They run alongside baseline + adapter
  checks.
- **Tier-tag your changes**: `Micro` / `Normal` / `Large` tiers change how
  strictly some checks fail (e.g. anchor-sentinel-presence-final warns on
  Micro, must-pass on Normal+).
- **Read the full reference**: every command's flags, defaults, and exit
  codes are documented in [`cli-reference.md`](./cli-reference.md).

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

You can't — that's the whole point of the gate. If the gate is blocking a
legitimate action, the underlying cause is usually a stale `state.yaml`.
Run `super-harness state rebuild` to regenerate from `events.jsonl`.

**Verify is failing but I don't see why**

`super-harness verify --json` prints the structured verdict including
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
