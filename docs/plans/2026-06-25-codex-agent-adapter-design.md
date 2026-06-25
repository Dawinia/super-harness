# Codex agent adapter — design

> Portability decoupling, axis B (de-bind from Claude Code). First cut: a real
> `CodexAdapter` so a workspace driven by OpenAI **Codex CLI** gets the same
> real-time deterministic gate + session-start context that the Claude Code
> adapter gives today. Written 2026-06-25. All load-bearing Codex facts below
> were verified first-hand against `developers.openai.com/codex/hooks`
> (not training memory) — see §7.

## 1. Why

super-harness governs an agent in real time through one mechanism: a
**PreToolUse hook** that the agent calls before each mutating tool, which super-harness
answers `allow`/`block` from the lifecycle state. Today only the Claude Code
adapter wires that hook, so a non-Claude agent can only ride the CI "cold floor"
(`attest verify` / `doc check` / `decision check` / `doc refs`) — it gets no
real-time gate.

The user runs **Codex CLI alongside Claude Code**. The portability premise we
*recorded* was "Codex has no hook face → cold-floor only." **First-hand
verification flipped that premise** (§7): Codex CLI shipped a lifecycle-hooks
framework (GA 2026-05-14) whose `PreToolUse` event can **deny** a tool call —
near-parity with Claude Code's PreToolUse. So a real Codex adapter that delivers
a live gate is feasible, not a degraded shim.

This is the first of the two portability axes. Axis A (de-bind from Python via
language profiles) is a separate, deferred spec — the audit showed everything
except the dead-reference identifier regex is already user-config, so Python
de-binding is mostly defaults, not machinery.

## 2. Scope

**IN:**
- A `CodexAdapter(AgentAdapter)` registered in the adapter registry, installable
  via the existing generic `adapter install codex` (no new CLI subcommand).
- A **PreToolUse** hook entry in `.codex/hooks.json` matching `apply_patch`
  (Codex's edit tool), routed to the `super-harness-hook` binary in a new
  `--agent codex` shim mode.
- A **SessionStart** hook entry whose command is `super-harness change resume`;
  Codex injects its stdout as developer context (verified §7).
- Idempotent, no-clobber, snapshot/rollback config merge into `.codex/hooks.json`
  (mirrors the Claude `_settings_merge` transaction shape).
- `.gitignore` coverage for the machine-specific hook config + its backups.
- A Codex-specific AGENTS.md subsection that teaches the gate, the **manual trust
  step**, and the coverage caveat.

**Explicitly OUT (recorded non-goals):**
- Language profiles / Python de-binding (axis A — separate spec).
- File-level scope enforcement for Codex edits. Codex's PreToolUse stdin carries
  `tool_input.command` (the patch text), **not** a clean `file_path`. The shim
  passes `file=None`; the gate decides on lifecycle **state** (does the current
  change state forbid mutation?), not per-file scope. File extraction from an
  `apply_patch` body is deferred to OPEN-ITEMS.
- Gating `Bash`. The PreToolUse matcher is `apply_patch` only, deliberately
  leaving `Bash` ungated — identical to the Claude adapter's invariant that the
  gate never blocks `Bash`, so the kill-switch (`touch .harness/gate-disabled`)
  always works even mid-block.
- Auto-trust. Codex's trust/hash model requires a human `/hooks` step; we cannot
  and will not bypass it (§4.3).
- `PostToolUse` / other Codex events. Only the two hooks above ship this cut.

## 3. Design

Zero changes to the **core gate / state machine / supervisor / verification
runner**. The Codex binding lives in the agent-adapter layer + one new shim mode
on the existing hook entry point — the same isolation the Claude adapter uses.
One **CLI-layer** change is required (not core, but not zero): `cli/adapter.py`'s
install/uninstall success messages are currently hardcoded to Claude wording
(`.claude/settings.local.json`, "PreToolUse gate hook registered in
.claude/...") and fire for any adapter — see §3.6. Codex install must not print
Claude paths.

### 3.1 Hook entry shim — `daemon/hook_entry.py`

`main()` already branches on `--agent <name>`. Add a `codex` arm calling a new
`_run_codex_shim()` that reuses the shared `_decide(tool, file)` core
(kill-switch short-circuit, harness-root resolution, active-change resolution,
supervisor query, fail-open everywhere) unchanged.

Codex delivers PreToolUse input as a JSON object on **stdin** with these
load-bearing keys (§7):

```json
{ "hook_event_name": "PreToolUse", "tool_name": "apply_patch",
  "tool_input": { "command": "<patch text>" }, "cwd": "...", "turn_id": "..." }
```

The shim:
1. `json.load(sys.stdin)`; non-object / malformed / missing `tool_name` → exit 0
   (fail-open ALLOW), matching the Claude shim.
2. `tool = data["tool_name"]` (always `"apply_patch"` for edits, whatever matcher
   alias fired — harmless since the verdict is tool-name-independent, §4.2);
   `file = None` (Codex gives `command`, not a path).
3. `decision, reason = _decide(tool, file)`.
4. On **block**: print the deny object to **stdout** and exit 0 (Codex's
   recommended, structured deny path — §7):
   ```json
   { "hookSpecificOutput": { "hookEventName": "PreToolUse",
       "permissionDecision": "deny",
       "permissionDecisionReason": "super-harness: <reason> — escape hatch: touch .harness/gate-disabled" } }
   ```
5. On **allow**: print nothing, exit 0 (Codex proceeds with its normal flow).

Note the deny-path difference from Claude: Claude blocks via **exit 2 + stderr**;
Codex blocks via **stdout JSON `permissionDecision: deny`**. Both are documented;
each agent's shim uses its own contract. (Exit-2 is also a valid Codex fallback,
but the JSON path surfaces the reason to the model cleanly, so we use it.)

### 3.2 `CodexAdapter` — `adapters/agent/codex.py`

Mirrors `ClaudeCodeAdapter` structurally.

- `name = "codex"`, `version = "0.1.0"`. `capabilities`: `pre_tool_use_hook`,
  `session_start_hook`, `rules_file_injection`, `subprocess_execution`, `mcp_server`
  True; `post_tool_use_hook` (not wired this cut), `session_end_hook`,
  `pre_commit_hook` False.
- `detect(workspace)` → `(workspace / ".codex").is_dir()`.
- `install_hooks(workspace)`:
  1. Resolve `super-harness-hook` **and** `super-harness` to absolute paths via
     `shutil.which`; abort with the same RuntimeError-with-reinstall-hint as
     Claude if either is missing — **before any write**.
  2. Snapshot `.codex/hooks.json` (content or absent) as the transaction
     boundary; on any merge failure, restore and re-raise (Claude's pattern).
  3. Merge a PreToolUse hook (`matcher: "^(apply_patch|Edit|Write)$"`, command
     `<abs super-harness-hook> --agent codex`) and a SessionStart hook (command
     `<abs super-harness> change resume`) via the generalized `_settings_merge`
     (`matcher`/`marker` kwargs — §3.3).
     The matcher is broadened beyond `apply_patch` because Codex surfaces a file
     edit under any of `apply_patch` / `Edit` / `Write` (§7); the regex is
     anchored exactly as Codex's own docs example shows (`matcher` is a regex —
     §7). The gate's verdict ignores which of these fired (it is state-only), so
     the matcher only needs to FIRE on every edit shape — a missed edit name
     would be a silent fail-open, hence the union.

  `install_hooks` does **NOT** touch `.gitignore` (neither does the Claude
  adapter). Gitignore coverage is a static canonical block rendered by
  `init` / `sync --gitignore` (§3.4), not adapter-side work. With no gitignore
  step here, the step-2 snapshot bounds the *entire* `install_hooks` write
  surface (`.codex/hooks.json` only), exactly mirroring Claude — no rollback gap.
- `inject_context(change_id)` → identical to Claude: shell out to
  `super-harness change resume <change_id>`, return stdout or `""`.
- `agents_md_subsection()` → Codex marker-wrapped block (§3.5).
- `on_uninstall(workspace)` → restore earliest `.codex/hooks.json` backup
  (Claude's earliest-backup-restore logic).

**Absolute path, not bare command (resolved design decision).** The hook
`command` pins a machine-specific absolute path (`shutil.which` result), so
`.codex/hooks.json` is gitignored — same model as Claude's
`settings.local.json`. We considered a bare `super-harness-hook` (PATH-resolved,
committable, more "portable"). Rejected: Codex's docs lean to direct exec with
**no guaranteed PATH** (examples use absolute interpreter paths; repo-local hooks
are told to resolve from the git root), so a bare command risks the hook binary
not being found → the gate silently fails open. For a governance tool a silent
fail-open is unacceptable. The committed-file "portability" is illusory under
Codex's exec model anyway — `adapter install codex` regenerates the correct
absolute path per machine, which is the real portability that matters.

### 3.3 Config merge — generalize `adapters/agent/_settings_merge.py`

**DRY refinement (discovered at plan-writing time):** Codex's `.codex/hooks.json`
has the *identical* shape to Claude's `settings.json` hooks block — the same
`{hooks: {PreToolUse: [{matcher, hooks:[{type,command,timeout}]}], SessionStart:
[...]}}`. The existing `_settings_merge.py` (idempotent, no-clobber, backup,
fail-loud, strip-and-replace by marker) differs only in two constants: the
PreToolUse `matcher` and the `--agent <name>` marker. So instead of forking a
near-duplicate `_codex_config_merge.py`, **generalize `_settings_merge.py`**:
thread `matcher` and `marker` as keyword args on `merge_pre_tool_use_hook`
(and `marker` on `merge_session_start_hook`), defaulting to Claude's current
values so every existing Claude call site + test stays byte-identical. Codex
passes `matcher="^(apply_patch|Edit|Write)$"`, `marker="--agent codex"`. This
keeps the merge logic single-sourced — exactly the anti-binding posture this
whole effort is about. (The filename stays `_settings_merge.py` to avoid import
churn; it is now agent-neutral despite the name.)

Schema target (verified §7):

```json
{ "hooks": { "PreToolUse": [ { "matcher": "^(apply_patch|Edit|Write)$",
      "hooks": [ { "type": "command", "command": "<abs hook> --agent codex" } ] } ],
    "SessionStart": [ { "hooks": [ { "type": "command", "command": "<abs cli> change resume" } ] } ] } }
```

- Idempotent: re-install replaces our entry, never duplicates. Identity markers
  (path-independent, mirroring Claude's): PreToolUse keyed on the `--agent codex`
  command substring; SessionStart keyed on the `change resume` substring.
- **Marker-collision note:** the SessionStart marker `change resume` is *identical*
  to the Claude adapter's SessionStart marker. This is benign **only because the
  two adapters write to disjoint files** (`.claude/settings.local.json` vs
  `.codex/hooks.json`) — the Codex merge operates solely on `.codex/hooks.json`
  and must never be pointed at a shared file. (PreToolUse markers do not collide:
  `--agent codex` ≠ `--agent claude-code`.)
- No-clobber: a user's pre-existing PreToolUse / SessionStart hooks are
  preserved; we append ours.
- **Fail-loud on a corrupt user file** (mirrors `_settings_merge`'s `ValueError`
  path): if `.codex/hooks.json` exists but parses to a non-object, or `hooks` is
  non-dict, or `hooks.PreToolUse` **or** `hooks.SessionStart` is present with a
  non-list shape we refuse to guess about, raise rather than `TypeError`
  mid-merge (both event lists are validated, mirroring `_settings_merge`'s
  `_ensure_event_list` for PreToolUse *and* SessionStart). install_hooks' snapshot
  then rolls back. A *missing* file is normal (create fresh).
- Backs the file up to `.codex/hooks.json.super-harness-backup.<time_ns>` before
  writing (Claude's backup convention, so `on_uninstall` + gitignore globs line
  up).
- Hooks are enabled by default in Codex (`[features] hooks = false` only
  *disables*), so install writes **no** enable flag.

### 3.4 `.gitignore` — `engineering/gitignore_injector.py`

Add to the existing canonical-paths block (the module already documents this as
the extension point for new agent adapters):

```
.codex/hooks.json
.codex/*.super-harness-backup.*
```

We ignore only `hooks.json` (+ backups), never `.codex/` wholesale —
`.codex/config.toml` may hold the user's own committed Codex settings.

These lines live in the **static** `_CANONICAL_PATHS` tuple and are rendered by
`inject_gitignore_block`, which is called only from `cli/init.py` and
`cli/sync.py` — exactly how the `.claude/` lines work today. The adapter never
calls it. Consequence (same property the Claude `.claude/` lines have): a fresh
Codex install only gets these lines once `init` or `sync --gitignore` has run.
AGENTS.md (§3.5) reminds the user to run `sync --gitignore` after installing on a
pre-existing repo.

### 3.5 AGENTS.md subsection (Codex)

Marker-wrapped `<!-- super-harness agent: codex -->`. Reuses the Claude review-
protocol prose (state machine + verdict verbs are agent-agnostic) but with three
Codex-specific deltas stated loudly:

1. **Manual trust step (load-bearing):** "After `adapter install codex`, the gate
   is INACTIVE until you run `/hooks` in Codex and trust the super-harness hook.
   Codex skips new/changed hooks until trusted (trust is recorded against the
   hook's hash). If you change the hook, re-trust it."
2. **What's gated:** `apply_patch` mutations are blocked when the change state
   forbids them; `Bash` is never gated (so the kill-switch works).
3. **Coverage caveat:** Codex PreToolUse intercepts only simple shell +
   `apply_patch` — it does **not** see `WebSearch` or other non-shell/non-MCP
   tools, so real-time coverage is narrower than Claude Code's.

### 3.6 CLI install/uninstall messaging — `cli/adapter.py`

The only Claude-hardcoded literals are on the **install** path, at two exact
lines: `cli/adapter.py:218` (`"Created .claude/settings.local.json (no .claude/
existed)."`) and `:220` (`detail = "PreToolUse gate hook registered in
.claude/settings.local.json"`), both printed for *every* `AgentAdapter`; the
`created_claude_dir` flag at `:145` is `not adapter.detect(root)`. Run against
Codex these announce `.claude/` paths — wrong. The **uninstall** success line
(`:418`, `"Uninstalled {name} adapter."`) is already generic — no change needed
there; the implementer should not go hunting for a non-existent uninstall leak.

Fix: make those two install lines **adapter-driven**. Add a small **non-abstract**
method to `AgentAdapter` (plain `def` with a default body — exactly the
`watch_paths` / `spec_paths` pattern at `adapters/__init__.py:203,222`, NOT
`@abstractmethod`, so `__init_subclass__`'s `inspect.isabstract` short-circuit
keeps accepting every existing concrete adapter), e.g. `installed_detail() ->
str` plus a `local_config_path()` accessor, that each adapter fills with its own
path + one-line summary; the CLI prints those instead of the literals. Claude
returns its existing strings (no behaviour change); Codex returns
`.codex/hooks.json` + the **trust reminder** ("run `/hooks` in Codex to trust the
hook before the gate is active"). Keeps `adapter install <name>` generic while
removing the Claude leak; the additive default means no other adapter breaks.

## 4. Honest limits / non-goals

### 4.1 Narrower real-time coverage than Claude Code
Codex PreToolUse covers `apply_patch` + simple shell only. Claude's gate covers
Edit/Write/MultiEdit/NotebookEdit. The CI cold floor (identical for both) backs
the gap. Documented in AGENTS.md + OPEN-ITEMS. **Plus a post-install asymmetry:**
the Claude hook is live the moment install writes it; the Codex hook provides
**zero** enforcement until a human runs `/hooks` and trusts it (§4.3). On a fresh
clone the Codex gate is dormant until that manual step — a real coverage delta
beyond the narrower tool set, not just a smaller matcher.

### 4.2 State-gate only, no per-file scope (this cut)
`file=None` means the gate enforces lifecycle-state legality of a mutation, not
per-file scope membership. This matches the gate's primary teeth (blocking edits
before plan approval / during review); per-file scope refinement for Codex is an
OPEN-ITEMS follow-up.

### 4.3 Trust step is manual and unforgeable-by-us
Install writes the config; only a human `/hooks` trust makes the gate live. We
cannot auto-trust (and would not — it is exactly the integrity property). For a
solo owner this is self-trustable, consistent with the HG-12 solo ceiling
(`project-bedrock-solo-owner-unforgeable`): the tool gives discipline + detection
+ disclosure, not owner-proof enforcement.

### 4.4 PATH/cwd assumptions
The gate resolves the harness root by walking up from the hook's cwd (Codex runs
hooks with the session cwd). A Codex session started outside the repo would
fail-open (no `.harness/` found) — the same assumption the Claude/positional
paths already make. Note the "no guaranteed PATH" premise (which forces absolute
paths for the *hook* commands, §3.2) also touches `inject_context`, which shells
out to the **bare** `super-harness` name (mirroring Claude's intentional
best-effort programmatic call). That is acceptable: `inject_context` is never the
gate path — a PATH-less failure there degrades context injection to `""`, it does
not open a gate hole.

## 5. Test + self-host plan

TDD throughout. The new `_run_codex_shim` + the `_settings_merge` generalization
(matcher/marker kwargs) are test-first; existing `_settings_merge` tests must stay
green unchanged (Claude-preserving defaults).

- **Shim unit tests** (`test_hook_entry`): codex stdin JSON → allow/block;
  malformed/empty/non-object stdin → fail-open ALLOW; block emits the exact
  `permissionDecision: deny` stdout JSON + exit 0; kill-switch ALLOWs.
- **Merge unit tests**: fresh write; idempotent re-install (no dupes); no-clobber
  of a user's existing PreToolUse/SessionStart; backup written; snapshot rollback
  on a forced merge failure.
- **Adapter tests**: `detect` on `.codex/`; `install_hooks` abort when a binary
  is missing (before any write); `install_hooks` does NOT write `.gitignore`
  (gitignore is sync/init's job); `on_uninstall` restores the earliest backup;
  `agents_md_subsection` contains the trust-step + caveat strings; install
  message is adapter-driven (no `.claude/` literal — §3.6).
- **Gitignore test**: `sync --gitignore` (or `init`) renders the two `.codex/`
  canonical lines (`engineering/gitignore_injector` — the static block, §3.4),
  mirroring the existing `.claude/` coverage test.
- **Registry test**: `adapter install codex` resolves `CodexAdapter`.
- **Derived docs**: AGENTS.md regenerated via `sync --agents-md`; `sync --check`
  + `doc check` both green. cli-reference unchanged (no new subcommand).
- **REQUIRED manual smoke (pre-trust release gate, not a footnote):** drive a
  real Codex session, run `/hooks` to trust the hook, then attempt an
  `apply_patch` in a gate-blocking state and CONFIRM it is actually denied. This
  is the only check that the chosen block contract (stdout `permissionDecision:
  deny` + exit 0) really blocks rather than silently failing open — given the
  Claude exit-code footgun history (exit 1 = non-blocking there), this live
  confirmation MUST pass before the adapter is trusted as a gate. Record the
  walkthrough result in the change's smoke note.

Self-host merge sequence per the standard lifecycle (branch → `change start` →
`plan ready --scope <all changed files>` → plan review → implement → green →
`done` → `review prepare`/independent reviewer/`review approve --verdict-file`
→ `attest write` + commit → `attest verify` → PR → CI green → squash →
`on-merge`).

## 6. Files touched

- `src/super_harness/daemon/hook_entry.py` — add `--agent codex` arm +
  `_run_codex_shim()`.
- `src/super_harness/adapters/agent/codex.py` — new `CodexAdapter`.
- `src/super_harness/adapters/agent/_settings_merge.py` — generalize: `matcher` +
  `marker` kwargs (Claude-preserving defaults) so Codex reuses the merge (§3.3).
- `src/super_harness/adapters/registry.py` — register `CodexAdapter`.
- `src/super_harness/adapters/__init__.py` — additive ABC hook for adapter-driven
  install detail (`installed_detail()` / local-config-path accessor; §3.6).
- `src/super_harness/cli/adapter.py` — print adapter-driven install/uninstall
  messages instead of Claude-hardcoded paths (§3.6).
- `src/super_harness/engineering/gitignore_injector.py` — two new canonical paths.
- `AGENTS.md` — regenerated (Codex subsection) via `sync --agents-md`.
- tests for each new unit + the registry path.
- `private/OPEN-ITEMS.md` — record the deferred items (file-level scope,
  Bash gating, PostToolUse, wider real-time breadth).

## 7. Verified Codex facts (first-hand, developers.openai.com/codex/hooks)

| Fact | Verified value |
|---|---|
| PreToolUse can block | Yes — `permissionDecision: "deny"` on stdout, **or** exit 2 + stderr |
| Recommended block path | stdout JSON `hookSpecificOutput.permissionDecision = "deny"` |
| PreToolUse stdin keys | `tool_name`, `tool_input.command`, `cwd`, `turn_id`, `session_id`, `permission_mode` (no `file_path`) |
| Edit tool name | `apply_patch` (patch text in `tool_input.command`) |
| Coverage limit | only simple shell + `apply_patch`; not WebSearch / non-shell-non-MCP |
| hooks.json schema | `{ "hooks": { "PreToolUse": [ { "matcher", "hooks": [ { "type": "command", "command", "timeout?", "statusMessage?" } ] } ] } }` |
| `matcher` semantics | **regex** ("a regex string that filters when hooks fire"); doc's own examples are anchored (`^Bash$`, `^apply_patch$`); `""`/`"*"`/omitted = match all |
| apply_patch matcher | `matcher` may use `apply_patch` / `Edit` / `Write` aliases → use `^(apply_patch\|Edit\|Write)$`; but the hook **input always reports `tool_name: "apply_patch"`** regardless of which alias fired |
| Enable flag | hooks ON by default; `[features] hooks = false` only disables → install writes nothing |
| Config locations | `<repo>/.codex/hooks.json` (+ `~/.codex/`, `config.toml` inline) |
| SessionStart context injection | Yes — plain stdout text is added as developer context (also `hookSpecificOutput.additionalContext`) |
| Trust model | new/changed hooks skipped until human `/hooks` trust (trust keyed to hook hash) |
| Hook exec env | session cwd as working dir; no guaranteed PATH (examples use absolute paths) → absolute command |

## 8. Open questions — none blocking

All load-bearing unknowns were resolved by first-hand verification (§7). The two
design decisions that needed a human call (absolute-vs-bare command; include
SessionStart) are resolved in §3.2 / §2. Remaining items are explicit deferrals
(§4), not open questions.

### Resolved by adversarial review (round 1)
- **`file=None` does not neuter the gate** — verified against `gates/pre_tool_use.py`
  + `gates/decisions.py` + `daemon/supervisor.py`: the verdict is state-only;
  `file` is cosmetic (label-building). The state-gate gives the Codex adapter the
  gate's full primary teeth (§4.2 confirmed correct).
- **`matcher` semantics were an unverified load-bearing claim** — now verified
  first-hand (regex; §7) and the matcher broadened to the edit-name union to
  close the silent-fail-open risk (§3.3).
- **"Zero core changes" was false at the CLI layer** — `cli/adapter.py` prints
  Claude-hardcoded paths for every adapter; §3.6 + §6 now own that fix.
- SessionStart marker collision + corrupt-file fail-loud now specified (§3.3).
- The live blocked-`apply_patch` smoke is elevated to a required pre-trust gate
  (§5), since the stdout-deny-+-exit-0 contract is the one thing standing between
  a working gate and a silent fail-open.

### Resolved by adversarial review (round 2)
- **Gitignore was in the wrong place** — round-1 had `install_hooks` injecting
  gitignore (contradicting §3.4 + the Claude shape). Removed: gitignore is the
  static `_CANONICAL_PATHS` block rendered by `init`/`sync` only (§3.2 step list
  + §3.4 now consistent); this also closes the rollback-gap the reviewer probed.
- **Fail-loud must validate `hooks.SessionStart` too**, not just PreToolUse (§3.3).
- **§3.6 pinned to the two exact install literals** (`adapter.py:218`/`:220`);
  uninstall success line is already generic; the new ABC method must be a plain
  non-abstract `def` (the `watch_paths`/`spec_paths` precedent) so no concrete
  adapter is forced to reimplement it.
- **Input `tool_name` is always `apply_patch`** (matcher aliases are an input/matcher
  distinction) — §7 + §3.1 corrected; harmless because the verdict is tool-name-
  independent, but prevents a future `tool_name`-branching bug.
- Confirmed CORRECT against code: `file=None` non-neutering, matcher-is-regex,
  fail-loud pattern, and exit-0+deny-JSON block contract.
