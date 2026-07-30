---
id: d-no-backticked-nonexistent-identifiers
status: proposed
---
PROPOSED (unsettled): do not backtick an identifier that does not resolve in source, even to name an anti-pattern — the dead-reference gate exits 2 on it and has no negative-context detection.

## What happens

`super-harness doc refs --gate` reads a backticked snake_case token in prose as a pointer
to a real symbol. It has no notion of a negating clause, so a sentence that names a field
in order to forbid it — "no hand-maintained ref-count metadata" — trips the same
high-confidence dead reference as a genuinely stale pointer would. The gate exits 2, and it
is a separate CI job from `doc check`, which passes independently.

The cost is not the wrong diagnosis; it is where the hit lands. A decision body is
hash-locked at `ratify`, and `AWAITING_CODE_REVIEW` freezes `docs/decisions/` with no
CLI-reachable way back (see `d-no-recovery-from-awaiting-code-review`). Learning this after
`done` therefore costs a re-ratify plus a full plan cycle. That is exactly what it cost the
change that produced this record.

## The rule

Say it in prose, unbackticked, when the thing you are naming does not exist in source.
"hand-maintained maturity labels or usage counters" carries the same meaning as the
backticked field names and does not trip the gate. Reserve backticks for identifiers a
reader could go and find.

And run `doc refs --gate` **checking its exit code, not its output**, before `done`. Its
findings print in a shape that reads like warnings.

## Why this is not fixed

Two candidate fixes were considered and both rejected.

Detecting the negating clause is prose parsing. This engine is deliberately string-and-set
work only — it never runs a model — and a heuristic that decides "this sentence means the
opposite" would be the first piece of language interpretation in it, with its own false
positives in both directions.

An explicit inline opt-out marker would work mechanically, but it buys a new authoring
surface — one more thing to learn, to spell right, and to leave behind when the prose it
was protecting is rewritten — in exchange for one word of rewording. The gate's bias
toward false positives over missed dead links is the correct bias, and the workaround is a
rule an author can simply follow, with immediate feedback when they do not.

So this is a recorded precondition rather than a defect to fix: it is knowledge you need
*before* you write a decision body, not a bug someone is going to close.

**Exit:** retire this record if the checker ever grows a sane opt-out, or if the
`AWAITING_CODE_REVIEW` recovery verb lands and makes the mistake cheap to undo.
