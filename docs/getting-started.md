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
   without writing. Review execution is not discovered or configured here.
2. **Configuration** selects coding-agent integrations and resolves existing-file
   conflicts. The generated external-review recognition policy is disabled until
   an owner later recognizes one concrete process.
3. **Review before writes** shows the selected integrations, disabled recognition
   policy, GitHub choice, grouped create/update/preserve actions, and any local
   settings files that will receive a backup. In an interactive mode, the
   workspace is unchanged until this plan is accepted.
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
configuration. **Ctrl+C** interrupts setup. Detected coding integrations are
preselected and labeled `detected · recommended` on a fresh init. An unavailable
coding integration remains selectable but is not preselected. Init does not scan
review CLIs, select models, start producers, or write a local producer profile.
Existing governance/profile files are preserved as historical data and are not
used to authorize a new Change.

A representative guided terminal session looks like this (paths and selections
will reflect your machine):

```text
$ super-harness init --setup-github
┌ super-harness init
│
◇  Workspace  /work/my-project
│
◇  Integrations  Codex, Claude Code
│
◇  External review recognition  Disabled until owner configuration
│
◇  GitHub  Workflow and PR template
│
◇  Plan  11 files to write
│  .harness ×9 · AGENTS.md · .gitignore
│  5 unchanged hidden — --verbose to see them
│
◇  Harness configuration
◇  Agent integrations
◇  Repository guidance
│
▲  GitHub setup: GitHub repository settings need manual confirmation. Settings -> General -> Pull Requests.
│
└ Setup complete in 3.1s · Next: super-harness status
```

The session reads as one continuous clack-style flow: a single spine (`│`) runs
from the `┌` opener to the `└` result, one blank spine line separates each group,
and every completed answer or outcome collapses to a single `◇` line. While you
answer a question, Questionary draws its own live prompt frame (a `◆` question with
a `›` pointer); that frame is erased once you answer, leaving only the `◇` summary
on the spine.

The default guided review is a single `◇ Plan  N files to write` header with the
changed files inlined and one hidden-count line. Preserved and skipped files are
summarized as hidden unchanged detail, and successful apply steps are grouped by
user-visible outcome. Run `super-harness --verbose init` to expand exact
preserved/skipped and backup paths (as `Update`/`Create`/`Delete`/`Preserve`/`Skip`
and `Back up` rows on the spine) and show per-operation apply diagnostics. Verbose
mode changes only rendering; it does not change the reviewed plan or writes.

In the verbose review, if the plan will change an existing `.codex/hooks.json`
or `.claude/settings.local.json`, a **Back up** row lists that path. The default
review leaves that unchanged diagnostic detail collapsed.
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
themselves. When the output stream itself accepts only ASCII, non-ASCII path
characters remain identifiable as deterministic escapes such as `\u9879\u76ee`
rather than being dropped or causing setup to crash.

`--yes` skips only the final confirmation in an interactive mode. It does not
select integrations or resolve conflicts with existing files. When stdin is not a TTY, `init` preserves
the scriptable behavior: it does not prompt or read user CLI configuration and
applies immediately from explicit flags and existing workspace defaults, so CI
and redirected scripts do not need `--yes`.

The installed `init` and event-backed lifecycle commands are designed for native
Windows (including Windows Terminal and PowerShell), macOS, Linux, and WSL.
Their Windows entrypoint, event lock, state-rebuild lock, and review-bundle Git
decoding paths have automated coverage. A real Windows TTY session has not yet
been manually verified, so report terminal-specific rendering or key-handling
differences if you encounter them. The optional observer daemon remains
POSIX-only because it uses `fork` and POSIX process-liveness semantics; native
Windows lifecycle support does not imply `observe start` support. The Unix
package-install commands in [Install the CLI](#1-install-the-cli) are examples
for that shell environment, not a runtime requirement.

What `init --setup-github` applies after interactive confirmation (or
immediately when stdin is not a TTY):

1. Creates `.harness/` with `events.jsonl` (the append-only lifecycle log),
   tracked skeleton configuration, and a disabled `review-recognition.yaml`.
   The derived `state.yaml` cache appears after the first lifecycle event, while
   `adapters.yaml` is created only when an integration is selected. Existing
   `review-governance.yaml` and `review-profiles.local.yaml` are preserved and
   inert for new Changes. Init never installs a third-party agent or reviewer
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

- In `INTENT_DECLARED`, authoring the plan document is allowed for any path
  matching `.harness/plan-paths.yaml`. `init` ships that file with the OpenSpec
  pattern (`openspec/changes/{slug}/*.md`) enabled by default, so writing
  `proposal.md` / `tasks.md` under this change's `openspec/changes/` directory is
  unblocked out of the box — the OpenSpec adapter then watches for them and
  emits `plan_ready` automatically → `AWAITING_PLAN_REVIEW`. Everything else
  (source files) stays blocked until then. Working notes that aren't part of
  the plan itself go in `.harness/scratch/<slug>/`, writable in every state.
- The plan must be submitted for review before implementation. super-harness does
  not run, retry, or configure a reviewer. `plan ready --plan <path>` snapshots
  the finite adopted document set (including the matching spec and
  `docs/product-foundations.md` when present) and the binding commitments.

  ```bash
  super-harness plan ready my-first-change \
    --scope "[docs/plans/my-first-change.md, src/app.py]" \
    --plan docs/plans/my-first-change.md \
    --commitment O1="observable behavior"
  ```

  The resulting plan subject is the authority boundary. An external process may
  produce a JSON conclusion, but it becomes lifecycle evidence only when the
  owner has enabled `.harness/review-recognition.yaml` for that exact process,
  issuer, version, and evidence form. The shipped policy is disabled. Import
  the retained original evidence with:

  ```bash
  super-harness review import my-first-change --evidence plan-review.json
  ```

  The recognition policy is the only active review configuration. The old
  governance and local producer-profile files are retained unchanged for
  historical readers; they do not select a producer, model, round, retry, or
  TTY workflow for a new Change.

  The imported record must name the exact subject, contain an explicit
  `approve` or `reject`, preserve provenance and original evidence, and use the
  recognized process. Empty output, a failed producer, an old skip, or a stale
  subject is not approval. A rejected or superseded revision must be withdrawn
  explicitly before the previously approved plan can reach completion:

  ```bash
  super-harness plan withdraw my-first-change \
    --candidate <subject-id> --reason "revision not adopted"
  ```
- Now in `IMPLEMENTATION_IN_PROGRESS`, the agent can edit source code. If it
  tries to `Edit` before the lifecycle permits it, the `PreToolUseGate` blocks
  the tool call.
- After `done` (→ `AWAITING_CODE_REVIEW`), code review uses the same external
  evidence boundary. Record the implementation assessment and complete coverage
  manifest, then import code evidence for the exact committed code subject:

  ```bash
  super-harness implementation record my-first-change --assessment implementation.json
  super-harness review import my-first-change --evidence code-review.json
  ```

  Evidence is not a claim that a particular model or TTY was used; it is an
  owner-recognized external conclusion bound to the exact subject. Code-only
  fixes can use `implementation reopen`; changes to the approved commitment,
  scope, or explicit limit require `plan redeclare --plan ...`, a new plan
  subject, and a new plan conclusion. Do not use this new design to bypass the
  currently effective scope or delivery gate.

> **Note**: `plan_approved` and `code_review_passed` are lifecycle milestones,
> but the active core can emit them only from imported, recognized evidence. A
> review skip remains a disclosed historical escape hatch and cannot create new
> plan authority. Plain-mode advances past `INTENT_DECLARED` with the manual
> `super-harness plan ready` command; framework adapters may emit `plan_ready`
> from their artifacts.

> **Revising a plan without silently expanding authority.** In `PLAN_REJECTED`,
> the gate allows editing the marked plan documents through the normal
> `Edit`/`Write` tools; source files remain blocked. Re-submit the revised
> subject and conclusion with `plan ready --plan ...` for the initial plan, or
> `plan redeclare --plan ...` when an already-approved plan's commitments, scope,
> or explicit limits change. A pending B2 candidate must be explicitly
> withdrawn if it is not adopted; a late conclusion never reactivates it.

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

When the PR opens, the CI workflow runs eight jobs:

1. **`pr-decorate`** — calls `super-harness pr emit-opened` to emit a
   `pr_opened` event and inject the metadata block if missing.
2. **`pr-validate`** — calls `super-harness pr validate <PR>` to check the
   metadata block + lifecycle position.
3. **`verification`** — runs `super-harness verify <slug>` (same checks as
   the local `verify` you ran in step 6).
4. **`attest-verify`** — runs `super-harness attest verify --base ... --head ...`;
   from a verifier installed from the trusted `BASE_SHA`, and blocks unless
   every changed file is covered by a complete, ordered lifecycle attestation
   and matching new-contract subjects.
5. **`candidate-acceptance`** — installs the candidate package separately and
   runs the new-contract acceptance tests; it cannot approve the candidate's
   own attestation.
6. **`decision-check`** — runs `super-harness decision check` (referential
   integrity + text-lock + executable checks; see §10).
7. **`doc-check`** — runs `super-harness doc check`; blocks if a derivable doc
   drifted from its generator.
8. **`on-merge`** — gated on the merge event; runs `super-harness on-merge`
   after the PR lands.

If any non-`on-merge` job fails, the PR cannot be merged (assuming you've
enabled branch protection). All jobs are visible as required checks on the PR.

---

## 8. Merge

After the applicable external code evidence is imported and all required checks
pass, you (or auto-merge) squash-merge to `main`. The `on-merge` job fires:

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
