# Golden output — discovering-architecture-norms on this fixture

A correct run produces a ranked candidate list equivalent to:

1. **[framework-boundary · CLEAN · top-tier]** Renderer (`src/`) must not import
   Electron/Node builtins (electron/fs/path); it goes through the preload bridge.
   Evidence: `electron/main.ts` uses them; `src/renderer.tsx` uses `bridge`, zero
   builtin imports. Why/breaks: process isolation / security — a direct import
   breaks the sandbox. Status: currently clean → lockable now.
2. **[layering · VIOLATED]** `src/lib` must not import `src/components` (logic ⊥ UI).
   Evidence: `src/lib/leaky.ts` imports `../components/Button` (1 leak inside a
   components→lib asymmetry). Why/breaks: logic can't run/test headless. Status:
   violated → fix-first or baseline before locking.
3. **[layering/sink · CLEAN]** `src/utils` imports nothing internal (pure sink).
   Evidence: `format.ts` has no internal imports; imported by lib/components/renderer.

Must NOT appear as a proposed rule:
- `i18n ⊥ lib` or `lib ⊥ i18n` — a coincidental symmetric zero (no evidence of an
  intended direction); proposing it would ossify an accident.

Every item above is a HYPOTHESIS for a human to judge, not an auto-locked rule.
