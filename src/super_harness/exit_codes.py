"""Exit code constants for super-harness CLI commands.

Per cli-command-surface §2.2 global convention:
- 0 = success
- 1 = generic error (uncategorized failure)
- 2 = validation / gate block (verification failed / lifecycle illegal / etc.)
- 3 = config not found (.harness/ missing / yaml corrupt)
- 4 = external tool failed (gh CLI missing / git operation failed)
- 5 = concurrency conflict (file lock contention)
- 64-78 reserved for v0.2 plugin extensions (BSD sysexits.h compatible)
"""
EXIT_OK = 0
EXIT_GENERIC = 1
EXIT_VALIDATION = 2
EXIT_NO_CONFIG = 3
EXIT_EXTERNAL_TOOL = 4
EXIT_CONCURRENCY = 5
