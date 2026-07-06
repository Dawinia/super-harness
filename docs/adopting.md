# Adopting super-harness in your project

super-harness earns its keep when it binds a rule *your team actually cares
about* so an AI agent can't quietly break it. Two guides walk the two halves of
that:

## 1. Discover which rules to lock

Before you can lock a rule you have to know which rules matter. For a mature
codebase the architecture already exists implicitly in the code, and the
maintainer's mental model has usually drifted from what the code actually does.

The **discovering-architecture-norms** skill mines your codebase for candidate
norms (dependency-direction / layering rules) and hands you a ranked list of
hypotheses to ratify. Your own agent runs it — super-harness does not spawn it.

- Skill: [`skills/discovering-architecture-norms/SKILL.md`](https://github.com/Dawinia/super-harness/blob/main/skills/discovering-architecture-norms/SKILL.md)
  (private repo during v0.1; needs repo access until the public release).

## 2. Lock a rule so the agent can't break it

Once you know the rule, arm it with the decision-conformance mechanism: a
ratified decision record + an executable check that *bites* (passes on current
code, fails on a counterexample). From then on, violating code is blocked in CI.

- Guide: [Arm an architecture rule](architecture-fitness.md).

## Where this fits the lifecycle

Adopting is orthogonal to the per-change lifecycle in [Concepts](concepts.md):
you lock rules once (they live in `docs/decisions/`), and every subsequent change
is checked against them by `super-harness decision check`. New to the lifecycle
itself? Start with the [Getting started](getting-started.md) walkthrough.
