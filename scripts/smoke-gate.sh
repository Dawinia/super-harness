#!/usr/bin/env bash
# scripts/smoke-gate.sh — manual smoke test for the pre-tool-use gate (Phase 5).
#
# Spins up a throwaway workspace, starts the daemon, and verifies the gate
# BLOCKS (exit 2) in a blocking lifecycle state and ALLOWS (exit 0) after the
# state advances — via BOTH paths:
#   - `super-harness gate check pre-tool-use`        (in-process decision)
#   - `super-harness-hook --agent claude-code`       (the EXACT path Claude Code
#                                                     drives: JSON on stdin -> exit code)
#
# It does NOT verify that a live Claude Code session honours exit 2 to actually
# block an Edit — that needs a real CC session (open one in the printed $WS with
# the claude-code adapter installed) and is the one step this can't automate.
#
# Usage:  bash scripts/smoke-gate.sh
# Exit:   0 = all checks passed, 1 = a check failed / setup error.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
VENV_BIN="$REPO/.venv/bin"

if [ -x "$VENV_BIN/super-harness" ]; then
  export PATH="$VENV_BIN:$PATH"
elif ! command -v super-harness >/dev/null 2>&1; then
  echo "ERROR: super-harness not found ($VENV_BIN missing and not on PATH)." >&2
  echo "       Build the repo venv, or 'pipx install super-harness'." >&2
  exit 1
fi

WS="$(mktemp -d)"
cleanup() {
  super-harness --workspace "$WS" daemon stop >/dev/null 2>&1 || true
  rm -rf "$WS"
}
trap cleanup EXIT

PAYLOAD='{"hook_event_name":"PreToolUse","tool_name":"Edit","tool_input":{"file_path":"foo.py"}}'
fail=0

# check <label> <expected_rc> <actual_rc>
check() {
  if [ "$2" = "$3" ]; then
    printf '  PASS  %s (exit %s)\n' "$1" "$3"
  else
    printf '  FAIL  %s (expected %s, got %s)\n' "$1" "$2" "$3"
    fail=1
  fi
}

echo "workspace: $WS"
super-harness --workspace "$WS" init        >/dev/null || { echo "ERROR: init failed" >&2; exit 1; }
super-harness --workspace "$WS" change start demo >/dev/null || { echo "ERROR: change start failed" >&2; exit 1; }
super-harness --workspace "$WS" daemon start >/dev/null || { echo "ERROR: daemon start failed" >&2; exit 1; }

echo "== state INTENT_DECLARED (expect BLOCK / exit 2) =="
super-harness --workspace "$WS" gate check pre-tool-use --tool Edit --file foo.py >/dev/null 2>&1
check "gate check" 2 $?
# super-harness-hook is cwd-based (no --workspace; Claude Code runs it from the
# project dir), so invoke it from inside $WS.
( cd "$WS" && printf '%s' "$PAYLOAD" | super-harness-hook --agent claude-code ) >/dev/null 2>&1
check "hook shim " 2 $?

# Advance to a permitting state. No CLI emits plan_approved yet, so write
# state.yaml with the production writer; the daemon picks it up via mtime reload.
python - "$WS" <<'PY'
import sys
from pathlib import Path
from super_harness.core.state import ChangeState
from super_harness.core.state_yaml import read_state_yaml, write_state_yaml

p = Path(sys.argv[1]) / ".harness" / "state.yaml"
data = read_state_yaml(p)
changes = data["changes"]
cid = next(iter(changes))
rec = dict(changes[cid])
rec["current_state"] = "PLAN_APPROVED"
write_state_yaml(p, {cid: ChangeState(**rec)}, last_reduced_event_id="manual")
PY
sleep 0.4

echo "== state PLAN_APPROVED (expect ALLOW / exit 0) =="
super-harness --workspace "$WS" gate check pre-tool-use --tool Edit --file foo.py >/dev/null 2>&1
check "gate check" 0 $?
( cd "$WS" && printf '%s' "$PAYLOAD" | super-harness-hook --agent claude-code ) >/dev/null 2>&1
check "hook shim " 0 $?

echo ""
if [ "$fail" = 0 ]; then
  echo "ALL PASS — gate blocks in INTENT_DECLARED, allows in PLAN_APPROVED."
  echo "Manual next step (can't automate): open a real Claude Code session in a"
  echo "workspace with 'super-harness adapter install claude-code' + a blocking"
  echo "state, ask it to Edit a file, and confirm the edit is blocked. Record the"
  echo "Claude Code version."
else
  echo "SOME CHECKS FAILED."
fi
exit "$fail"
