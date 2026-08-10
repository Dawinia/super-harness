---
change: 2026-08-09-authorization-channel
---
# Authorization record — design

The TTY gate on `review authorize` comes out. It is replaced by a count `report`
can show — not by another wall, and not by a claim about the merge boundary that
this repo's plumbing cannot cash.

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

What survives is a **count a human can falsify from memory**: `Authorizations: 5`
is checkable by the person who remembers authorizing twice. No individual event
is checkable that way, and no local file is tamper-proof, so the count is the
whole mechanism.

Evidence and surface are separate obligations, and only the first is already
met. `.harness/attestations/*.jsonl` is the **evidence**: committed, in the PR
diff, and carrying every authorization event verbatim today. No **surface** at
the merge boundary survived contact with this repo (§2 lists the three that were
tried and what killed each), so this change delivers the count where it is
correct — `report` — and stops there rather than shipping a number that would be
zero or stale exactly when it mattered.

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

### 2. The count becomes readable locally — and no claim is made past that

The evidence is already committed. An attestation file is a verbatim event
snapshot, so every `review_round_authorized` event — reason included — is
*already* in the PR diff today. What is missing is not the record but its
legibility: nobody audits raw JSONL while merging, so the anchor this design
rests on is present and unread.

So this adds one derivation and one rendered surface. `derive_authorizations`
computes the count and the recorded reasons; `report` shows them, via
`value_report.py` for the field and `cli/report.py` for the rendering —
`value_report.py` alone would compute something nothing displays.

**That is the whole of it, and it is visibility, not enforcement.** Three
merge-boundary surfaces were tried and each failed on a fact about this repo:

- *The attestation file.* It is a verbatim event snapshot that `derive_state` and
  `find_ordering_violations` parse; a synthetic summary line would change a
  format those readers depend on.
- *`attest verify` stdout.* That is a CI check log. Nobody reads it on the way to
  clicking merge, so a count there is delivered to a reader this design cannot
  claim exists.
- *The PR description's metadata block.* `build_metadata` derives from
  `.harness/events.jsonl` — gitignored, and absent from the CI checkout where the
  only automated writer runs. It would print `Authorizations: 0` for a change
  with five. It is also written once at `pr_opened`, so later authorizations
  would be missed; and this repo installs no such workflow at all, so on the
  self-host path the line would never appear.

An understated count is worse than none: it lets the falsify-from-memory check
pass against a number that is wrong in the direction that hides the abuse. So the
count is not routed to the merge boundary, and this document does not claim it
is. `report` is a command a human runs deliberately, against local events that
are actually present — the one place the number is both correct and read on
purpose.

There is likewise **no new blocker and no new disclosure verb.** A blocker must
name a condition, and every candidate here ("the agent did it") is one the
harness cannot evaluate.

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
- **The count is only as good as its reader, and nothing puts it in front of
  them.** It appears when someone runs `report`. Routing it to the merge boundary
  is left undone rather than faked; if that is wanted, it needs plumbing this
  change does not build (see §2 for what each candidate surface lacks).
- **`--reason` records what was typed, not what was true.** Of this change's own
  four authorizations, three carry the literal placeholder from the relayed
  command (`<你的理由>`, `<真话>`, `<why>`). Nothing validates it, and nothing
  can.

## Scope

Modified:

- `cli/review.py` — `review authorize` drops the TTY refusal and the confirm; the
  round-budget block's hint becomes the exact text in §3.
- `engineering/value_report.py` — `derive_authorizations`, built on the same
  event iteration `report` already uses. It deliberately does **not** live in
  `attestation.py`: nothing at the merge boundary consumes it, and the two
  modules read events through different parsers (strict `parse_event_line` vs
  the tolerant dict iterator), so a shared derivation would have to pick one
  contract and break the other's.
- `engineering/value_report.py` — the rollup on `ValueReport`.
- `cli/report.py` — renders it. A computed field nothing shows is the failure
  this cut is built to avoid, so the rendering is scope, not a follow-up.
- `scripts/gen_cli_reference.py` — the **source** of the stale exit-code text
  (`_EXIT_CODES`, the `review authorize` entries only: "human declined the
  interactive confirmation" and "non-TTY, …"). The identically-worded
  `review human confirm` entries stay: that gate is a non-goal.
- `docs/cli-reference.md` — regenerated from the above, never hand-edited (it
  carries an AUTOGENERATED banner and `doc check` flags drift).
- `adapters/agent/claude_code.py` — the AGENTS.md subsection string, which is
  where the host-specific `!` guidance from §3 lives. It says nothing about
  `review authorize` today.
- `AGENTS.md` — regenerated via `sync --agents-md` from that string, never
  hand-edited.
- `tests/unit/cli/test_report.py` — the rendered surface.

Not in scope, having been checked rather than assumed: `docs/concepts.md`,
`docs/getting-started.md` and `docs/state-machine.md` contain no TTY claim about
`review authorize`. Their only TTY sentences are about `review human confirm`'s
nonce and about `init`'s wizard — editing those would document the removal of a
gate this change deliberately keeps.

Acceptance obligations, beyond unit coverage:

- `review authorize` succeeds with neither stdin nor stdout a TTY — the `!` case,
  which is the whole point, and which the confirm alone would still have blocked
  after the `isatty` check was removed.
- `report` is exercised on its **rendered output**, not its derivation, because
  a computed-but-unshown count is the failure mode this cut exists to avoid —
  against real events rather than a mocked rollup.
- `doc check` passes with no drift after regeneration, proving the exit-code text
  was fixed at its generator rather than in the generated file.
- Live: `report` on this change shows its own authorizations — there were four,
  and three of their reasons are the placeholder text from the command the agent
  relayed, which is the Known tax below in its natural habitat.
