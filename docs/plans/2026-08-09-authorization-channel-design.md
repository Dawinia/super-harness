---
change: 2026-08-09-authorization-channel
---
# Authorization record — design

The TTY gate on `review authorize` comes out, and what replaces it is a count in
the committed attestation, not another wall.

## Why: the gate stops the honest path and nobody else

`review authorize` refuses to run without an interactive TTY, with no `--yes`
path by design. The intent was a Ulysses pact: one automated review round costs
one deliberate human act, and a code agent must not be able to fund its own
rounds.

Both halves fail in practice.

**It does not stop an agent.** Twelve lines of stdlib defeat it. Measured from a
non-interactive agent subprocess on 2026-08-09:

```
pid, fd = pty.fork()          # child execs the command
os.write(fd, b"y\n")          # answers the click.confirm prompt
→ tty: True     got: 'y'
```

Both `isatty()` calls report True and the `y/N` confirm is answered.
`script -q /dev/null <cmd>` does the same with a stock macOS tool.

**It does stop the human.** In a Claude Code session the human's own `!` prefix
runs commands without a TTY (measured). So the one path that is unambiguously a
human act is the path the gate rejects, and the observed workflow is: the agent
relays the block, the human opens a separate terminal, `cd`s to the repo, pastes
a 76-character command, and comes back to say it is done. That happened on every
brake bite so far — including the one this change itself triggered at round 7 of
its own plan review.

The check buys nothing on the axis it was built for and taxes the axis it was
not.

## What can actually be enforced, and what cannot

Nothing on disk resists an agent with shell access: `events.jsonl` is
gitignored, has no hash chain, and can be rewritten wholesale. Worse, the
harness cannot tell an agent-run command from a human-typed one at all — process
ancestry and environment are byte-identical between the two (measured: same
`/bin/bash → /bin/zsh → claude` chain, the same `AI_AGENT` and
`CLAUDE_CODE_CHILD_SESSION` variables, neither with a TTY).

So there is no honest basis for a rule of the form "block agent-run
authorizations". Any such rule enforces a distinction the harness cannot make.

What survives is `.harness/attestations/*.jsonl`: committed, in the PR diff, and
therefore read by a human at the one moment that matters. The teeth are a **count
a human can falsify from memory** — a line reading `authorizations: 5` is
checkable by the person who remembers authorizing twice. No individual event is
checkable that way. The count is the whole mechanism.

## Design

### 1. The gate comes out

`_interactive_terminal()` and the `click.confirm` prompt are removed from
`review authorize`. Both must go: `!` supplies no stdin either, so leaving the
confirm would trade one refusal for another.

`--reason` stays required and is recorded verbatim. It is the human's own words,
unverifiable by construction, and the only claim of approval this design makes.

With the gate gone, `!` works from inside the session, and its output already
triggers the agent's next turn — the human types one line and nothing else.

`review human draft --pager` keeps its TTY check: a pager genuinely needs a
terminal, and `--pager` is optional. `init`, `sync --agents-md` and
`adapter uninstall` are untouched.

### 2. The count reaches the merge boundary

The attestation gains one line: how many automated review rounds this change
authorized, with the `--reason` recorded for each. `report` surfaces the same.

There is **no new blocker and no new disclosure verb.** A blocker must name a
condition, and every candidate here ("the agent did it") is one the harness
cannot evaluate. Blocking on something unmeasurable yields either a rule that
never fires or a rule that fires on everything — theatre, or a tax on every
honest authorization. The number in the PR diff is the enforcement, and its
reader is the person merging.

### 3. The brake block stops naming a terminal

The block today reads:

```
super-harness review begin: this automated round requires one-shot human authorization
  Hint: Use an interactive TTY: `super-harness review authorize <c> --reviewer <r> --reason <why>`.
```

After §1 the first clause is false, and it is also the sentence that sends the
human to a second terminal. It becomes:

```
super-harness review begin: this automated round requires one-shot human authorization
  Hint: relay this block and ask the human to run:
        super-harness review authorize <c> --reviewer <r> --reason "<why>"
```

The `STOP. Relay the block above verbatim…` paragraph is unchanged.

What the new text does **not** say is that the agent could run the command
itself. That omission is placement, not obscurity — the agent reads this source.
The block prints at the exact moment the agent is deciding whether to route
around, and a sentence there describing a sanctioned self-service path converts
an instruction into a priced one. Host-specific ergonomics (under Claude Code the
human runs it in-session with `!`) belong in the adapter's `AGENTS.md`
subsection: that is guidance about what to tell the human, not an escape hatch.

## Rejected: a breadcrumb channel that identifies the caller

This design originally carried a second mechanism. A `Bash`-matcher `PreToolUse`
hook would write a breadcrumb, letting `review authorize` tell an agent-run
invocation from a human-typed one, record a `channel` per authorization, and
block agent-run rounds at merge until disclosed.

The premise was verified, not assumed: a human's `!` command does **not** go
through the Bash tool, and hooks fire even under `bypassPermissions`. The signal
genuinely exists. What did not hold was any implementation of it.

Seven review rounds produced 26 findings. **Twenty-five were about that
mechanism.** The gate removal produced one (this document's own false hint text);
the attestation count produced none. Each round's blocker was introduced by the
previous round's fix:

| round | the fix | what it opened |
|---|---|---|
| 2 | one shared breadcrumb file | a sibling call's text overwrote it |
| 3 | pair entries by `tool_use_id` | a payload echoing an id hijacked the match |
| 5 | `mktemp` + delete any one | concurrent deletes ratchet the count permanently |
| 6 | clear the directory on authorize | two authorizes in one Bash call launder |

The shape is consistent: each fix added structure, and each new structure added a
concurrency or coupling surface the previous one lacked. Round 5 tried the
opposite direction — remove structure, parse nothing — and improved immediately,
until round 6 added structure back to fix what removing it had cost.

The honest reading is that the mechanism cannot carry what it was asked to carry
at this size. It is also not what the teeth were made of: the falsifiable count
needs none of it. Channel would have upgraded "5 authorizations, you remember 2"
into "and these 3 were agent-run" — better reading, not better evidence.

Registered as an issue together with this history, so it can be reconsidered if
the count ever shows something that wants explaining.

## Rejected: a `permissions.ask` rule installed by the adapter

The adapter has only ever touched `hooks`; making it mutate `permissions` is a
new class of settings surgery. It also buys nothing in a session running in
bypass mode, which is the normal mode here — the prompt evaporates exactly when
it would matter.

## Non-goals

- `review human confirm`. Its TTY gate is equally forgeable, but it is not worse
  than today, and its semantics differ: it asserts a *cognitive* act ("I read
  this diff"), which no merge-time disclosure can repair. It keeps its gate until
  there is a mechanism worth putting behind it.
- `adapter uninstall` conflating `--quiet` with consent. Real, unrelated, its own
  issue.

## Known taxes

- **An agent can now fund its own review rounds with one command.** That is the
  deliberate trade. It could already do so at the cost of one `pty.fork()`; now
  the act lands in a count the human reads at merge instead of being invisible.
  The tax is that the count is the only thing standing there.
- **The count is only as good as its reader.** It fails silently against a human
  who merges without looking. Nothing here fixes that, and nothing pretends to.

## Scope

Modified:

- `cli/review.py` — `review authorize` drops the TTY refusal and the confirm; the
  round-budget block's hint becomes the exact text in §3.
- `engineering/attestation.py` — the authorization count and reasons join the
  attestation.
- `engineering/value_report.py` — the same rollup reaches `report`.
- `docs/cli-reference.md`, `docs/concepts.md`, `docs/state-machine.md`,
  `docs/getting-started.md` — all four describe the TTY requirement today.
- `AGENTS.md` — regenerated via `sync --agents-md`, never hand-edited.

Acceptance obligations, beyond unit coverage:

- `review authorize` succeeds with neither stdin nor stdout a TTY — the `!` case,
  which is the whole point and which the confirm alone would still have blocked.
- The attestation of a change with N authorizations reports N and every recorded
  reason, exercised on real events rather than a mocked rollup.
- Live: this change's own attestation carries its round-7 authorization, with the
  reason the human typed.
