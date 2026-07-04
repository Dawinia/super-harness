# Golden output — discovering-architecture-norms on this fixture

A correct run produces a ranked candidate list equivalent to:

1. **[framework-boundary · CLEAN · top-tier]** Renderer (`src/`) must not import
   Electron/Node builtins (electron/fs/path); it goes through the preload bridge.
   Evidence: `electron/main.ts` uses them; `src/renderer.tsx` uses `bridge`, zero
   builtin imports. Why/breaks: process isolation / security — a direct import
   breaks the sandbox. Status: currently clean → lockable now.
2. **[layering · VIOLATED]** `src/lib` (logic) must not import UP into the UI layer
   (`src/components`, and the `src/renderer.tsx` entry) — logic ⊥ UI.
   Evidence: the intended direction UI→lib is asymmetric — `src/components/Button.tsx`
   and `src/renderer.tsx` both import `src/lib/exporter` (2 forward edges), while the
   sole reverse edge is `src/lib/leaky.ts` importing `../components/Button` (1 leak
   inside a 2:1 UI→lib asymmetry). Why/breaks: logic can't run/test headless. Status:
   violated → fix-first or baseline before locking.
3. **[layering/sink · CLEAN]** `src/utils` imports nothing internal (pure sink).
   Evidence: `format.ts` has no internal imports; imported by lib/components/renderer.

Must NOT appear as a proposed rule:
- `i18n ⊥ lib` or `lib ⊥ i18n` — a coincidental symmetric zero (no evidence of an
  intended direction); proposing it would ossify an accident.
- "`src/i18n` imports nothing (a pure sink)" — unlike `utils` (a *used* sink with many
  importers), `i18n/strings.ts` has zero inbound AND zero outbound internal edges. It
  is isolated scaffolding, not a boundary anything relies on; a sink rule here would
  ossify an accident just as the symmetric zero would.

Every item above is a HYPOTHESIS for a human to judge, not an auto-locked rule.
