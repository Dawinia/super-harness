"""E2E happy-path lifecycle tests for the openspec + claude-code stack.

Single test (`test_full_lifecycle.py`) is the v0.1 ship gate — see plan
§16 for the full reconcile notes on which lifecycle events are bridged
via `EventWriter.emit(skip_validation=True)` until the v0.2 reviewer
subagent integration emits them for real.
"""
