# Design — Move the PreToolUse gate in-process; demote the daemon to an optional observer

**Date:** 2026-07-03
**Scope:** `daemon/hook_entry.py` (decision path rewrite), `daemon/client.py` + `daemon/protocol.py` + `daemon/hot_state.py` (delete), `daemon/supervisor.py` (shrink to observer lifecycle), `daemon/server.py` (strip UDS/gate dispatch; keep process shell as observer host), `cli/daemon.py` → `cli/observe.py` (rename), a consolidated state-snapshot loader in `core/`, `docs/decisions/d-single-gate-policy.md` (re-ratify: wording says "daemon + in-process gate both read it"), `docs/daemon-architecture` spec (rewrite + re-ratify), and the daemon test tree. Resolves REVIEW-FINDINGS §F7 and §F8 in one cut.

## Problem

The PreToolUse gate hot path routes every tool-call decision through a
long-running UDS daemon. A month of self-host dogfooding produced hard
evidence that this architecture does not earn its rent and actively fails:

- **The gate has been silently fail-open for most of the month.** The
  supervisor spawns the daemon by bare name (`Popen(["super-harness-daemon",
  ...])`, `supervisor.py`), relying on PATH. The hook itself is registered by
  absolute venv path, and the hook's environment has no venv on PATH — so the
  spawn's `OSError` is swallowed and the daemon never comes up from the hot
  path. Fallback audit logs show ~200 permissive ALLOWs **per day**
  (197 on 2026-07-01, 231 on 2026-07-02, files back to 06-25);
  `daemon.log` records exactly **2 starts in a month**. Even the manual
  `super-harness daemon start` fails on the same PATH dependency.
- **When the daemon *was* up, it blocked wrongly.** Its only sustained uptime
  window (2026-07-02 02:40–04:32) is exactly when the stale-change bug
  (HG-STALE-MERGED-CHANGE, fixed in #62) was hijacking the gate. Net dogfood
  experience: daemon up → wrong blocks; daemon down → no enforcement.
- **The latency justification is hollow.** Measured on Apple Silicon with this
  repo's 67 KB / 51-change `state.yaml`:

  | measurement | wall clock |
  |---|---|
  | bare Python interpreter cold start | ~50 ms |
  | hook end-to-end, daemon up | ~140–150 ms |
  | hook end-to-end, daemon down (fail-open path) | ~150–250 ms |
  | warm UDS roundtrip alone | **0.16 ms** |
  | one `state.yaml` parse, pure-Python SafeLoader | **68 ms** |
  | same parse, CSafeLoader (libyaml) | 7 ms |

  The hot path's cost is interpreter cold start + one YAML parse — both paid
  by the hook process **before** it talks to the daemon
  (`_decide` → `read_active_change_id` fully parses `state.yaml` just to get
  the change_id, then discards the parse). The daemon's entire contribution is
  re-reading the same file to apply a 10-row table lookup. Net benefit of the
  roundtrip: ≈ 0 ms. If latency ever matters, the lever is CSafeLoader
  (68 ms → 7 ms), not a resident process.
- **The gate decision is implemented twice** (F8): in-process
  `PreToolUseGate` (returns `suggested_action`) and the daemon's table
  dispatch (drops it) — so a blocked agent gets no "what to do next" line on
  the hot path, only the generic halt hint.

## Root causes

**R1 — A pure function was modeled as a service.** The gate decision is
`f(state.yaml, tool, file) → (allow|block, reason, suggestion)`: pure,
read-only, a 10-row table at its core. Service-izing it bolted on process
lifecycle (spawn/pidfile/flock), transport (UDS), protocol versioning, cache
coherence (`HotState` mtime), a reachability failure mode (fail-open), and the
inevitable dual implementation. All of it is accidental complexity a pure
function does not need. The month of silent fail-open is not an unlucky
implementation bug — it is the predictable consequence of making an
enforcement point depend on a process that may not be there. PATH was merely
this month's trigger.

**R2 — The API boundary cuts through the middle of one atomic read.**
change_id resolution lives client-side (hook fully parses `state.yaml`),
record lookup lives server-side (daemon re-reads the same file for the same
record). One logical read — "read the state, look up the active change" — is
split across a protocol boundary, and both halves pay a full parse. When a
boundary forces the same data to be read once on each side, the boundary
itself is wrong. Collateral debt from the same split: the defensive
"state.yaml may be corrupt / non-mapping" parse exists in **three** copies
(`hot_state.py`, `active_change.py`, `cli/gate.py:_read_change_state`), and
`HotState`'s `mtime <= self._mtime` cache carries the classic same-timestamp
double-write staleness hazard.

**R3 — Two unrelated responsibilities share one process, inverting the
reliability hierarchy.** Synchronous decision-making (must be reliable, runs
on every edit) and resident observation (optional, best-effort, only
meaningful when a framework adapter has watch paths) were bundled because "we
have a daemon anyway". Result: the component that most needs reliability (the
gate) depends on the least reliable component in the system (a resident
process that must be findable, version-matched, and alive), and the failure
mode is silent permission. Every mechanism that has actually drawn blood in
dogfood — merge gate, CI attest-verify, Stop-hook authoring feedback — is a
short-lived process reading files; none touches the daemon. (Verified:
`core/authoring_check.py` is explicitly "no daemon" — its "daemon threads"
are `threading.Thread(daemon=True)` in the hook process; the Codex Stop path
never crosses the UDS either. G-FEEDFORWARD has zero coupling to the daemon.)

**The repo already has the right architecture; the daemon RPC sidesteps it.**
`events.jsonl` is the append-only SSOT, `state.yaml` the derived view, and
PR #67 put a cross-process flock on the write path. The daemon's RPC is a
private control plane bolted next to that data plane — and it doesn't even
reuse it, it *re-reads* it. Removing the RPC is not removing a feature; it is
returning to the repo's single paradigm: **processes talk through the event
log and derived files, not through sockets.**

## Design: two planes

```
┌─ Decision plane (synchronous · pure · in-process · every tool call) ─┐
│                                                                      │
│  Entry layer (thin shells, one per agent, envelope format only)      │
│    claude-code shim │ codex shim │ positional │ gate check CLI       │
│         └──────────────┬─────────────────────────┘                   │
│  Snapshot layer (THE single I/O seam)                                │
│    load_state_snapshot(root) → one parse                             │
│      ├ active change (env override > recency, per #62)               │
│      ├ change record                                                 │
│      └ the three defensive parses consolidate here; CSafeLoader      │
│        preferred (68 ms → 7 ms), SafeLoader fallback                 │
│  Policy layer (pure function, zero I/O)                              │
│    PreToolUseGate.decide(snapshot, action)                           │
│      → reads PRE_TOOL_USE_DECISIONS + SUGGESTIONS (SSOT unchanged)   │
└──────────────────────────────────────────────────────────────────────┘

┌─ Observation plane (asynchronous · optional · resident · explicit) ──┐
│  framework observer (watchdog)                                       │
│    one job: watch framework artifacts → EventWriter.emit (#67 flock) │
│    → post_emit refreshes state.yaml → decision plane sees it on its  │
│      next file read                                                  │
│  liveness = pidfile-flock probe (try LOCK_NB; held ⇒ alive)          │
│  interface to the decision plane = the filesystem;                   │
│  zero sockets, zero protocol, zero client                            │
└──────────────────────────────────────────────────────────────────────┘
```

The planes have **no runtime dependency on each other** — the interface is
`events.jsonl`/`state.yaml`: files that already exist, are already locked
(#67), and were validated by 19 self-host lifecycle iterations. Observer dead
→ decision plane unaffected (framework artifacts need a manual
`adapter scan-once`). Decision plane does not know the observer exists.

### Component disposition

| current component | fate | rationale |
|---|---|---|
| `gates/decisions.py` + `gates/pre_tool_use.py` | unchanged | already the SSOT + pure engine |
| `hook_entry._decide` | rewire to snapshot + policy layers | reuse the already-proven in-process path (`gate check` CLI is documented "NO daemon dependency"); block message gains `suggested_action` |
| `client.py` / `protocol.py` / `hot_state.py` | delete | exist only to serve the RPC |
| `supervisor.py` hot half (fail-open / fire-and-forget spawn / version-mismatch dance / 200 ms timeout knob) | delete | the whole failure class disappears |
| `supervisor.py` CLI half (`ensure_running` / `is_running`) | shrink into observation plane; spawn by **absolute path** resolved from `sys.argv[0]`'s directory (kills the PATH bug class at the root) | explicit observer start still needs it |
| `server.py` UDS accept loop + gate dispatch | delete; process shell survives as observer host | the observer needs no request/response interface; liveness via pidfile flock deletes the entire protocol layer |
| `framework_observer.py` | unchanged | the only thing that genuinely needs a resident process |
| `operation-logs/` daemon-fallback audit channel | retire | "daemon unreachable" no longer exists; kill-switch bypass already audits via `gate_bypassed` events — audit converges on events.jsonl |
| `cli/daemon.py` (`daemon start/stop/status`) | rename to `observe start/stop/status` | the process's only job is observation; pre-public window makes the ratified-surface rename a one-time cost (decided 2026-07-03) |

### Failure model (closed set)

The decision plane's permissive outcomes shrink from an open set (process
reachability × protocol version × cache freshness × PATH) to a **closed,
deterministic, enumerable** set of three — semantics all preserved from today:

1. no `.harness/` in workspace → allow ("not our concern")
2. kill switch (`.harness/gate-disabled`) → allow + `gate_bypassed` event
3. `state.yaml` missing/corrupt/no non-terminal change → allow ("no active
   change")

Each is a pure function of the filesystem at decision time; each is testable
without spawning anything.

## Why this holds up to scrutiny

**Extensibility lives in two existing seams, not in a resident process.**

- The four ratified cold-path gates (`pre-commit`, `pre-push`, `pr-open`,
  `pr-merge`, Phase 12/13) are all "snapshot + events → pure verdict" — they
  drop straight into the decision plane; none needs a daemon. This is the
  forward test that the decision plane's shape is right.
- New agents plug in at the entry layer (three shapes already proven:
  claude-code / codex / positional); the policy layer is agent-agnostic —
  axis B portability is untouched.
- Multi-actor (OPEN #3) rides the event log, not an RPC; the observer is
  naturally "one writer among many" — #67's flock already paid for that.
- Performance headroom: a 7 ms CSafeLoader parse is ample for a gate. If
  `state.yaml` ever grows 100×, the snapshot layer is the **single socket**
  where a faster implementation (mmap index, or even a cache process) plugs
  in — policy and entry layers untouched. If a daemon ever deserves to come
  back, it comes back *behind the snapshot seam*, not astride the decision
  path.

**Review cost collapses.** Today, answering "will this edit be blocked?"
requires reasoning about daemon liveness, PATH, protocol version, mtime cache
freshness, and a 200 ms timeout — five distributed-systems questions guarding
a 10-row table. After this change it is two pure functions.

**Honest counter-arguments, examined and rejected:**
- *"A warm process avoids cold start"* — the hook process is a fresh Python
  every time; the daemon never could absorb that (correction already on
  record in the daemon-architecture memory).
- *"Centralized write serialization"* — solved writer-side by #67's flock.
- *"Watch immediacy"* — the gate is pull-model; after a `scan-once` the next
  file read sees the events. Nothing on the decision path needs push.
- *"Future IDE/LSP-style long connection"* — no such requirement exists;
  YAGNI.

The only surviving justification is continuous framework observation — and it
needs no RPC, only the event log.

## Migration & taxes (walk the pothole list before `declare scope`)

- **Re-ratify tax:** `d-single-gate-policy`'s ratified body says "daemon +
  in-process gate both read it" — the reader count changes → body edit →
  `decision ratify` re-approval (pothole #6, #66); sweep the same phrasing in
  `gates/decisions.py` docstring and `server.py` comments in the same cut.
  The `daemon-architecture` spec is rewritten and re-ratified (AC-2 50 ms
  budget, UC-6 respawn dance, and the client/supervisor split all die with
  the RPC).
- **Tier-2 reconcile tax:** `server.py` and `hook_entry.py` sit on
  reconciled anchors — check `reconciled_anchors` before declaring scope
  (pothole #2).
- **Test surface:** protocol/client/hot_state/supervisor-hot-path unit +
  integration tests retire with their modules; `hook_entry` integration
  tests rewrite as in-process assertions (and the
  `SUPER_HARNESS_HOOK_QUERY_TIMEOUT` test-only knob — itself a smell — is
  deleted); observer-side tests survive.
- **Regeneration surface:** `AGENTS.md` via `sync --agents-md`;
  getting-started; `cli-command-surface` wording for the renamed `observe`
  command group.
- **Adapter install:** unchanged — the hook command line does not change.
- **`.gitignore` block (#65):** `daemon.pid`/`daemon.log` entries follow the
  rename (observer still writes both); `daemon.sock` disappears entirely.

## Settled decisions

- Direction confirmed 2026-07-03: decision plane in-process, RPC layer
  retired, observer kept as optional resident process (demote, don't delete —
  `framework_observer.py` is the one genuinely resident concern; delete later
  if evidence shows nobody starts it).
- `daemon` → `observe` command rename: **approved** (pre-public window; the
  conservative "keep name, change semantics" option was considered and
  declined).
- The PATH spawn bug needs no standalone fix: the hot-path spawn is deleted;
  the observer's explicit start resolves the binary by absolute path.
