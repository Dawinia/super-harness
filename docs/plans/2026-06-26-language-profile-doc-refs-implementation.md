# Language Profile for doc-refs Identifier Recognition — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the dead-doc-reference gate's code-identifier recognition tunable per-workspace (one knob: `.harness/language.yaml` → `doc_refs.identifier_pattern`), defaulting byte-for-byte to today's C-family behavior, so non-C-family governed projects (proven: Ruby `?`/`!` methods) get correct dead-ref detection.

**Architecture:** A new tolerant loader `core/language_profile.py` (mirrors `core/source_scope.py`) returns the identifier pattern (default = C-family). `core/doc_refs.py`'s `scan_doc_refs(root)` loads it internally and derives the doc-span matcher (`ident_re = ^{pattern}$`) and the source tokenizer (`token_re = (?<!\w){pattern}`), passing the compiled regexes down to its helpers. All three `scan_doc_refs` callers (`cli/doc.py`, `cli/review.py`, `cli/done.py`) stay unchanged. The precision filter `looks_like_symbol` gains a language-neutral "decoration" signal (a `?`/`!`/`@`/`$` in an admitted identifier counts as code) so the wider patterns are useful.

**Tech Stack:** Python 3.10+, `re`, `yaml`, pytest, click. Verify with `V=PATH="$(pwd)/.venv/bin:$PATH"`.

Design SSOT: `docs/plans/2026-06-26-language-profile-doc-refs-design.md`.

---

## File Structure

- `src/super_harness/core/language_profile.py` — NEW. Tolerant loader `load_identifier_pattern(root) -> str`; `IDENTIFIER_PATTERN_DEFAULT`. One responsibility: read `.harness/language.yaml`, return a validated pattern or the default.
- `src/super_harness/core/doc_refs.py` — MODIFY. Thread a per-workspace pattern: `looks_like_symbol`/`extract_backtick_symbols` gain an optional `ident_re` param (default = module `_IDENT_RE`, preserving every existing caller); `collect_source_identifiers` gains optional `token_re` (default `_TOKEN_RE`); `looks_like_symbol` gains the decoration signal; `scan_doc_refs` loads the pattern and derives + passes the regexes.
- `docs/getting-started.md` — MODIFY. Document `.harness/language.yaml` (`doc_refs.identifier_pattern`) with the C-family default explanation + a Ruby example.
- `private/OPEN-ITEMS.md` — MODIFY (gitignored, local). Record the `doc refs --gate` exit-0 observation as a separate follow-up.
- Tests: `tests/unit/core/test_language_profile.py` (NEW); `tests/unit/core/test_doc_refs.py` (MODIFY — default-equivalence regression + decoration + Ruby fixture).
- `docs/plans/2026-06-26-language-profile-doc-refs-{design,implementation}.md` — in the self-host PR scope.

Verification shorthand: `V=PATH="$(pwd)/.venv/bin:$PATH"`. Every commit message ends with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`. Strict `git add <files>`, never `-A`.

---

## Task 1: The language-profile loader (`core/language_profile.py`)

**Files:**
- Create: `src/super_harness/core/language_profile.py`
- Test: `tests/unit/core/test_language_profile.py`

- [ ] **Step 1: Write the failing tests** — create `tests/unit/core/test_language_profile.py`:

```python
from pathlib import Path

from super_harness.core.language_profile import (
    IDENTIFIER_PATTERN_DEFAULT,
    load_identifier_pattern,
)


def _write(root: Path, text: str) -> None:
    (root / ".harness").mkdir(parents=True, exist_ok=True)
    (root / ".harness" / "language.yaml").write_text(text, encoding="utf-8")


def test_absent_file_returns_default(tmp_path):
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_valid_ruby_pattern_returned(tmp_path):
    _write(tmp_path, "doc_refs:\n  identifier_pattern: '[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?'\n")
    assert load_identifier_pattern(tmp_path) == r"[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?"


def test_corrupt_yaml_returns_default(tmp_path):
    _write(tmp_path, "doc_refs: [this is: not: valid")
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_non_dict_top_level_returns_default(tmp_path):
    _write(tmp_path, "- just\n- a\n- list\n")
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_missing_key_returns_default(tmp_path):
    _write(tmp_path, "doc_refs:\n  something_else: 1\n")
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_empty_string_pattern_returns_default(tmp_path):
    _write(tmp_path, "doc_refs:\n  identifier_pattern: ''\n")
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_invalid_regex_returns_default(tmp_path):
    _write(tmp_path, "doc_refs:\n  identifier_pattern: '[unterminated'\n")
    assert load_identifier_pattern(tmp_path) == IDENTIFIER_PATTERN_DEFAULT


def test_default_is_c_family():
    assert IDENTIFIER_PATTERN_DEFAULT == r"[A-Za-z_][A-Za-z0-9_]*"
```

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/core/test_language_profile.py -v`
Expected: FAIL (module does not exist).

- [ ] **Step 3: Create the loader** — `src/super_harness/core/language_profile.py`:

```python
"""Per-workspace language tuning for the dead-doc-reference gate (design 2026-06-26).

Today the ONLY governed-project language coupling in super-harness is the
code-identifier recognizer in ``core/doc_refs.py``. This loader externalizes its
identifier pattern so a non-C-family project (e.g. Ruby ``?``/``!`` methods) can tune
it via ``.harness/language.yaml``:

    doc_refs:
      identifier_pattern: '[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?'

Tolerant, fail-safe to the C-family default (mirrors ``core/source_scope.py``): a
missing / unreadable / non-dict / missing-key / empty / un-compilable pattern all
return the default. doc_refs is fail-open, so a bad config never bricks the gate.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

# Today's behavior, byte-for-byte. The doc-span matcher anchors this (``^{p}$``);
# the source tokenizer wraps it (``(?<!\w){p}``). See doc_refs + design §3.3.
IDENTIFIER_PATTERN_DEFAULT = r"[A-Za-z_][A-Za-z0-9_]*"


def language_file(workspace_root: Path) -> Path:
    return workspace_root / ".harness" / "language.yaml"


def load_identifier_pattern(workspace_root: Path) -> str:
    """Return the doc_refs identifier pattern for this workspace, or the C-family
    default. NEVER raises: any problem falls back to the default."""
    f = language_file(workspace_root)
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
        re.compile(pat)
    except re.error:
        return IDENTIFIER_PATTERN_DEFAULT
    return pat
```

- [ ] **Step 4: Run to verify pass**

Run: `$V python -m pytest tests/unit/core/test_language_profile.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Lint + commit**

Run: `$V ruff check src tests && $V mypy src`
```bash
git add src/super_harness/core/language_profile.py tests/unit/core/test_language_profile.py
git commit -m "feat: language_profile loader for the doc-refs identifier pattern" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Thread the pattern through doc_refs (default-equivalent)

**Files:**
- Modify: `src/super_harness/core/doc_refs.py`
- Test: `tests/unit/core/test_doc_refs.py`

This task MUST NOT change any output for the default pattern. The new params default to today's module regexes; `scan_doc_refs` derives the regexes from the loaded pattern.

- [ ] **Step 1: Write the failing tests** — append to `tests/unit/core/test_doc_refs.py`:

```python
import re

from super_harness.core.doc_refs import (
    _TOKEN_RE,
    collect_source_identifiers,
    extract_backtick_symbols,
    looks_like_symbol,
)
from super_harness.core.language_profile import IDENTIFIER_PATTERN_DEFAULT


def test_default_tokenizer_equals_old_token_re():
    """The derived default token_re must be byte-for-byte equivalent to the old
    `\\b[A-Za-z_][A-Za-z0-9_]*\\b`, including @/$/?/! adjacency and digit prefixes —
    the cases a naive boundary would silently drop (design §3.3/§4)."""
    token_re = re.compile(rf"(?<!\w){IDENTIFIER_PATTERN_DEFAULT}")
    for s in ["@property", "$element jQuery", "a?b:c", "foo!bar", "123abc",
              "var2name x", "self.method_name", "addNumbers PaymentProcessor",
              "@decorator\ndef f", "__init__"]:
        assert token_re.findall(s) == _TOKEN_RE.findall(s), s


def test_decoration_signal_under_ruby_pattern():
    ruby = re.compile(r"^[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?$")
    assert looks_like_symbol("valid?", ident_re=ruby) is True     # ? is decoration
    assert looks_like_symbol("charge!", ident_re=ruby) is True
    assert looks_like_symbol("total_amount", ident_re=ruby) is True  # snake
    assert looks_like_symbol("note", ident_re=ruby) is False      # plain word, no signal


def test_default_decoration_unchanged():
    # With the default ident_re, behavior is exactly today's snake/camel rule.
    assert looks_like_symbol("addNumbers") is True
    assert looks_like_symbol("snake_case") is True
    assert looks_like_symbol("note") is False
    assert looks_like_symbol("valid?") is False  # ? not admitted by default ident_re


def test_extract_backtick_symbols_accepts_ident_re():
    ruby = re.compile(r"^[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?$")
    out = extract_backtick_symbols("call `valid?` then `note`.", ident_re=ruby)
    assert ("valid?", 1) in out
    assert all(sym != "note" for sym, _ in out)


def test_collect_source_identifiers_accepts_token_re(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "a.rb").write_text("def valid?\nend\n", encoding="utf-8")
    ruby_tok = re.compile(r"(?<!\w)[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?")
    idents = collect_source_identifiers(
        tmp_path, include=["**/*"], exclude=["docs/**"], token_re=ruby_tok
    )
    assert "valid?" in idents
    default_idents = collect_source_identifiers(tmp_path, include=["**/*"], exclude=["docs/**"])
    assert "valid?" not in default_idents and "valid" in default_idents
```

- [ ] **Step 2: Run to verify fail**

Run: `$V python -m pytest tests/unit/core/test_doc_refs.py -k "tokenizer or decoration or accepts" -v`
Expected: FAIL (`looks_like_symbol`/`extract_backtick_symbols` take no `ident_re`; `collect_source_identifiers` takes no `token_re`).

- [ ] **Step 3: Modify `core/doc_refs.py`.** Add the import near the other core imports (after line 33):

```python
from super_harness.core.language_profile import load_identifier_pattern
```

Replace `looks_like_symbol` (lines 83-93) with the param + decoration signal:

```python
def looks_like_symbol(span: str, ident_re: re.Pattern[str] = _IDENT_RE) -> bool:
    """True if `span` is a single code identifier that looks like code (precision crux).

    Accepts a single identifier (optionally with a trailing `()`) admitted by
    `ident_re` that EITHER contains `_` / a camelCase boundary OR carries identifier
    "decoration" (a non-`[A-Za-z0-9_]` char the pattern admits, e.g. `?` `!` `@` `$`).
    With the default `ident_re` no decoration is possible, so behavior is unchanged.
    Rejects prose words, flags, dotted names, paths, and multi-token spans.
    """
    candidate = span[:-2] if span.endswith("()") else span
    if not ident_re.match(candidate):
        return False
    has_snake_or_camel = "_" in candidate or bool(_HAS_INTERNAL_UPPER_RE.search(candidate))
    has_decoration = any(not c.isalnum() and c != "_" for c in candidate)
    return has_snake_or_camel or has_decoration
```

Replace `extract_backtick_symbols` (lines 96-108) to thread `ident_re`:

```python
def extract_backtick_symbols(
    text: str, ident_re: re.Pattern[str] = _IDENT_RE
) -> list[tuple[str, int]]:
    """Return [(symbol, 1-based-line)] for backtick spans that pass `looks_like_symbol`.

    A trailing `()` is stripped from the recorded symbol so resolution matches the
    bare identifier. Order preserved; duplicates kept (caller may dedupe per file).
    """
    out: list[tuple[str, int]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for m in _BACKTICK_RE.finditer(line):
            span = m.group(1).strip()
            if looks_like_symbol(span, ident_re):
                out.append((span[:-2] if span.endswith("()") else span, lineno))
    return out
```

Change `collect_source_identifiers` signature (line 115-117) and the tokenize line (130) to accept `token_re`:

```python
def collect_source_identifiers(
    root: Path, *, include: list[str], exclude: list[str],
    token_re: re.Pattern[str] = _TOKEN_RE,
) -> set[str]:
    """Every identifier token present in any source-scope file. Binary/unreadable skipped."""
    idents: set[str] = set()
    for f in _list_files(root):
        if not f.is_file():
            continue
        rel = f.relative_to(root)
        if not _in_scope(rel, include, exclude):
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        idents.update(token_re.findall(text))
    return idents
```

In `scan_doc_refs` (after `doc_include, doc_exclude = load_doc_scope(...)`, ~line 153), derive the regexes from the loaded pattern and pass them down:

```python
    pattern = load_identifier_pattern(workspace_root)
    ident_re = re.compile(rf"^{pattern}$")
    token_re = re.compile(rf"(?<!\w){pattern}")
```

Then change the `collect_source_identifiers(...)` call (line 158-160) to pass `token_re=token_re`:

```python
    present = collect_source_identifiers(
        workspace_root, include=src_include, exclude=src_exclude + doc_include,
        token_re=token_re,
    )
```

And change the `extract_backtick_symbols(text)` call inside the doc loop (line 174) to `extract_backtick_symbols(text, ident_re)`.

- [ ] **Step 4: Run to verify pass + NO regression**

Run: `$V python -m pytest tests/unit/core/test_doc_refs.py -v`
Expected: PASS — new tests AND every pre-existing doc_refs test (default behavior unchanged).

- [ ] **Step 5: Lint + commit**

Run: `$V ruff check src tests && $V mypy src`
```bash
git add src/super_harness/core/doc_refs.py tests/unit/core/test_doc_refs.py
git commit -m "feat: doc_refs derives identifier/token regexes from the language pattern (default-equivalent)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: The non-C-family proof (Ruby end-to-end)

**Files:**
- Test: `tests/unit/core/test_doc_refs.py`

This is the design's graded deliverable: with the default pattern a Ruby `?`-suffix dead-ref is MISSED (today's gap); with a Ruby `language.yaml` it is FLAGGED and live suffix methods resolve.

- [ ] **Step 1: Write the failing test** — append to `tests/unit/core/test_doc_refs.py`:

```python
from super_harness.core.doc_refs import scan_doc_refs


def _ruby_workspace(root):
    (root / "lib").mkdir(parents=True, exist_ok=True)
    (root / "lib" / "account.rb").write_text(
        "class Account\n"
        "  def valid?\n    @balance >= 0\n  end\n"
        "  def total_amount\n    @balance\n  end\n"
        "  def charge!(cents)\n    @balance -= cents\n  end\n"
        "end\n",
        encoding="utf-8",
    )
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "guide.md").write_text(
        "# Guide\n\nUse `total_amount`. Check `valid?` then `charge!`.\n"
        "Legacy: `removed_helper` and `deleted?` are gone.\n",
        encoding="utf-8",
    )
    (root / ".harness").mkdir(parents=True, exist_ok=True)


def test_ruby_default_pattern_misses_suffix_dead_ref(tmp_path):
    """Documents TODAY's gap: with the C-family default, a `?`-suffix dead ref is
    invisible; only the snake_case dead ref `removed_helper` is flagged."""
    _ruby_workspace(tmp_path)
    flagged = {f.symbol for f in scan_doc_refs(tmp_path).findings}
    assert "removed_helper" in flagged
    assert "deleted?" not in flagged  # missed — ? breaks the default pattern


def test_ruby_pattern_flags_suffix_dead_ref_and_resolves_live(tmp_path):
    """With a Ruby identifier_pattern: `deleted?` (dead) is flagged; `valid?` /
    `charge!` / `total_amount` (live) resolve; no false positive."""
    _ruby_workspace(tmp_path)
    (tmp_path / ".harness" / "language.yaml").write_text(
        "doc_refs:\n  identifier_pattern: '[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?'\n",
        encoding="utf-8",
    )
    flagged = {f.symbol for f in scan_doc_refs(tmp_path).findings}
    assert "deleted?" in flagged
    assert "removed_helper" in flagged
    assert "valid?" not in flagged
    assert "charge!" not in flagged
    assert "total_amount" not in flagged
```

- [ ] **Step 2: Run to verify (first passes immediately, second should pass after Task 2)**

Run: `$V python -m pytest tests/unit/core/test_doc_refs.py -k ruby -v`
Expected: BOTH PASS (Task 2 already wired the loading; these tests assert the end-to-end behavior). If `test_ruby_pattern_flags...` fails, debug the `token_re`/`ident_re` derivation in `scan_doc_refs` — `valid?` must be tokenized from `def valid?` (so it resolves) and `deleted?` must be admitted as a doc symbol (so it is checked).

- [ ] **Step 3: (no new impl — Task 2 already implements this; this task is the permanent CI-enforced proof.)**

- [ ] **Step 4: Run full doc_refs module**

Run: `$V python -m pytest tests/unit/core/test_doc_refs.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add tests/unit/core/test_doc_refs.py
git commit -m "test: Ruby non-C-family proof for the doc-refs identifier knob (n=2)" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Document the knob + OPEN-ITEMS + drift/suite gates

**Files:**
- Modify: `docs/getting-started.md`
- Modify: `private/OPEN-ITEMS.md` (gitignored — local only, NOT committed)

- [ ] **Step 1: Document `.harness/language.yaml` in `docs/getting-started.md`.** Add a focused subsection (place it near other `.harness/` configuration notes, or in a "Using a non-Python project" subsection). Exact content to add:

```markdown
### Tuning the dead-reference gate for non-C-family languages

`super-harness doc refs` flags backtick code-symbols in your prose docs that no
longer resolve in source. It recognizes a "code symbol" with a default identifier
pattern that fits C-family languages (Python, JavaScript/TypeScript, Go, Rust, Java,
C#, …): `[A-Za-z_][A-Za-z0-9_]*` with snake_case / camelCase shape. These work with
zero configuration.

A language with other identifier conventions (e.g. Ruby's `valid?` / `save!`
methods, or `@ivar` / `$global`) can tune the pattern in an optional
`.harness/language.yaml`:

```yaml
doc_refs:
  identifier_pattern: '[@$]{0,2}[A-Za-z_][A-Za-z0-9_]*[?!]?'   # Ruby
```

The single pattern drives both source tokenization and doc-span recognition, so they
stay consistent. A missing, malformed, or un-compilable config silently falls back to
the C-family default — it never breaks the gate.
```

(If the surrounding doc uses a different heading depth, match it. Pick the smallest
edit that gives the knob one clear home; do NOT name `.harness/gate-disabled` or
restructure unrelated sections.)

- [ ] **Step 2: Verify the doc addition does not self-trip the gate (super-harness's own repo, default pattern).**

Run: `$V super-harness doc refs 2>&1 | grep -v l1_update`
Expected: no NEW dead-ref for the lines you added (`identifier_pattern` resolves to
`core/language_profile.py`; `valid?` / `save!` / `@ivar` / `$global` do not "look like
a symbol" under the default pattern, so they are not checked). If a new dead-ref
appears, reword the example (e.g. avoid a backtick snake_case token that is not a real
source symbol).

- [ ] **Step 3: Record the deferred follow-up in `private/OPEN-ITEMS.md`** (local, gitignored — do NOT `git add` it). Append under a clear heading:

```markdown
## Language-profile doc-refs (PR for 2026-06-26-language-profile-doc-refs) — residue
- [DOABLE-NOW, deferred — separate from portability] `doc refs --gate` printed a
  `DEAD-REF` line but exited 0 during the 2026-06-26 portability recon. Investigate the
  `--gate` exit-code path (expected non-zero on a dead ref at the gate tier). Same on
  Python; NOT a language-coupling — recorded here so it is not lost.
- [BY-DESIGN] Only doc_refs is language-tuned (the sole governed-project coupling,
  proven by the TS n=2). Other languages with exotic conventions tune `identifier_pattern`;
  auto-detection by file extension is deliberately NOT built (YAGNI, "别建大框架").
- [SEPARATE AXIS] super-harness itself stays Python/pipx; a non-Python governed repo
  still needs Python on its CI runner to `pipx install super-harness`.
```

- [ ] **Step 4: Drift gates + full suite green**

Run:
```bash
$V super-harness sync --check        # expect exit 0 (no AGENTS.md change — no CLI surface change)
$V super-harness doc check           # expect exit 0 (no derived-doc change)
$V ruff check src tests && $V mypy src && $V python -m pytest -q
```
Expected: all green. (This slice adds no CLI command/flag and no event/state type, so
`AGENTS.md`, `docs/cli-reference.md`, `docs/state-machine.md` must NOT drift. If any
does, STOP and reconcile — and add it to the self-host PR scope before `plan ready`.)

- [ ] **Step 5: Commit the docs**

```bash
git add docs/getting-started.md
git commit -m "docs: document the .harness/language.yaml doc-refs identifier knob" -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Self-host merge sequence (after all tasks green)

Use a **date-prefixed slug** (convention; the branch is `2026-06-26-language-profile-doc-refs`).
Per `project-self-host-pr-attest-scope` (same as #51/#52). **#51 lesson:** run the drift
gates (Task 4 Step 4) BEFORE `plan ready` so the scope sees every changed file; there is
no CLI to amend scope mid-flight.

Enumerate scope from `git diff --name-only main` at plan-ready time. Expected scope:
`src/super_harness/core/language_profile.py`, `src/super_harness/core/doc_refs.py`,
`docs/getting-started.md`, `tests/unit/core/test_language_profile.py`,
`tests/unit/core/test_doc_refs.py`, and the two `docs/plans/2026-06-26-language-profile-doc-refs-*.md`.
(`private/OPEN-ITEMS.md` is gitignored — not in scope.)

```
change start 2026-06-26-language-profile-doc-refs
plan ready 2026-06-26-language-profile-doc-refs --tier-hint Normal --scope '[<git diff --name-only main>]'
review approve 2026-06-26-language-profile-doc-refs --reviewer plan-reviewer
implementation start 2026-06-26-language-profile-doc-refs
# ... Tasks 1-4, full suite green ...
done 2026-06-26-language-profile-doc-refs                 # pass slug explicitly
review prepare 2026-06-26-language-profile-doc-refs --reviewer code-reviewer --base main
# independent reviewer subagent → verdict file (5/5 checklist incl. doc-impact)
review approve 2026-06-26-language-profile-doc-refs --reviewer code-reviewer --verdict-file <path>
attest write 2026-06-26-language-profile-doc-refs && git add .harness/attestations && git commit
attest verify --base main --head HEAD
git push -u origin 2026-06-26-language-profile-doc-refs && gh pr create   # title/body right (token lacks read:org)
# CI green → squash → on-merge --commit <sha> --change 2026-06-26-language-profile-doc-refs
```

---

## Self-Review (completed)

- **Spec coverage:** §3.1 config file → Task 1 (loader) + Task 4 (docs). §3.2 loader → Task 1. §3.3 threading (load inside `scan_doc_refs`, `ident_re=^{p}$`, `token_re=(?<!\w){p}`, decoration signal, `looks_like_symbol`/`extract_backtick_symbols`/`collect_source_identifiers` params) → Task 2. §3.4 one-pattern rationale → embodied by Task 2 (single `pattern` drives both regexes). §4 honest limits → Task 4 OPEN-ITEMS (`--gate` exit-0, only-doc_refs, separate-axis). §5 test plan: loader tests → Task 1; default-equivalence incl. @/$/?/digit → Task 2 `test_default_tokenizer_equals_old_token_re`; decoration signal → Task 2; Ruby fixture → Task 3; self-host default path → Task 4 Step 2/4. §6 files → File Structure. All sections mapped.
- **Placeholder scan:** none — every code/doc step shows literal content.
- **Type/name consistency:** `IDENTIFIER_PATTERN_DEFAULT`, `load_identifier_pattern`, `language_file`, `looks_like_symbol(span, ident_re=...)`, `extract_backtick_symbols(text, ident_re=...)`, `collect_source_identifiers(..., token_re=...)`, `scan_doc_refs`, `ident_re`/`token_re` used identically across tasks and matching the real `core/doc_refs.py` signatures (verified against the source).
- **Default-equivalence guard:** Task 2 Step 1 `test_default_tokenizer_equals_old_token_re` locks the highest-risk claim with the exact adversarial strings the spec review flagged (`@`/`$`/`?`/`!`/digit).
- **Ordering:** Task 1 (loader) before Task 2 (imports it); Task 3 reuses Task 2's wiring; Task 4 gates last. No forward refs.
