# Doc lifecycle in the AI-coding era — research-grounded reframe of the two-arm model

**Status:** brainstorm output, not yet a build plan. Refines umbrella §13
(`docs/plans/2026-06-05-decision-conformance-harness-design.md`), the prior SSOT for the
conformance-vs-sedimentation split. Written 2026-06-25.

**TL;DR.** A two-batch deep-research pass (39 adversarially-verified claims, primary
sources) was run to answer: *in the AI-coding era, how do docs become load-bearing instead
of decorative?* The findings **refine, not overturn,** §13. Net changes:

1. **"Decay-on-disuse" is out** as a grounded mechanism. The only empirically-supported,
   tooling-backed staleness trigger is **change-triggered** ("the code a doc is coupled to
   changed → doc suspect").
2. **The two arms collapse toward one shared detection mechanism** (change-triggered),
   distinguished by **role** (feedback/gate vs feedforward/curation), **not** by "opposite
   lifecycles" as §13 claimed.
3. **Context-rot promotes curation to a first-class concern** (new vs §13): unbounded
   accumulation of agent-facing knowledge measurably degrades the agent.
4. **The current CLI already implements a complete lifecycle for the load-bearing core**
   (ratified decisions + code-derivable docs). The one validated, still-unbuilt gap is
   **stale code-element references in hand-written prose docs**.

---

## 1. Why this doc

This session opened intending to build the ②b "sedimentation arm" (the only one of the
three founding intents never touched). Before building, we ran the doc-code research the
project had queued, plus a fresh deep-research pass. The honest conclusion is deflationary:
**②b as a separate, teeth-bearing "decaying-knowledge arm" is largely a mirage** — its
distinguishing mechanism does not survive scrutiny, and what remains is either
un-mechanizable or a context-engineering hygiene concern. The high-value output of the
session is therefore *this reframe* (preventing the project from building an arm on a
disproven model), plus identifying the one narrow, validated cut that is genuinely missing.

## 2. The research (both batches, adversarially verified)

Deep-research harness run `wf_b6de023e-0cd` (6 angles, 27 sources, 124 claims, top-25
verified) + a focused second batch `wf_721f4077-98a` (25 curated angle-4/5/spec-tool
claims, 23/25 confirmed). Full claim pool and prose syntheses preserved in session
scratchpad (`doc-lifecycle-research-synthesis.md`, `doc-lifecycle-full-claim-pool.md`,
`doc-lifecycle-research-batch2.md`). Confidence = 3-vote adversarial verdict.

### 2.1 Change-triggered staleness is the empirical bedrock

- Code and comments **largely do NOT co-evolve**: across 1,500 Java systems / 3,323,198
  commits / ~1.3B AST-level changes, **only 13–20% of code changes trigger a comment
  update** (Wen, Nagy, Bavota, Lanza, ICPC 2019). Staleness is the *default*, not the
  exception. [high]
- Documentation rot is pervasive and measurable: **>25% of the 1000 most popular GitHub
  projects** carry ≥1 outdated code-element reference (arXiv:2307.04291); across 3,000+
  projects most do at some point (arXiv:2212.01479, *Empirical Software Engineering* 2024,
  Tan/Wagner/Treude). [high]
- It is **mechanically detectable and already shipped**: a GitHub Action scans docs for
  code-element references that no longer match source (island parsing, lineage DocRef
  2013); an attention-model detector (MCCL, IEEE TSE 2024) reports F1 82.6% on 1,518
  projects. [high]
- Staleness carries **measurable defect cost** (~1.5× bug-introducing for inconsistent
  changes). [high, but SZZ-based, correlational — see caveats]
- Wen et al. give **per-change-category odds ratios with CIs** — a usable statistical recipe
  for deciding *which* diffs warrant a doc gate rather than gating everything. [high]

### 2.2 Decay-on-disuse and time-triggers have NO grounding

Across both batches, **zero** papers or tools surfaced for time-triggered ("not updated in
6 months → stale") or usage-triggered ("low reference count → demote") staleness. This is
*absence of evidence*, not disproof — but the asymmetry vs §2.1 is stark. Compounding it,
two non-research arguments make decay-on-disuse weak for super-harness specifically:

- **Disuse is a bad proxy for wrongness** (our inference, not cited): rarely-referenced
  knowledge can be perfectly correct and occasionally critical (e.g. "how to recover a
  corrupted `state.yaml`"). Demoting on disuse discards correct, load-bearing knowledge.
- **No usage signal exists here**: decay-on-disuse needs a countable "reference" event.
  super-harness has no citation graph over knowledge notes (unlike systems that derive
  `ref_count` from spec citations), so the signal cannot even be computed.

Karpathy's "lint = demote contradictions / stale / orphans" (the §13 source) bundles three
things; only **orphans** (= change-triggered, cites something deleted) has grounding.

### 2.3 Context-rot makes curation first-class

- LLMs **do not** hold performance steady as context grows — they degrade even on trivial
  retrieval (Chroma *Context Rot*; independently reproduced by NoLiMa ICML 2025, Liu et al.
  "Lost in the Middle" U-shaped >30% mid-context drop, arXiv:2510.05381). [high]
- **Irrelevant/distractor content measurably worsens output**, amplifying with length.
  Focused prompts beat padded-full prompts across all tested models (LongMemEval); gap most
  pronounced for Claude. [high]
- Anthropic frames "context engineering" as **curating and maintaining the optimal token
  set** during inference. **Progressive disclosure** (Agent Skills: name+description
  preloaded → `SKILL.md` body on relevance → bundled files lazily) is the productized,
  context-rot-resistant delivery format. [high]

Implication: a knowledge surface that only *accumulates* becomes the distractor load that
degrades the agent it serves. **Selective surfacing, not maximal retention.**

### 2.4 Don't lean on DIKW / SECI as settled science

DIKW has a central logical error and (per Frické 2009) "should be abandoned" — though the
strong "abandon" prescription was itself refuted 0-3 in verification as one author's
contested view; the *logical-error* critique stands [high]. SECI's empirical basis is
"highly unsatisfactory" (Gourlay) [high]. Ground the design in the operational SE evidence
(§2.1), **not** in knowledge-pyramid or tacit→explicit-conversion theory.

## 3. The reframe of §13

§13 split two arms by "opposite lifecycles: frozen-on-ratify vs decay-on-disuse." With
decay-on-disuse removed (§2.2), that justification fails. The corrected model:

**There is one staleness-detection mechanism — change-triggered ("the thing a doc is
coupled to moved → doc suspect"). The arms differ by ROLE, not lifecycle:**

| | CONFORMANCE | SEDIMENTATION |
|---|---|---|
| Direction | feedback — checked *against* code (sensor) | feedforward — fed *into* the agent (guide) |
| Staleness signal | change-triggered (shared mechanism) | change-triggered (same mechanism, aimed at knowledge artifacts) |
| Response on staleness | **block merge** | **prune / refresh the injected context** (context-rot) |
| Truth condition | "does code match it?" | "is it still useful + accurate to the agent?" |

Consequences:

- The earlier "(甲) anchor-drift = real ②b vs (乙) orphan-lint = conformance-extended" fork
  is a **false binary**: the validated decay mechanism is conformance-family either way, so
  ②b's only genuine novelty would be the *feedforward role + curation*, not a new decay test.
- **Curation under context-rot** (§2.3) is the one genuinely new, non-conformance concern —
  but it is context-engineering hygiene, not a gate, and does not by itself justify a whole
  teeth-bearing arm.
- **Honest residue** (irreducible): (a) **non-code-coupled knowledge** — pure
  craft/preference ("we prefer style X") couples to no code symbol, so change-triggering
  cannot fire; it decays via taste/team change, which has no mechanical signal → human /
  LLM-judge advisory at most. (b) **Behavioral/semantic drift** — the symbol still resolves
  but its meaning changed; all known detectors are syntactic → human floor.

**Net: §13's two-arm taxonomy stands as a description of two roles, but "sedimentation" is
NOT a separate buildable arm with its own decay mechanism. It collapses into (i) the shared
change-triggered detector applied to feedforward artifacts and (ii) a curation concern.**

## 4. Coverage map — what the current CLI already does

For the artifact class it was designed around (ratified decisions + code-derivable docs),
the CLI already implements a **complete, end-to-end lifecycle**:

| Lifecycle stage | Built command |
|---|---|
| Birth | `decision new` (proposed) |
| Freeze / ratify | `decision ratify` (`ratified_text_hash` + bite-test) |
| Drift / consistency check | `decision check` (dangling / text-lock / tier-1 executable); `doc check` (regen-diff) |
| Change-triggered re-review | tier-2 `suspect` + `decision check --gate-reconcile` |
| Supersede | `decision supersede` (append-only, old kept + marked) |
| Retire | `decision retire` (tombstone) |
| Regenerate | `doc check --fix` |

This is real doc lifecycle management, and per §2.1 it covers the empirically load-bearing,
mechanizable core. **What it does NOT cover:**

1. **Stale code-element references in hand-written prose docs.** `.harness/source-paths.yaml`
   explicitly excludes `docs/**`; the anchor scanner only finds `@decision:` sentinels in
   *source*. A renamed/deleted symbol mentioned in `getting-started.md` is invisible. This
   is the **#1 empirically-grounded doc-rot mechanism (§2.1) and it is unbuilt.**
2. **Curation / pruning** for context-rot — unbuilt.
3. **Non-code-coupled knowledge** (gotchas/preferences) — no lifecycle hooks.
4. **Behavioral/semantic drift** — the human floor; not mechanizable.

## 5. The converged first cut (two layers)

Value note: this serves **adopters** of the tool, not this repo. super-harness is an
open-source governance layer; its own docs are small and maintained, but the empirical
target is everyone who runs it. The §2.1 evidence (>25% of popular repos carry dead code
references) IS the adopter felt-pain — "our repo doesn't hurt" is not a reason to skip it.

Brainstorm (2026-06-25) converged on a two-layer split, **with the hard gate retained**.
Both layers honor the iron rule: the harness never runs the LLM itself.

### 5.1 B-layer — dead code-reference gate (mechanical, no LLM, no watch-link)

**What it does.** Scan committed docs for code-symbol references that no longer resolve
against current source — the §2.1-validated mechanism. **No declared watch-link is needed:
the symbol name in the prose IS the link** (the "watch what?" problem dissolves for this
mechanism; see §6 for why the watch-link framing was dropped).

**Precision policy (the gate-worthiness crux).** Prose mentions symbols loosely, so raw
island-parsing over-fires. Precision-first:
- **Only backtick code-spans count as high-confidence → BLOCK.** A `` `_format_rows` `` in a
  doc that no longer resolves fails the gate.
- **Bare qualified names (`core.reducer.fold`) → WARN only.** Promote to block later only if
  validation shows acceptable precision.
- **Symbol existence is resolved fail-open toward no-false-positive:** flag only symbols that
  precisely match a *deleted/renamed* identifier; anything not precisely matchable is NOT
  reported. Cheapest honest resolution (git-grep / lightweight index) TBD at plan time;
  default biases to silence over noise.

**Where the teeth bite (one predicate, graded exit codes — mirrors `decision check
--gate-reconcile`):**
- `done` → **warn** (early heads-up so the agent fixes in-loop).
- `review approve --reviewer code-reviewer` emit → **BLOCK** (primary gate; cannot approve
  while a backtick reference is dead). ← placement decision, 2026-06-25.
- CI merge gate → **BLOCK** (agent-agnostic cold-floor backstop; stays even though ③ is the
  primary gate, because pure-vibe sessions skip ③ entirely).

**Reuse vs new (TBD at plan time):** extend `anchor_scanner` (un-exclude `docs/**`, add a
doc→symbol reference mode) vs a new `doc_check` check class. Must respect `source-paths`
scope so it does not fire on intentionally-archival docs (`docs/plans/` history).

### 5.2 C-layer — semantic doc-impact via the ①b forced review (no new mechanism)

For staleness that is NOT a dead-reference — "the symbol still resolves but the doc's claim
about its behavior is now false," and whole-project/AI-generated guidance (AGENTS.md) — the
judgment is semantic. The harness does not run an LLM; it **reuses ①b**: at the code-review
boundary the forced verdict's checklist carries a "doc impact" item, a bare/incomplete
verdict is rejected at emit (existing ①b teeth), the semantic judgment stays the agent's,
and the harness verifies the verdict's shape/freshness + snapshots it into the attestation.
Pure-vibe fallback: the P3 headless reviewer in CI, or nothing.

### 5.3 When/how it fires in one vibe-coding session

Worked example — vibe feature "add `--json` to `decision list`," during which the agent
renames `_format_rows` → `_render` (a doc references the old name in a backtick span):

| Moment | Mechanism | How it bites | Needs lifecycle? |
|---|---|---|---|
| edit-time | (deferred soft reminder) | PreToolUse nudge, fail-open, CC-only | interactive only; deferred |
| `done` | B-layer (warn) | scan flags the now-dead `` `_format_rows` `` on stderr; agent fixes in-loop | interactive (agent invokes `done`) |
| `review approve` (③) | B-layer (**block**) + C-layer (①b) | dead backtick ref blocks the approve emit; the forced verdict must dispose "doc impact" | interactive review boundary, or CI P3 |
| CI merge gate (④) | B-layer (**block**) + `doc check` | dead ref → exit 2; derivable docs out of sync → exit 2 | **no** — cold floor, unskippable |
| `on-merge` | trail | verdict/attestation already committed; auditable | — |

Honest consequence: the purer the vibe (no lifecycle), the more it degrades to **only the CI
cold floor (④)** — B-layer dead-ref gate + derivable `doc check`. The semantic C-layer needs
a review boundary; without one it happens via the P3 CI reviewer or not at all. Consistent
with "real-time gate only for hook-having agents; agent-agnostic = CI cold floor."

## 6. Explicitly NOT building (anti-gilding record)

- **No separate sedimentation arm** — the model collapsed (§3).
- **No "watch-link" (doc declares the files it tracks)** — pressure-tested and dropped:
  authoring burden + drift, and whole-project docs watch everything → permanently suspect →
  noise (and, for agents, context-rot distractor load). The validated mechanism (§5.1) needs
  no watch-link at all.
- **No "watch the decision set" (doc tracks a set of ratified decisions)** — pressure-tested
  and dropped: only 9 decisions exist and a 211-line ARCHITECTURE.md references them 7 times,
  so it covers a thin decision-backed sliver (mass false-negatives), is orthogonal to the
  validated code-reference rot, and still drifts.
- **No decay-on-disuse / `ref_count` / `last_referenced`** — no grounding, bad proxy, no
  usage signal exists here (§2.2).
- **No time-triggered "6-month" staleness gate** — heuristic without evidence (§2.2).
- **No generic untargeted "docs may be stale" reminder** — for agents an untargeted nudge is
  a context-rot distractor (§2.3), worse than useless.
- **No behavioral-drift *detector*** — that residue is routed to C-layer (§5.2), human/agent
  judgment, never a harness-run LLM.
- **No parallel knowledge base** — §13.4 stands, reinforced by context-rot (§2.3).

## 7. Honest caveats

- Benchmark numbers (MCCL F1 82.6%, doc-rot percentages) are author-reported, not
  independently reproduced.
- The defect-cost link is SZZ-based and correlational, not causal.
- Context-rot's vendor framing (Chroma sells retrieval) is motivated; the *finding* survives
  on independent peer-reviewed reproduction, the *framing* should be discounted.
- All validated detection is **syntactic**; semantic/behavioral drift is uncovered. Do not
  overclaim conformance coverage.
- KM critiques are "X argued Y" attributions; they justify not over-relying on DIKW/SECI,
  not declaring them dead.
- "No evidence found" for time/disuse triggers reflects this research pass, not a proof of
  nonexistence.
