---
id: d-core-is-base
status: ratified
ratified_by: dawinialo@163.com
ratified_at: '2026-06-27T05:43:35.587478Z'
ratified_text_hash: sha256:671c31c0f690031610e9357e803ee375ad8e7b3966376b6afb783943139e4288
---
core/ is the base layer: it must not import the orchestration layers cli/gates.

`super_harness.core` is the pure foundation the upper layers build on. It must not
depend (directly OR transitively) on the orchestration layers `super_harness.cli` or
`super_harness.gates`, so the core can be imported (e.g. by the daemon) without dragging
in the CLI/gate stack. The faithful mechanical form of this invariant is an import-graph
contract, not a text grep: `grep` sees only direct textual imports and is blind to the
transitive and function-local edges that actually break layering. The rung-1 check is the
import-linter `core-is-base` contract in `.importlinter`.

(`sensors` is intentionally NOT yet covered: `core.review_bundle` reaches `sensors`
transitively via a function-local `adapters` import — a real coupling import-linter
caught that grep declared clean. Fixing it is tracked separately.)

```check
PYTHONPATH=src lint-imports --config .importlinter --contract core-is-base --no-cache
```

```counterexample path=src/super_harness/core/_ce_core_is_base.py
from super_harness.cli import plan  # forbidden: core importing an orchestration layer
```
