---
change: 2026-08-09-authorization-channel
---
# Authorization channel — design

Cut A of two. This cut covers `review authorize` end to end. `review human
confirm` reuses the same machinery in cut B and is a non-goal here.

## Why: the TTY gate stops the honest path and nobody else

`review authorize` refuses to run without an interactive TTY, with no `--yes`
path by design. The intent was a Ulysses pact: one automated review round costs
one deliberate human act, and a code agent must not be able to fund its own
rounds.

Both halves of that intent fail in practice.

**It does not stop an agent.** Twelve lines of stdlib defeat it. Measured from a
non-interactive agent subprocess on 2026-08-09:

```
pid, fd = pty.fork()          # child execs the command
os.write(fd, b"y\n")          # answers the click.confirm prompt
→ tty: True     got: 'y'
```

Both `sys.stdin.isatty()` and `sys.stdout.isatty()` report `True`, and the
`y/N` confirm is answered. `script -q /dev/null <cmd>` does the same with a
stock macOS tool. The gate costs a determined agent one line.

**It does stop the human.** In a Claude Code session the human's own `!` prefix
runs commands without a TTY (measured — see the probe below), so the one path
that is unambiguously a human act is the path the gate rejects. The observed
workflow is therefore: the agent relays the block, the human opens a separate
terminal, `cd`s to the repo, pastes a 76-character command, and returns to the
session to say it is done. That has happened on every brake bite so far.

So the check buys nothing on the axis it was built for and taxes the axis it was
not. It has to go — but removing it alone would leave `review authorize` freely
runnable by the agent with no record at all, which is strictly worse than today.
The replacement is a record, not a wall.

### What replaced the TTY check, and why it is not another wall

Nothing on disk resists an agent with shell access: `events.jsonl` is
gitignored, has no hash chain, and can be rewritten wholesale. `.harness/
attestations/*.jsonl`, by contrast, is committed and lands in the PR diff.

That asymmetry decides the design. The teeth are not in blocking the command;
they are in a **count a human can falsify from memory**. An attestation line
reading `authorizations: 5 (human_typed 2, agent 3)` is checkable by the person
who remembers authorizing twice. No individual event is checkable that way, and
no local file is tamper-proof, so the count in the committed artifact is the
only anchor that survives contact with an adversarial agent.

### Measured: `!` does not go through the Bash tool

The whole channel mechanism rests on one premise, so it was tested before the
design was fixed. A temporary `Bash`-matcher `PreToolUse` hook appending its
stdin to a file, then one human `!` command and several agent commands:

```
entries: 4
entries whose command IS the human probe: NONE
```

Every agent tool call appears; neither `! echo HUMAN_PROBE_1` nor
`! echo HUMAN_PROBE_2` does. The hook payload also carries
`"permission_mode": "bypassPermissions"`, which settles the second premise:
**hooks fire under bypass mode**, where host permission prompts do not.

## Design

### One rule, stated once

Every `review_round_authorized` event records **how the harness observed the
invocation's origin**, and an origin the harness could not attribute to a human
must be disclosed before merge.

`channel` is deliberately not a claim that a human approved. That claim lives in
`--reason`, in the human's own words, and is unverifiable by construction.
`channel` is the circumstantial evidence filed alongside it.

### 1. The gate comes out

`_interactive_terminal()` and the `click.confirm` prompt are removed from
`review authorize`. `--reason` stays required. With both gone, `!` works from
inside the session, and its output already triggers the agent's next turn — the
human types one line and nothing else.

`review human draft --pager` keeps its TTY check: a pager genuinely needs a
terminal and `--pager` is optional. `init`, `sync --agents-md` and
`adapter uninstall` are untouched.

### 2. A breadcrumb the agent cannot avoid writing

The Claude Code adapter registers a **second** `PreToolUse` entry, separate from
the existing gate entry:

```json
{ "matcher": "Bash",
  "hooks": [{ "type": "command",
              "command": "cat > <abs>/.harness/.last-agent-bash.json  # super-harness-breadcrumb",
              "timeout": 5 }] }
```

`Bash` must not be added to the existing `Edit|Write|MultiEdit|NotebookEdit`
matcher. That entry runs the Python gate binary, which would put a process
launch on every shell command and — far worse — **block all Bash in gated
states**.

A matching `PostToolUse` entry removes the file when the call finishes:

```json
{ "matcher": "Bash",
  "hooks": [{ "type": "command",
              "command": "rm -f <abs>/.harness/.last-agent-bash.json  # super-harness-breadcrumb",
              "timeout": 5 }] }
```

The pair is what makes the file mean **an agent Bash call is in flight right
now**, which is the only thing §3 needs it to mean. Neither hook parses or
decides anything; the pre-hook overwrites the file with the payload Claude Code
feeds it, so the file stays one line and the raw payload survives for later
inspection.

Both commands carry the trailing `# super-harness-breadcrumb` comment. That
literal — not the path — is the marker `_settings_merge` matches on, following
the module's existing `_OURS_MARKER` convention and keeping the path literal out
of every file but the seam, as the decision at the end of this document
requires. Uninstall removes both entries through that same marker-based path.

`.harness/.last-agent-bash.json` joins the managed gitignore block's file list in
`engineering/gitignore_injector.py`. This repo does not blanket-ignore
`.harness/` — the block enumerates each volatile file, and the existing
`.harness/.*.lock` line does not cover a `.json`. Omitted, every agent Bash call
would leave an untracked file in the working tree, `git add -A` would commit raw
agent command text, and the committed file would then appear in the PR diff as a
change no lifecycle covers — the merge gate blocking on the harness's own
breadcrumb. Because `init` writes that same constant list downstream, the
omission would reach every adopter, so it is fixed in the injector rather than by
hand-editing this repo's `.gitignore`.

### 3. Channel resolution, in exactly one place

`core/authorization_channel.py` is the only module that reads the breadcrumb or
infers an origin. The resolution is a decision list, read top to bottom, first
match wins — not a table of independent rows, so no combination of inputs can
fall through unclassified:

1. the hook pair is **not installed on every registered agent adapter** →
   `unknown`
2. a breadcrumb file **is present** → `agent` if its command text names this
   invocation, otherwise `unknown`
3. no breadcrumb, and a TTY is present on both stdin and stdout → `tty`
4. no breadcrumb, no TTY → `human_typed`

**Presence is the load-bearing predicate, not content.** The agent cannot avoid
writing the breadcrumb — the hook fires on the tool call, above anything the
command itself can do — but it fully controls what the command *says*. A wrapper
script, a command assembled from a shell variable, or a `sleep` before the real
invocation all leave a breadcrumb whose text names nothing useful. If content
decided the outcome, every one of those would fall through to the most permissive
branch. So rule 2 keys on the file existing at all: any agent shell call in
flight means this invocation cannot be attributed to a human, whatever it says.

Text matching survives only as a **label**: it separates `agent` from `unknown`,
which are treated identically at the merge boundary. A match needs the command
text to contain both the literal `review authorize` and this invocation's
`--reason` value. That predicate is deliberately fragile — quoting, escaping and
variable assembly all defeat it — and its fragility is now harmless, because
failing it downgrades `agent` to `unknown` rather than promoting anything to a
human channel.

Rule 1 comes first because without the hook, absence carries no information, and
a TTY is forgeable in twelve lines: an agent that uninstalls the hook and
allocates a pty must not reach `tty`. It is evaluated across **all** registered
agent adapters, and every one of them must have the pair installed. Any-True
would be a laundering route — registering a second, hookless adapter would make
that host's authorizations resolve `human_typed` and never disclose. All-True
means a repo mixing an observable host with an unobservable one discloses
everything, including genuine human acts. That is noisy and correct: the harness
cannot see one of its hosts.

Rule 2 outranks rule 3 for the same reason it exists: an agent that allocates a
pty still leaves the breadcrumb. This closes the forgery demonstrated at the top
of this document.

Rules 3 and 4 are reachable only on the positive evidence that **no agent shell
call is in flight**. Nothing falls through to them.

Two consequences worth stating plainly:

- **Concurrency is safe.** Claude Code issues several Bash calls in one
  assistant message, so a sibling call may overwrite the breadcrumb with
  unrelated text. Under rule 2 that still resolves `unknown`, which discloses.
  The old content-keyed predicate would have resolved `human_typed`.
- **A leaked breadcrumb fails closed.** If `PostToolUse` never runs (crash,
  timeout, kill), the file survives and subsequent authorizations resolve
  `unknown` until the next normally-completing agent Bash call removes it —
  which, in any live session, is seconds away. There is deliberately **no TTL**:
  a time-based expiry would restore the `sleep`-past-the-window evasion the
  in-flight formulation exists to close.

The resolver deletes nothing. Lifetime belongs entirely to the hook pair, which
is what keeps `core/authorization_channel.py` a pure function of its inputs.

The residual evasion, stated rather than papered over: an agent can detach a
process (`nohup … &`) so the real invocation outlives the tool call whose
breadcrumb would have covered it. Nothing in this design stops that. It is a
single-purpose, unmistakable act, it is visible in the transcript, and it is the
honest boundary of what a file written by a hook can prove.

Whether the hook is installed is not inferred from the breadcrumb file's
existence — a freshly installed repo has the hook but no file yet. The predicate
is `AgentAdapter.breadcrumb_hook_installed(workspace) -> bool`, defaulting to
`False` on the base class, implemented by `ClaudeCodeAdapter` as a marker lookup
in `.claude/settings.local.json` through the existing `_settings_merge` marker
machinery. `cli/review.py` reduces it with **all-True** over every agent adapter
recorded in `adapters.yaml` and passes the single boolean in;
`core/authorization_channel.py` stays pure and never imports an adapter. Zero
registered agent adapters, an adapter that does not implement the predicate, and
an unreadable settings file all yield `False` — and therefore `unknown`. That
covers the Codex case: no breadcrumb hook on that host, so every authorization
in a Codex-registered repo discloses, agent-run or human alike.

The event records the verdict and the observations it came from:

```json
"payload": {
  "reason": "<the human's words>",
  "authorization_channel": "human_typed",
  "channel_evidence": {
    "breadcrumb_hook_installed": true,
    "breadcrumb_matched": false,
    "stdin_tty": false
  }
}
```

Events emitted before this change carry no `authorization_channel`. They are
read as `legacy` and never require disclosure: the rule in force when they were
written was the TTY gate, and they satisfied it.

### 4. The count reaches the merge boundary

The attestation gains a per-channel rollup of the change's authorizations. An
authorization in `agent` or `unknown` is a merge blocker until disclosed, in the
same shape `gate_bypassed` already uses — undisclosed by append order, cleared
by an explicit write.

The verb is **new**: `attest write --disclose-agent-authorization "<why>"`.
Reusing `--disclose-gate-bypass` would fold two different facts into one number,
and the number is the only thing a human can independently check. "Someone wrote
files around the gate" and "a review round was funded by the agent that was
being reviewed" call for different judgments at merge.

### 5. The brake block stops naming a terminal, and stops there

The block today (`cli/review.py`) reads:

```
super-harness review begin: this automated round requires one-shot human authorization
  Hint: Use an interactive TTY: `super-harness review authorize <c> --reviewer <r> --reason <why>`.
```

After §1 that first clause is simply false, and it is also the sentence that
sends the human out to a second terminal — the workflow this cut exists to
delete. It becomes:

```
super-harness review begin: this automated round requires one-shot human authorization
  Hint: relay this block and ask the human to run:
        super-harness review authorize <c> --reviewer <r> --reason "<why>"
```

The `STOP. Relay the block above verbatim…` paragraph is unchanged.

What the new text does **not** say is that the agent could run the command
itself, or that doing so costs a disclosure. That omission is not obscurity —
the agent reads this source. It is placement. This block prints at the exact
moment the agent is deciding whether to route around, and a sentence there
reading "you can run this yourself, it just costs a disclosure" converts an
instruction into a priced, pre-rationalized path. The consequence machinery does
not require the agent to know about it.

The host-specific ergonomics belong in the adapter's `AGENTS.md` subsection, not
here: under Claude Code the human runs that line in-session with the `!` prefix,
whose output already triggers the agent's next turn. That is guidance about what
to tell the human, not an escape hatch.

The fact that agent-run authorizations exist and must be disclosed belongs on
the human side — `report`, the attestation rollup, and the docs explaining the
blocker — because the human is who meets it.

## Rejected

**A `permissions.ask` rule installed by the adapter.** It was the original
second half of this design and it does nothing here. Channel resolution has no
`host_prompt` value, because the CLI can observe only that a rule exists in
config, never that a human clicked. Meanwhile the breadcrumb catches agent-run
authorizations unconditionally, including under bypass mode where the prompt
evaporates. Buying a new class of settings mutation — the adapter has only ever
touched `hooks`, never `permissions` — for something strictly weaker and fully
covered is a bad trade.

**Slicing by mechanism, shipping the gate removal first.** It opens a window
where the agent can fund rounds with no record, and drags `review human confirm`
into the same window. Slicing by command keeps every intermediate state safe:
while cut A is in flight, `human confirm` keeps its current TTY gate — forgeable,
but not worse than today.

**Deferring the merge blocker to a later cut.** A gate assertion is only worth
what this cut measures. The blocker is armed here and is expected to bite this
very change: until the breadcrumb hook is installed in this repo, every
authorization resolves to `unknown` and needs a disclosure. That disclosure on
cut A's own attestation is the acceptance evidence.

## Non-goals

- `review human confirm` — cut B. Its asymmetry is decided but not built here:
  an `agent`/`unknown` channel must **refuse locally**, not record-and-disclose.
  Funding an extra round wastes tokens; fabricating a human review verdict
  fabricates a cognitive act, and no merge-time disclosure can repair "I did in
  fact read that diff".
- The Codex breadcrumb hook. Without it every Codex authorization resolves
  `unknown` and discloses, human or agent alike (§3 rule 1) — noisier than it
  needs to be, but never wrong, and it is the same answer any unobservable host
  gets. The CLI is quota-locked until 2026-08-28 and cannot be tested, and this
  repo does not ship untested adapter surface. Registered as an issue.
- `adapter uninstall` conflating `--quiet` with consent. Real, unrelated, own
  issue.

## Known taxes

- **The human pays for the agent's concurrency.** Any agent Bash call in flight
  when the human authorizes resolves the human's act to `unknown`, costing a
  disclosure. This is the deliberate direction of the trade (§3 rule 2): the
  alternative reading is the one that lets an agent launder. It never costs
  work, only a line at merge.
- **A second `PostToolUse` hook on every Bash call.** One `rm -f`, no
  interpreter start — but the harness now sits on both ends of every shell
  command the agent runs, which is twice the surface for a hook bug to stall a
  session.
- **Every Bash tool call writes a file inside the repo.** One `cat`, no
  interpreter start, so the direct cost is noise — but it is a new write on a
  hot path, and it churns anything watching the tree (file watchers, indexers).
  Keeping it out of `.harness/` would avoid that at the cost of inventing a
  per-workspace path outside the repo; the gitignore line is the cheaper trade.
- **Adopters with neither the hook nor a TTY disclose every time.** That is the
  `unknown` path working as designed, and it is the price of refusing to read
  absence of evidence as evidence.
- **`_settings_merge.py` gains a second managed entry.** That module carries
  atomic replacement, pristine backups and a transaction lock, and its matcher
  is currently a single constant. This is the sharpest edge in the cut.

## Scope

New:

- `src/super_harness/core/authorization_channel.py` — the single seam, and the
  only file naming the breadcrumb path. Pure: takes the workspace root, the
  invocation's `--reason`, the TTY facts and the all-True hook-installed
  boolean; returns the channel plus the evidence dict. Reads the breadcrumb but
  never writes or deletes it. Never raises — an unreadable or malformed
  breadcrumb is still a *present* breadcrumb and resolves to `unknown`, never to
  a human channel.
- `docs/decisions/d-authorization-channel-single-seam.md` — tier-1, with the
  executable check described above.

Modified:

- `cli/review.py` — `review authorize` drops the TTY refusal and the confirm,
  reduces `breadcrumb_hook_installed` with all-True over the registered agent
  adapters, calls the resolver, and records the result. It deletes nothing: the
  breadcrumb's lifetime belongs to the hook pair. The round-budget block's hint
  is rewritten to the exact text in §5.
- `adapters/agent/_settings_merge.py` — a managed `PreToolUse` **and**
  `PostToolUse` entry for the breadcrumb, both keyed on the
  `# super-harness-breadcrumb` marker literal, planned in the same transaction
  and removed symmetrically on uninstall. The existing matcher constant is not
  widened, and the module never spells the breadcrumb path.
- `adapters/__init__.py` — `AgentAdapter.breadcrumb_hook_installed(workspace)`,
  defaulting to `False`, so a host that never grew the hook resolves `unknown`
  rather than inheriting someone else's answer.
- `adapters/agent/claude_code.py` — supplies the breadcrumb command built from
  the constant in `core/authorization_channel.py` plus the workspace-absolute
  path, implements `breadcrumb_hook_installed` as a marker lookup, and names the
  new hook in `installed_detail()`.
- `engineering/gitignore_injector.py` — the breadcrumb joins the managed file
  list; `.gitignore` is regenerated from it, never hand-edited.
- `core/events.py` — the disclosure event type.
- `engineering/attestation.py` — per-channel rollup; undisclosed `agent` or
  `unknown` becomes a blocker, by append order, matching `gate_bypassed`.
- `cli/attest.py` — `--disclose-agent-authorization "<why>"`.
- `engineering/value_report.py` — the rollup reaches `report`.
- `docs/cli-reference.md`, `docs/concepts.md`, `docs/state-machine.md`,
  `docs/getting-started.md` — all four describe the TTY requirement today.
- `.gitignore`, `AGENTS.md` — both regenerated (`sync`), never hand-edited.

Acceptance obligations, beyond unit coverage of the resolution table:

- The channel resolver is exercised against fabricated breadcrumbs for all four
  outcomes and for every ordering that decides between them: hook absent **with**
  a TTY present resolves `unknown`, not `tty`; a present breadcrumb **with** a
  TTY present resolves `agent`, not `tty`; a present breadcrumb whose text names
  nothing resolves `unknown`, **not** `human_typed` (the fail-open branch this
  design was rewritten to remove); a malformed or unreadable breadcrumb resolves
  `unknown`; and `human_typed` is reachable only with no breadcrumb at all.
- All-True is pinned: a repo registering one adapter with the hook and one
  without resolves `unknown`, never `human_typed`.
- `attest verify` fails on a change carrying an undisclosed `agent`/`unknown`
  authorization, and passes once disclosed. This is exercised on real events,
  not mocked rollups.
- Live: at least one authorization in this change resolves and is disclosed —
  the blocker armed in §4 is expected to bite here (see Rejected, third item).

## Decision to record

`d-authorization-channel-single-seam`, tier-1: **the breadcrumb has exactly one
reader.** The path literal, and every read of it, live in
`core/authorization_channel.py`; `claude_code.py` imports the constant to build
the hook command rather than spelling the path itself.

Tier is derived structurally here — `decision_tier` returns 1 only when a
```check``` fenced block is present — so the record carries a runnable command
and a counterexample, in the shape `d-core-is-base` already uses:

````
```check
test -z "$(grep -rl 'last-agent-bash' --include='*.py' src/super_harness \
           | grep -v 'core/authorization_channel\.py')"
```

```counterexample path=src/super_harness/engineering/_ce_channel_seam.py
BREADCRUMB = ".harness/.last-agent-bash.json"  # forbidden: a second reader
```
````

The check covers the mechanical half only, and the record says so. "No other
module infers an invocation's origin" is the intent; "the path literal appears
in one file" is what a grep can hold. The narrower predicate is still the one
that matters, because a second reader has to name the file before it can invent
its own verdict.

The rule earns tier-1 because the resolution is a four-outcome decision list
with a precedence rule and a fail-closed first branch. A second site that "also
works it out" will drift, and it will drift permissive — the loose copy stays
silent and lets things through, so nothing reports the divergence. This is the
failure `core/parse_ts.py` was consolidated to prevent, and there it was found
by review rather than by design.
