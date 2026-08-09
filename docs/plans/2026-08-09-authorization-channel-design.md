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
              "command": "cat > <abs>/.harness/.last-agent-bash.json",
              "timeout": 5 }] }
```

`Bash` must not be added to the existing `Edit|Write|MultiEdit|NotebookEdit`
matcher. That entry runs the Python gate binary, which would put a process
launch on every shell command and — far worse — **block all Bash in gated
states**.

The hook parses nothing and decides nothing: it overwrites a single file with
the hook payload Claude Code feeds it. Overwrite, not append, so the file stays
one line; mtime is the timestamp; the raw payload is preserved so the judgment
can be revisited later without archaeology. Uninstall removes the entry through
the existing marker-based path.

### 3. Channel resolution, in exactly one place

`core/authorization_channel.py` is the only module that reads the breadcrumb or
infers an origin. Four values:

| channel | when |
|---|---|
| `agent` | breadcrumb matches this invocation |
| `human_typed` | hook installed, no match, no TTY |
| `tty` | hook installed, no match, TTY present |
| `unknown` | no TTY **and** the hook is absent or unreadable |

A breadcrumb **matches** when its command text contains both the literal
`review authorize` and this invocation's `--reason` string verbatim. Requiring
the reason is what makes the predicate tight: a `--reason` is a required,
human-written sentence, so an unrelated agent command essentially cannot collide
with it, while any genuine invocation contains both. Matching on
`review authorize` alone was rejected after a probe false-positived on a script
that merely printed the string — reading source or grepping for the command name
would misclassify the human's next authorization.

Precedence: **a breadcrumb match outranks a TTY.** This is what closes the pty
forgery. An agent that allocates a pty still leaves the breadcrumb, and the
breadcrumb wins.

`unknown` is the fail-closed default and is treated exactly like `agent`
downstream. Absence of a hook is absence of evidence, not evidence of a human —
and equating the two also removes the incentive to uninstall the hook to launder
an authorization.

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

### 5. The brake block says nothing about this

The round-budget block keeps telling the agent to stop and relay. It does **not**
mention that the command now runs without a TTY, and does not mention the
disclosure price.

This is not obscurity — the agent reads this source. It is placement. The block
prints at the exact moment the agent is deciding whether to route around, and a
sentence there reading "you can run this yourself, it just costs a disclosure"
converts an instruction into a priced, pre-rationalized path. The consequence
machinery does not require the agent to know about it.

The human side is where the fact belongs: `report`, the attestation rollup, and
the docs that explain the blocker — because the human is who meets it.

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
- The Codex breadcrumb hook. Codex has no human-typed shell, so a human there
  uses a real terminal (`tty`) and the agent resolves to `unknown` — which is
  the correct verdict. Deferring costs nothing, and the CLI is quota-locked
  until 2026-08-28, so it cannot be tested. Registered as an issue.
- `adapter uninstall` conflating `--quiet` with consent. Real, unrelated, own
  issue.

## Known taxes

- **The false positive is a stale breadcrumb.** If the agent runs an
  authorization and the human runs the same one before any other agent shell
  command intervenes, the human's is read as `agent`. The CLI consumes the
  breadcrumb on match and ignores matches older than the invocation's own start,
  which bounds it; the residue costs one disclosure, never lost work.
- **Every Bash tool call writes a file.** One `cat` with no interpreter start.
  Measured cost is noise, but it is a write on a hot path and it is new.
- **Adopters with neither the hook nor a TTY disclose every time.** That is the
  `unknown` path working as designed, and it is the price of refusing to read
  absence of evidence as evidence.
- **`_settings_merge.py` gains a second managed entry.** That module carries
  atomic replacement, pristine backups and a transaction lock, and its matcher
  is currently a single constant. This is the sharpest edge in the cut.

## Scope

New:

- `src/super_harness/core/authorization_channel.py` — the single seam. Pure:
  takes the breadcrumb path, the invocation's `--reason`, the TTY facts and
  whether the hook is installed; returns the channel plus the evidence dict.
  Never raises — an unreadable or malformed breadcrumb resolves to `unknown`,
  never to a human channel.
- `docs/decisions/d-authorization-channel-single-seam.md` — tier-1, with the
  executable check described above.

Modified:

- `cli/review.py` — `review authorize` drops the TTY refusal and the confirm,
  resolves the channel, records it, and consumes a matched breadcrumb. The
  round-budget block's wording is unchanged in substance (§5).
- `adapters/agent/_settings_merge.py` — a second managed `PreToolUse` entry with
  its own marker, planned in the same transaction and removed symmetrically on
  uninstall. The existing matcher constant is not widened.
- `adapters/agent/claude_code.py` — supplies the breadcrumb command with the
  workspace-absolute path; `installed_detail()` names the new hook.
- `core/events.py` — the disclosure event type.
- `engineering/attestation.py` — per-channel rollup; undisclosed `agent` or
  `unknown` becomes a blocker, by append order, matching `gate_bypassed`.
- `cli/attest.py` — `--disclose-agent-authorization "<why>"`.
- `engineering/value_report.py` — the rollup reaches `report`.
- `docs/cli-reference.md`, `docs/concepts.md`, `docs/state-machine.md`,
  `docs/getting-started.md` — all four describe the TTY requirement today.
- `AGENTS.md` — regenerated via `sync --agents-md`, not hand-edited.

Acceptance obligations, beyond unit coverage of the resolution table:

- The channel resolver is exercised against fabricated breadcrumbs for all four
  values, including the precedence case (breadcrumb match **with** a TTY present
  must resolve `agent`) and the malformed-breadcrumb fail-closed case.
- `attest verify` fails on a change carrying an undisclosed `agent`/`unknown`
  authorization, and passes once disclosed. This is exercised on real events,
  not mocked rollups.
- Live: at least one authorization in this change resolves and is disclosed —
  the blocker armed in §4 is expected to bite here (see Rejected, third item).

## Decision to record

Tier-1, with an executable check: **channel resolution has exactly one
implementation.** No module other than `core/authorization_channel.py` may read
the breadcrumb file or infer an invocation's origin.

Four values, one precedence rule, one fail-closed default. A second site that
"also works it out" will drift, and it will drift permissive — the loose copy
stays silent and lets things through, so nothing reports the divergence. This is
the failure `core/parse_ts.py` was consolidated to prevent, found there by
review rather than by design.
