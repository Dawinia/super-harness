# Design — Remove custom plugin loading (v0.1 builtin-only)

> 2026-07-06. F12 (pre-public hardening). Root-fix, not a per-call-site guard.

## Problem

super-harness ships a general-purpose arbitrary-code-execution primitive:
`core/_plugin_loader.load_class_from_path` does `importlib` `exec_module()` on a
file path taken from `.harness/*.yaml`, then instantiates the named class. Three
loaders funnel through it:

- `adapters/registry.load_adapters` — `builtin: false` + `path:` + `class:` entries
- `core/_registry.load_components` (sensors + gates) — dict entries `{id: {path, class}}`

This is real, reachable arbitrary code execution. It is triggered automatically by
`super-harness sync` / `sync --check` (which runs in CI, `.github/workflows/doc-check.yml`),
`super-harness init`, and the opt-in `observe start` host. The PreToolUse / Stop hot
path is NOT affected (it uses the builtin `PreToolUseGate` directly and never loads
plugins); `sensor list` / `gate list` load plugins only on that explicit command.

**The feature is 100% latent:** no shipped CLI writes a `builtin: false` / dict-plugin
entry (`adapter install` accepts builtin names only; the auto-managed adapters.yaml
carries a "Do not edit" header and only ever contains builtins). No docs teach it. Only
unit tests exercise it. Meanwhile the real plugin trust model (sandboxing / isolation) is
already deferred to v0.2 in `docs/limitations.md`.

**Pre-public risk:** a malicious `.harness/adapters.yaml` in a PR causes CI (`sync --check`)
to execute attacker code; and any user who clones a tampered repo and runs
`sync`/`init`/`observe start` gets local RCE ("config-flavored curl | bash"). This is the
one hardening blocker for going public.

## Decision

Remove the arbitrary-exec mechanism entirely in v0.1. Not a guard at each call site —
delete the primitive so no live path can `exec_module` a user-supplied file. The v0.2
extension seam is deleted too (full removal, not a disabled stub); v0.2 rebuilds it
alongside a real sandbox.

## Scope of removal

Source:
- **Delete** `src/super_harness/core/_plugin_loader.py` (the only home of `exec_module`).
- `src/super_harness/adapters/registry.py`: delete `_resolve_custom`, drop the
  `load_class_from_path` import; `load_adapters` resolves builtin entries only.
- `src/super_harness/core/_registry.py`: delete `_load_plugin`, delete `read_plugin_paths`,
  delete the `builtin_only` parameter, and turn the dict-entry branch into a rejection.
- `src/super_harness/sensors/registry.py`, `src/super_harness/gates/registry.py`: drop the
  `builtin_only` parameter (now meaningless).
- `src/super_harness/cli/sensor.py`, `src/super_harness/cli/gate.py`: drop `builtin_only=`
  args + `read_plugin_paths`; remove the "plugin" column/wording from `list` output, command
  help, and module docstrings.
- `src/super_harness/core/paths.py`: drop the "enumerate plugin entries" wording from the
  `sensors_yaml_path` / `gates_yaml_path` docstrings.
- `src/super_harness/cli/adapter.py`: relabel the dead `else "custom"` adapter source in
  `adapter list` to `"unsupported"` (display-only; reads raw yaml, never imports — not an RCE
  path, but completes the seam removal so no user surface presents "custom adapters").

## Behavior contract

A non-builtin config entry is now unsupported and the loader **raises** a clear
`ValueError`: `"custom plugins are not supported in v0.1 (builtin-only); see limitations"`.

- adapters: an entry whose `builtin` is not literally `true` (`false`, or the key omitted) →
  raise. **The reject fires BEFORE the `enabled: false` skip**, so a disabled non-builtin
  cannot slip through silently — the invariant is "any non-builtin is rejected, always." Only
  a disabled *builtin* is skipped.
- sensors/gates: a dict-form entry (old `{id: {path, class}}`) → raise.

This matches the loaders' existing raise-on-bad-config style (unknown builtin → raise,
malformed schema → raise). **Where the raise surfaces depends on the caller:**
- `sensor list` / `gate list` translate it to a visible `EXIT_VALIDATION` error.
- The three runtime adapter callers (`cli/sync.py`, `daemon/framework_observer`,
  `engineering/agents_md_render`) already wrap load in `try/except` and **degrade to
  advisory-skip / no-watchers** — so for adapters the raise is honest but always downgraded
  to a stderr advisory (there is no adapter command that surfaces it loudly; `adapter list`
  reads raw yaml and never calls the loader). This is deliberate and pre-existing fail-safe
  behavior: `sync --check` skips AGENTS reinjection rather than failing CI on a corrupt
  adapters.yaml. The point of this change is not to fail loud — it is that **no `exec`
  happens on any path**; the raise simply replaces the exec.

## Testing (TDD, RED first)

Load-bearing security regression: build a `.harness/adapters.yaml` with
`builtin: false, path: ./evil.py, class: X`, where importing `evil.py` writes a sentinel
file as a side effect. Assert `load_adapters()` raises AND the sentinel is never created.
Pre-removal this is RED (the file gets exec'd → sentinel appears); post-removal GREEN.
Symmetric tests for sensors and gates (dict-form plugin entry).

Structural test: `super_harness.core._plugin_loader` no longer imports; no `exec_module`
call remains in the package.

Convert existing custom-loading unit tests into "rejects non-builtin entry without exec"
tests; delete `_plugin_loader` tests.

## Docs

- `docs/limitations.md`: add "custom sensor/gate/adapter plugins are not in v0.1
  (builtin-only); the plugin trust model + sandbox land in v0.2."
- `docs/cli-reference.md`: drop "plugin" from the `gate list` / `sensor list` descriptions.
- Update docstrings in the touched files (remove "custom entries dynamically imported" /
  "executes arbitrary contributor code" language).
- `private/specs/` architecture specs: light annotation only ("v0.1 ships builtin-only,
  plugin loading deferred to v0.2") — historical design records, not gate-enforced; no
  rewrite.
- `AGENTS.md`: regenerated via `sync --agents-md` (not hand-edited).

## Non-goals

- `verification.yaml` runs project shell commands via subprocess — a separate, intentional,
  documented capability (running the adopter's own tests/lint). Out of F12 scope.
- Reintroducing plugins with a sandbox — v0.2.

## Blast-radius note

No decision record (`docs/decisions/d-*.md`) anchors any file in scope → no tier-2 reconcile
tax. Self-host lifecycle scope must still enumerate every file above (attest verify blocks
on any uncovered change).
