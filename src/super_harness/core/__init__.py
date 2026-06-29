"""super_harness.core — the pure base layer.

Must not import the upper layers (`cli`, `gates`, `sensors`), directly or
transitively, so the core can be imported (e.g. by the daemon) without dragging
in the CLI/gate/sensor stack. This invariant is enforced as a rung-1
architecture-fitness check; see the `core-is-base` contract in `.importlinter`.
"""
# @decision:d-core-is-base
