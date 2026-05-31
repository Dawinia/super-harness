"""End-to-end tests exercising production entry points (subprocess the real CLIs).

These tests drive the installed ``super-harness`` / ``super-harness-daemon`` /
``super-harness-hook`` binaries by name (which MUST be resolvable on PATH) so
the full install → daemon → hook → on-merge → L1-updater path is proven
exactly as a user would experience it. The Phase 16 ship-gate test
(``test_full_lifecycle.py``) additionally uses ``EventWriter`` directly at
three explicitly annotated gap-bridge points (events with no v0.1 production
emitter) and shares helpers via ``tests/e2e/conftest.py``.
"""
