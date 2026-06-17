# Design: AGENTS.md decision-conformance guidance + sync drift gate

Date: 2026-06-17
Status: approved (brainstorm converged)
Slice: SLICE-4 follow-up (NEW 2026-06-17, OPEN-ITEMS) — the local-sensor layer the
decision text-lock (Tool A, PR #40) and executable-checks (Tool B, PR #41) designs
both rely on, finally wired into the generated AGENTS.md.

## 1. The gap (functional, not cosmetic)

`docs/plans/2026-06-12-decision-text-lock-design.md` §4.1 defines three layers for
where `decision check` runs, sorted by hardness:

| Layer | Mechanism | Status before this slice |
|---|---|---|
| CI hard gate | CI runs full `decision check` before merge | **done** (decision-check.yml) |
| Agent self-check (portable) | the project's agent instructions (AGENTS.md) tell the agent to run `decision check` at natural checkpoints | **MISSING** |
| Hook auto-fire | Claude-Code PostToolUse runs it automatically | deferred (separate slice) |

The middle layer is the one this slice fills. Tool A and Tool B shipped the
machinery (`decision check` does reference-integrity + text-lock + executable
checks), but the **generated** `AGENTS.md` never tells an agent the command
exists. The claude-code adapter subsection (`adapters/agent/claude_code.py`) only
covers the gate + review protocol; the outer framework template
(`engineering/agents_md_render.py`) covers branch naming / PR / scope / verify —
neither mentions `decision check` or `doc check`. So for an unattended agent the
local early-warning layer **never fires**; only CI catches drift, half a day and
many commits later. This is a functional gap: the §4.1 sensor is designed but not
wired into the product.

`AGENTS.md` is a **generated artifact** (marker-bounded, stamped
`DO NOT EDIT MANUALLY`). Hand-editing it is overwritten by `sync`. The only real
fix is to change the template that generates it, then regenerate — a code change.

## 2. Scope of this slice

Two halves:

- **Half 1 — content (the gap fix).** Add a `### Decision conformance` section to
  the outer framework template so every super-harness user gets it via
  `init`/`sync`, regardless of which agent adapter is installed.
- **Half 2 — drift gate.** Add `sync --check` (a shippable dry-run that fails on
  drift) so (a) downstream users can self-check "my AGENTS.md is behind the
  template after a CLI upgrade", and (b) this repo's own CI catches a stale
  generated `AGENTS.md`. One command serves both.

Out of scope (registered in OPEN-ITEMS):
- Hook auto-fire (PostToolUse) — already-deferred §4.1 layer, Claude-Code-only.
- Richer per-decision UX in `decision show` / timeouts — pre-existing Tool B defers.

## 3. Half 1 — placement and content

### 3.1 Placement decision: outer framework section, NOT the claude-code subsection

The brief's literal wording said "add it to the claude-code section". Brainstorm
diverged, justified against the §4.1 authority the brief itself cites:

- §4.1 frames this layer as **portable — "works for any agent, zero per-harness
  integration"**. `decision check` / `decision ratify --dry-run` / `doc check` are
  plain CLI commands any agent with shell access runs. Nothing is Claude-Code
  specific.
- By contrast the existing claude-code subsection content *is* CC-specific:
  PreToolUse hook recovery, dispatching a reviewer via the `Task` tool, the
  `Bash`-never-gated kill switch. The review protocol lives there because it
  depends on the `Task` tool. Decision conformance has no such dependency.
- Distribution consequence: a downstream user sees the claude-code subsection only
  if they installed that adapter. Putting decision-conformance in the **outer
  framework template** (`_AGENTS_MD_SECTION_TEMPLATE`) reaches **every**
  super-harness user — including no-agent repos and any future second agent
  adapter — with zero duplication.

Honest calibration: v0.1 ships only the claude-code agent adapter, so today A and B
reach nearly the same humans; the difference is no-agent repos and the next
adapter. B is the architecturally correct, future-proof, zero-duplication home.

Placement within the outer template: a new `### Decision conformance` section after
`### File scope` (both are "stay inside the declared constraints" concerns).

### 3.2 Content

Rendered verbatim into `_AGENTS_MD_SECTION_TEMPLATE`, matching the terse,
imperative style of the sibling outer sections:

```markdown
### Decision conformance

Ratified decisions under `docs/decisions/` are binding: super-harness
hash-locks each decision's text and, where configured, attaches an executable
check. Treat `super-harness decision check` as a LOCAL SENSOR you consult while
you work — CI runs it too as the un-bypassable floor, so keep it green locally.

- **At natural checkpoints** (a chunk done, before you commit) run
  `super-harness decision check --changed`. A non-zero exit means you violated a
  ratified decision or edited a ratified decision's body text — fix it before
  continuing; don't push the drift downstream to CI.
- **Don't hand-edit the body of a ratified decision.** Its text is hash-locked;
  re-ratifying (`super-harness decision ratify <id>`) is the only unlock, and is
  a deliberate, recorded act.
- **Attaching an executable check to a decision?** Before you propose it, run
  `super-harness decision ratify <id> --dry-run` to confirm the check actually
  bites (runs the bite-test without ratifying).
- `super-harness decision check` (full) and `super-harness doc check` are also
  CI gates — keep both green locally so a push never bounces.
```

Covers the four brief points: (1) checkpoint `--changed` sensor, non-zero = fix
first; (2) `ratify --dry-run` self-test before proposing a check; (3) full
`decision check` + `doc check` are CI gates, keep green locally; (4) never
hand-edit ratified body text, re-ratify is the only unlock.

Commands verified against the live CLI (project venv): `decision check --changed`,
`decision ratify <id> --dry-run` ("Run the bite-test only; do not ratify"),
`doc check` all exist as described.

### 3.3 Regeneration

After editing the template, regenerate this repo's tracked `AGENTS.md` with
`super-harness sync --agents-md` and commit it. (Confirmed: `sync` re-renders the
outer section via the shared `render_super_harness_section` SSOT, so init and sync
never drift.)

## 4. Half 2 — `sync --check` drift gate

### 4.1 Why this shape (it subsumes a derived-doc registration)

An earlier brainstorm option registered `AGENTS.md` in `.harness/derived-docs.yaml`
and let the existing `doc check` engine diff it. That is **project-internal only**
(`derived-docs.yaml` and `scripts/gen_*` are this repo's dev tooling, not shipped)
and does nothing for the real user pain: *a user upgrades the CLI, their AGENTS.md
falls behind the new template, and nothing tells them.*

`sync --check` is a **shippable** CLI feature of roughly the same cost (both need
the same render-to-string refactor) that serves both needs with one command:
- Downstream users run `super-harness sync --check` in their CI to detect a stale
  generated section after a CLI upgrade.
- This repo runs the same command in its own CI as the self-hosting drift gate.

So `sync --check` replaces the derived-doc registration entirely — no
`gen_agents_md.py`, no `derived-docs.yaml` entry.

### 4.2 Semantics

`sync --check` is a dry-run in the `prettier --check` / `black --check` /
`terraform plan` family: **compute what `sync` would write, compare to what is on
disk, write nothing, and exit non-zero with a diff if they differ.**

- **Scope follows the existing sync flags.** `sync --check` checks both managed
  artifacts (the AGENTS.md super-harness section AND the `.gitignore` block).
  `sync --agents-md --check` checks only AGENTS.md; `sync --gitignore --check` only
  `.gitignore`. `--check` composes with these scope flags rather than introducing a
  new scope axis. `--adapter <name>` is NOT supported with `--check` (it is
  rejected with a clear message): the AGENTS.md render re-injects every installed
  adapter subsection, so the `--agents-md` check already covers adapter-subsection
  drift — a per-adapter check would be redundant surface.
- **Exit codes.** Clean (no drift) → `EXIT_OK` (0). Drift detected →
  `EXIT_VALIDATION` (2), matching `doc check`'s drift exit code. Config / IO errors
  reuse sync's existing envelope (`EXIT_NO_CONFIG` / `EXIT_GENERIC`). **Exit 2 is
  reserved exclusively for drift** so a CI step can key `exit 2 ⇒ regenerate`.
  Therefore the `--adapter` + `--check` rejection must NOT use `click.UsageError` /
  `BadOptionUsage` (Click exits those with code 2, colliding with drift): reject via
  `format_error(...)` + `sys.exit(EXIT_GENERIC)` (1), matching every other sync
  error path.
- **Output.** Print a unified diff per drifted artifact (bounded, like
  `doc_check`'s `_DIFF_MAX_LINES`) so the failure tells the reader exactly what to
  regenerate. `--json` is not honored in v0.1 (consistent with `sync` today).
- **No writes, no prompt.** `--check` never writes, so the overwrite-confirm prompt
  is irrelevant and skipped.

### 4.3 How `--check` gets the canonical content (temp-copy, zero render refactor)

`render_super_harness_section` and `inject_gitignore_block` are path-based: they
write into a file. Rather than refactor them to also render-to-string, `--check`
**renders into a throwaway temp copy** and diffs that against the real file:

1. **Byte-copy** `root/AGENTS.md` (if present) into a `TemporaryDirectory` with
   `shutil.copyfile` — a byte copy, not a text round-trip, so a CRLF file's line
   endings survive for the injector's newline detection.
2. Call the EXACT same `render_super_harness_section(root, tmp_agents, version)` the
   write path uses — it reads adapters from the real `root` (only `agents_path`
   points at the temp copy) and replaces the existing section in place. Reading the
   temp copy back yields the canonical content with zero changes to the render SSOT.
3. **Normalize line endings on BOTH sides** (the rendered temp copy AND the on-disk
   file, as `doc_check._normalize` does) before diffing; non-empty diff = drift.

The `.gitignore` half is identical with `inject_gitignore_block(tmp_gitignore)`.
Both injectors preserve content outside their markers, so the diff isolates only
the managed-block drift. This reuses the production write path verbatim against
disposable copies — no second template, no render-to-string surface.

**Absent artifact = drift.** If `root/AGENTS.md` (or `.gitignore`) is absent, the
copy step is skipped and the injector writes the full section/block into the temp
path; the on-disk side is empty, so the whole artifact shows as drift → exit 2.
This is deliberate: a repo missing a managed artifact *is* out of sync with what
`sync` would write (e.g. `init` never run, or the file deleted). The bounded diff
and `EXIT_VALIDATION` are reused for it; no special-casing.

### 4.4 CI wiring

Add one step — `super-harness sync --check` — to an existing workflow (the
`doc-check.yml` "generated-artifact drift" workflow is the natural sibling; merge
gate is the alternative). No new workflow file.

## 5. Tests

- **Half 1:** unit test asserting the rendered outer section contains
  `### Decision conformance` and the three commands (`decision check`,
  `decision ratify ... --dry-run`, `doc check`). Lives alongside the existing
  `agents_md_render` / outer-section render tests (not `test_claude_code.py`, since
  the content moved out of the adapter).
- **Half 2:** unit/integration tests for `sync --check`: (a) clean repo →
  exit 0, no output diff, no write; (b) drifted AGENTS.md (hand-mutated managed
  section) → exit 2 + diff, file unchanged; (c) scope flags narrow the check;
  (d) byte-identical `.gitignore` / AGENTS.md round-trips clean after a real
  `sync`.

## 6. Files touched

- `src/super_harness/engineering/agents_md_render.py` — add `### Decision
  conformance` to `_AGENTS_MD_SECTION_TEMPLATE`.
- `AGENTS.md` — regenerated via `sync --agents-md` (tracked; committed).
- `src/super_harness/core/sync_check.py` — new: temp-copy render + diff
  (`run_sync_check`).
- `src/super_harness/cli/sync.py` — add `--check` flag + dispatch.
- `.github/workflows/doc-check.yml` (or merge-gate) — one `sync --check` step.
- `tests/unit/...` (+ integration) — per §5.
- `private/OPEN-ITEMS.md` — mark the NEW(2026-06-17) item done; register any
  remaining follow-ups (e.g. PostToolUse hook auto-fire stays deferred).

## 7. Honest limits

- `sync --check` is the **portable** drift gate, but like every CI gate it is the
  floor, not a guarantee an agent runs the local sensor mid-work. The §4.1 local
  sensor remains soft-by-design (bypassable); its job is cheap turnaround for a
  cooperative agent, with CI as the un-bypassable backstop. This slice wires the
  sensor's *instructions*; it does not make the sensor mandatory (that is the
  deferred PostToolUse hook layer).
- The drift gate covers the **managed** artifacts only (the marker-bounded AGENTS.md
  section + the `.gitignore` block). User content outside the markers is never
  inspected — by design.
