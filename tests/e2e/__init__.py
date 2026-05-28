"""End-to-end tests exercising production entry points (subprocess the real CLIs).

Unlike `tests/integration/`, these tests do NOT import internal helpers or
cross-package conftest fixtures: they drive the installed ``super-harness`` /
``super-harness-daemon`` / ``super-harness-hook`` binaries by name (which MUST
be resolvable on PATH) so the full install → daemon → hook gate path is proven
exactly as a user would experience it.
"""
