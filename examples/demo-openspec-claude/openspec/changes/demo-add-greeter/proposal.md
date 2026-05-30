# Add a `greet()` helper to demo super-harness

## Why

This change exists to demonstrate the super-harness lifecycle end-to-end inside
a realistic OpenSpec + Claude Code workspace. The actual code change is tiny by
design — the value is in watching state advance from `INTENT_DECLARED` through
`AWAITING_CODE_REVIEW` while the gate, sensors, and L1 follow-up wire up around
you.

## What changes

- Add `greet(name: str) -> str` to `src/greeter.py`.
- Add a baseline test in `tests/test_greeter.py`.
- Verify the `greeter` capability spec declares the required behavior (already
  done in the seeded version under `openspec/specs/greeter/spec.md`).
