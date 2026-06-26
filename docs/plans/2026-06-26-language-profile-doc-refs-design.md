# Language profile for doc-refs identifier recognition — design

> Portability axis A (decouple super-harness from Python *as the governed
> project's language*). Grounded by a live n=2 reconnaissance, not speculation:
> a full lifecycle run on a TypeScript repo and a Ruby repo (throwaway, 2026-06-26)
> showed the harness is **already language-agnostic for the governed project
> EXCEPT one knob** — the dead-doc-reference gate's code-identifier recognizer
> (`core/doc_refs.py`). This slice externalizes that single knob. Written
> 2026-06-26. See memory `project-portability-decoupling-direction`.

## 1. Why

The 2026-06-25 audit and the 2026-06-26 live recon converge on one finding: the
runtime harness has **no Python coupling to the governed project** except the
code-shape heuristic in `core/doc_refs.py`. Everything else is already neutral:

- `verification_defaults.yaml` ships `checks: []` — there is **no** baked
  ruff/mypy/pytest; the user/adapter authors their own check commands.
- `source_scope` defaults to `["**/*"]` minus `["docs/**"]` — scans all files,
  assumes no extension.
- `_CANONICAL_PATHS` (gitignore) lists only super-harness's **own** artifacts
  (`.harness/`, `.claude/`, `.codex/`) — no `__pycache__`/`.venv`.
- anchor/sentinel scanning is glob-driven (config-supplied).

**Live n=2 (recon, throwaway repos, no super-harness code changed):**
- **TypeScript/Node:** full lifecycle (init → change → plan-ready scoping `.ts` →
  approve → impl-start → `verify` running `node --test` → done → review → attest
  verify) ran clean. `doc refs` correctly flagged a dead `` `removedHelper` `` and
  passed live `` `addNumbers` `` / `` `PaymentProcessor` `` / `` `chargeCard` ``.
  → C-family languages work **zero-config** today (TS/Go/Rust/Java/JS/C#/C++/Swift/
  Kotlin/PHP all use `[A-Za-z_][A-Za-z0-9_]*` identifiers with snake/camel shape).
- **Ruby:** `doc refs` correctly flagged dead `` `removed_helper` `` (snake_case)
  but **MISSED** dead `` `deleted?` `` and could not see live `` `valid?` `` /
  `` `charge!` ``. Root cause: `?`/`!` suffixes (and `@`/`$` prefixes) break the
  identifier regex in **both** source tokenization and doc-span recognition.

So the one genuine governed-project language coupling is the doc-refs
identifier recognizer. This slice makes it a per-workspace knob, default =
today's C-family behavior. Nothing else needs changing for portability.

### Current mechanism (the thing being tuned)

`core/doc_refs.py` uses three module-level regexes:
- `_BACKTICK_RE = \`([^\`\n]+)\`` — extracts backtick spans from prose (NOT tuned).
- `_IDENT_RE = ^[A-Za-z_][A-Za-z0-9_]*$` — a doc backtick span "looks like a symbol"
  only if (after stripping a trailing `()`) it fully matches this. The check lives in
  `looks_like_symbol`, called by `extract_backtick_symbols`.
- `_HAS_INTERNAL_UPPER_RE = [a-z][A-Z]|[A-Z][a-z]` — the precision filter:
  `looks_like_symbol` requires the candidate to ALSO contain `_` or internal upper
  (so plain English words in backticks like `` `note` `` are NOT treated as code).
- `_TOKEN_RE = \b[A-Za-z_][A-Za-z0-9_]*\b` — tokenizes every source-scope file
  into the "source identifier set" a doc span is checked against.

`_IDENT_RE` and `_TOKEN_RE` are the same character-class in anchored vs
word-boundary form. They are the language coupling.

## 2. Scope

**IN:**
- A per-workspace, **optional** language profile that supplies ONE setting: the
  code-**identifier pattern** (the character class for a code symbol). It drives
  BOTH the source tokenizer and the doc-span recognizer so they stay consistent.
- Default (file absent / corrupt / key missing / invalid regex) = exactly today's
  C-family pattern. Zero-config for C-family; fail-safe to current behavior.
- A small loader (`core/language_profile.py`) mirroring `core/source_scope.py`'s
  tolerant load-with-defaults shape.
- Extend the precision filter so a span containing identifier "decoration"
  (non-`[A-Za-z]` chars admitted by the pattern, e.g. `?` `!` `@` `$`) counts as a
  code signal — so `valid?` qualifies as code-shaped while plain `note` still does
  not. (This is a small, language-neutral rule, NOT a configurable knob.)
- Docs: document the knob with two copy-paste examples (C-family default shown for
  reference; Ruby). A Ruby fixture test proving the knob works (the n=2 non-C-family
  validation the design is graded on).

**Explicitly OUT (recorded non-goals):**
- **A "language profile framework."** There is exactly ONE consumer (doc_refs) and
  ONE setting. No plugin system, no per-language registry, no auto-detection by file
  extension (auto-detection is the framework we are explicitly avoiding — YAGNI; the
  memory says "别建大框架"). Selection is explicit opt-in via the config file.
- Tuning anything other than doc_refs — nothing else is coupled (proven by the TS
  n=2). `verification`/`scope`/`gitignore`/anchor-scan stay as-is.
- Making the `code-shape` precision heuristic itself configurable (snake/camel + the
  new decoration rule are language-neutral enough; only the identifier *pattern*
  varies by language).
- Decoupling super-harness's **own** runtime/distribution from Python (it stays a
  Python/pipx-installed tool, like `ruff`/`gh`). That is a separate axis; a non-Python
  governed project still installs the tool via pipx (Python on the CI runner only).

## 3. Design

### 3.1 The config file

New optional `.harness/language.yaml`:

```yaml
doc_refs:
  identifier_pattern: '[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?'   # e.g. Ruby
```

- Single key under `doc_refs:` — `identifier_pattern` (a Python `re` character-class/
  pattern for ONE code identifier, unanchored body; the loader anchors it for the
  doc-span check and word-boundary-wraps it for tokenization).
- Default when the file is absent, unreadable, non-dict, missing the key, or the
  pattern fails to compile: `IDENTIFIER_PATTERN_DEFAULT = r"[A-Za-z_][A-Za-z0-9_]*"`
  (today's C-family behavior, byte-for-byte).

### 3.2 The loader — `core/language_profile.py`

Mirror `core/source_scope.py` exactly (tolerant, defaults-on-anything-wrong):

```python
IDENTIFIER_PATTERN_DEFAULT = r"[A-Za-z_][A-Za-z0-9_]*"

def load_identifier_pattern(workspace_root: Path) -> str:
    """Return the doc_refs identifier pattern for this workspace, or the C-family
    default. Tolerant: a missing/corrupt/invalid config NEVER bricks doc_refs."""
    f = workspace_root / ".harness" / "language.yaml"
    if not f.is_file():
        return IDENTIFIER_PATTERN_DEFAULT
    try:
        data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError, UnicodeDecodeError):
        return IDENTIFIER_PATTERN_DEFAULT
    dr = data.get("doc_refs") if isinstance(data, dict) else None
    pat = dr.get("identifier_pattern") if isinstance(dr, dict) else None
    if not isinstance(pat, str) or not pat:
        return IDENTIFIER_PATTERN_DEFAULT
    try:
        re.compile(pat)                      # reject an invalid regex
    except re.error:
        return IDENTIFIER_PATTERN_DEFAULT    # fail-safe; doc_refs stays fail-open
    return pat
```

(Exact error-family catch tuple `(yaml.YAMLError, OSError, UnicodeDecodeError)` per
the repo's recurring lesson; matches `source_scope.load_source_scope`.)

### 3.3 Threading into `core/doc_refs.py`

**Load inside `scan_doc_refs(root)`, NOT at the CLI.** `scan_doc_refs(workspace_root)`
is the single public entry, and it has **three** callers — `cli/doc.py` (`doc refs`),
`cli/review.py` (the ③ code-review approve gate), and `cli/done.py` (`_warn_dead_refs`).
Threading a pattern parameter from each CLI would touch all three and risk drift.
Instead `scan_doc_refs` calls `load_identifier_pattern(root)` itself (it already
receives `root`), compiles the derived regexes once, and passes them down to its
internal helpers (`extract_backtick_symbols`, `collect_source_identifiers`,
`looks_like_symbol`). **All three call sites stay byte-for-byte unchanged.**

The two tuned regexes become **derived from the loaded pattern**, compiled once per
invocation (not at import — the pattern is per-workspace):

- `ident_re = re.compile(rf"^{pattern}$")` (replaces `_IDENT_RE`). For the default
  pattern this is `^[A-Za-z_][A-Za-z0-9_]*$` — identical to today's `_IDENT_RE`.
- `token_re = re.compile(rf"(?<!\w){pattern}(?!\w)")` (replaces `_TOKEN_RE`). BOTH
  lookarounds are on `\w` (`[A-Za-z0-9_]`) — NOT `?!@$`. This is the load-bearing
  correction (see §4): the old `\b…\b` is exactly `(?<!\w)…(?!\w)`. The trailing
  `(?!\w)` is required for full equivalence (not just ASCII): without it, an ASCII
  identifier glued to a **Unicode** word char (`método`, `foo_é`) would mis-tokenize a
  truncated fragment where the old Unicode-aware trailing `\b` matched nothing. With
  both lookarounds the default is provably equivalent to `\b[A-Za-z_][A-Za-z0-9_]*\b`
  on every case (`@property`→`property`, `$element`→`element`, `a?b`→`a`,`b`,
  `123abc`→nothing, `método`/`foo_é`→nothing — all match today). For Ruby it captures
  `@balance` and `valid?` whole (the trailing `(?!\w)` holds because `?`/`!` are
  followed by a non-word char in real source).

The real precision function is **`looks_like_symbol`** (NOT `looks_like_code`); it is
called by `extract_backtick_symbols` (which strips a trailing `()`). It keeps
`_HAS_INTERNAL_UPPER_RE` and gains the decoration signal, taking the compiled
`ident_re` (no `pattern_chars` param):

```python
def looks_like_symbol(span: str, ident_re: re.Pattern[str]) -> bool:
    candidate = span[:-2] if span.endswith("()") else span
    if not ident_re.match(candidate):
        return False
    has_snake_or_camel = "_" in candidate or bool(_HAS_INTERNAL_UPPER_RE.search(candidate))
    has_decoration = any(not c.isalnum() and c != "_" for c in candidate)  # ? ! @ $ ...
    return has_snake_or_camel or has_decoration
```

`collect_source_identifiers` uses `token_re` instead of `_TOKEN_RE`.

**Default-equivalence invariant:** with no `language.yaml` (default pattern), every
doc_refs output must be byte-for-byte identical to today. Regression-locked by a test
whose fixtures **deliberately include** identifiers adjacent to `@` / `$` / `?` / `!`
and a digit-prefixed run (e.g. `@decorator`, `$el`, `a?b`, `123abc`) — the cases where
a naive boundary would silently regress (per §4); without those fixtures the
regression could ship green.

### 3.4 Why one pattern, not three knobs

`_IDENT_RE` and `_TOKEN_RE` are the same character class; exposing them separately
would let a user desync them (a token set that can't match its own doc spans). One
`identifier_pattern` keeps source tokenization and doc-span recognition provably
consistent. The precision heuristic is language-neutral, so it is not a knob.

## 4. Honest limits / non-goals

- **Only doc_refs is language-tuned** — because only doc_refs is coupled (TS n=2
  proved the rest neutral). If a future audit finds another coupling, it gets its own
  treatment; this slice does not pre-build for hypotheticals.
- **The default still has the precision/recall tradeoff of today.** A dead reference
  to a bare lowercase word (`` `parse` `` with no `_`/camel/decoration) is NOT flagged
  in ANY language — deliberate (avoids flagging English words). The knob changes which
  identifiers are *admitted*, not the precision philosophy.
- **No auto-detection.** A non-C-family repo must opt in via `language.yaml`. This is
  the YAGNI line: auto-detection is the framework we refuse to build for one consumer.
- **super-harness itself stays Python/pipx.** A non-Python governed project still needs
  Python on its CI runner to `pipx install super-harness`. Decoupling the tool's own
  distribution is a separate axis, not this slice.
- **`doc refs --gate` exit-code observation (out of scope, flagged for follow-up):**
  during recon, `doc refs --gate` printed `DEAD-REF` but exited 0. This is unrelated to
  portability (same on Python) — recorded as a separate OPEN-ITEM to investigate, NOT
  fixed here.

## 5. Test plan

TDD throughout. Verify with `PATH="$(pwd)/.venv/bin:$PATH"`.

- **Loader unit tests** (`core/language_profile.py`): absent file → default; valid
  Ruby pattern → returned; corrupt YAML → default; non-dict / missing key → default;
  invalid regex string → default (no raise).
- **doc_refs default-equivalence (regression lock):** existing doc_refs fixtures with
  NO `language.yaml` produce identical results (the C-family behavior is unchanged).
  The fixtures MUST include source/doc text with identifiers adjacent to `@` / `$` /
  `?` / `!` and a digit-prefixed run (`@decorator`, `$el`, `a?b`, `123abc`) — these are
  exactly the tokens a naive boundary would drop, so without them the regression ships
  green (§3.3 / §4). Assert the derived default `token_re` returns the same set as the
  old `_TOKEN_RE` on these strings.
- **doc_refs Ruby fixture (the n=2 non-C-family proof):** a source file with
  `def valid?` / `def charge!` / `def total_amount` + a doc citing `` `valid?` ``,
  `` `charge!` ``, `` `total_amount` `` (live) and `` `deleted?` `` (dead):
  - with the DEFAULT pattern → `deleted?` is MISSED (documents today's gap),
  - with the Ruby `identifier_pattern` → `deleted?` is FLAGGED as dead AND
    `valid?`/`charge!`/`total_amount` resolve as live (no false positive).
- **`looks_like_symbol` decoration signal:** `valid?` (under Ruby pattern) → code;
  plain `note` → not code (precision preserved); `total_amount` → code; `addNumbers`
  → code.
- **CLI integration:** `super-harness doc refs` in a workspace with a Ruby
  `language.yaml` flags `deleted?`; the C-family default path stays green.
- **Self-host (Python, profile-1):** super-harness's own repo has no `language.yaml`
  → default path → `doc refs` / `doc check` behavior unchanged (this very PR must pass
  its own doc gates with zero new config).

The live TS + Ruby throwaway recon is the design's grounding evidence; the Ruby
fixture test makes the non-C-family proof permanent and CI-enforced.

## 6. Files touched

- `src/super_harness/core/language_profile.py` — NEW loader (mirror `source_scope.py`;
  adds `import re` + a `re.compile()` validation block, the one addition over the mirror).
- `src/super_harness/core/doc_refs.py` — derive `ident_re`/`token_re` from the loaded
  pattern inside `scan_doc_refs(root)` (which calls `load_identifier_pattern(root)`);
  add the decoration signal to `looks_like_symbol`; pass the compiled regexes to
  `extract_backtick_symbols` / `collect_source_identifiers`.
- (NO change to `cli/doc.py`, `cli/review.py`, `cli/done.py` — all three call
  `scan_doc_refs(root)` unchanged; the pattern is loaded internally.)
- `docs/` — document `.harness/language.yaml` (`doc_refs.identifier_pattern`) with
  C-family-default + Ruby examples (likely in the doc-refs / getting-started reference;
  exact home pinned at writing-plans time, following the repo's doc rules).
- tests — loader unit tests; doc_refs default-equivalence; Ruby fixture; decoration
  signal; CLI integration.
- this design doc + the implementation plan (in scope for the self-host PR).
- `private/OPEN-ITEMS.md` — record the `doc refs --gate` exit-code observation as a
  separate follow-up.

## 7. Open questions — none blocking

- Exact doc home for the knob reference (getting-started vs a doc-refs reference page)
  — pinned at writing-plans against the repo's doc rules; does not affect the design.

(The tokenizer boundary is now pinned in §3.3: `(?<!\w){pattern}`, with the
default-equivalence cases enumerated and locked by the §5 regression test. It is no
longer an open question.)
