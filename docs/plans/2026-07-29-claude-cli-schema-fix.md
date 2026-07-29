---
change: 2026-07-29-claude-cli-schema-fix
stage: plan
---

# claude-cli `$schema` rejection — fix plan (Micro)

**Goal:** Make the `claude-cli` reviewer protocol produce an invocation the
installed Claude CLI actually accepts, so the `claude` review source stops
failing every run on this machine.

**Tier:** Micro — one adapter file, one behaviour, no design surface.

## The defect

`core/review_verdict.py:133` emits the verdict schema with

```python
"$schema": "https://json-schema.org/draft/2020-12/schema",
```

`adapters/reviewer/claude_cli.py:63` serialises that object verbatim into
`claude --json-schema <inline json>`. The installed Claude CLI rejects it:

```
Error: --json-schema is not a valid JSON Schema:
       no schema with key or ref "https://json-schema.org/draft/2020-12/schema"
```

The process exits non-zero with an **empty** `result.raw.json`, so
`review result import` then fails with `Expecting value: line 1 column 1`.

### Measured, not assumed

```
$ echo "say ok" | claude --print --model haiku --output-format json \
    --json-schema '{"$schema":"https://json-schema.org/draft/2020-12/schema",...}'
Error: --json-schema is not a valid JSON Schema: no schema with key or ref ...

$ echo "say ok" | claude --print --model haiku --output-format json \
    --json-schema '{"type":"object","properties":{"a":{"type":"string"}},...}'
ACCEPTED
```

Dropping the `$schema` key is the whole difference.

### Blast radius: this has been silently costing reviews

The same failure was recorded on 2026-07-24 (both plan-reviewer rounds
`execution_failed`, attributed at the time to "infra flakiness"). It is not
flakiness — the `claude` source has been **structurally unusable** on this machine
for any review since the CLI started validating `--json-schema`. Every review that
appeared to be "2-source" but had claude fail was really single-source.

## Fix

The two protocols consume the same schema file differently:

| protocol | flag | form | accepts `$schema` |
|---|---|---|---|
| codex-cli | `--output-schema <path>` | file, byte-for-byte | yes (working today) |
| claude-cli | `--json-schema <json>` | inlined string | **no** |

That is a **protocol difference, so it belongs in the protocol adapter** — not in
the shared `build_verdict_schema`. Stripping `$schema` at the source would change
what Codex receives for no reason and lose a correct, standard annotation from the
on-disk artefact.

**Change:** in `claude_cli.py`, after loading and validating the schema object,
build the inlined copy from a shallow copy with `$schema` removed.

```python
        if not isinstance(schema, dict):
            raise ReviewerProtocolError("Claude verdict schema must be a JSON object")
        # The installed Claude CLI validates `--json-schema` against its own
        # registry and rejects the draft-2020-12 `$schema` URI outright
        # ("no schema with key or ref ..."), exiting non-zero with empty stdout.
        # The annotation is meaningless for an inlined schema anyway. Codex takes
        # the same file by path and does accept it, so the strip is protocol-local.
        inlined = {k: v for k, v in schema.items() if k != "$schema"}
        compact_schema = json.dumps(inlined, separators=(",", ":"), sort_keys=True)
```

## Tests

`tests/unit/adapters/reviewer/test_claude_cli.py`:

1. `test_inlined_schema_omits_dollar_schema` — build an invocation from a schema
   file containing `$schema`; assert the `--json-schema` argv value parses to a
   dict **without** `$schema`, and that every other key survives unchanged.
2. `test_schema_file_on_disk_is_not_mutated` — the file still contains `$schema`
   after the call (the strip is on a copy; Codex reads the same file).
3. `test_schema_without_dollar_schema_is_passed_through` — no-op when absent.

Regression anchor for the real defect (keep it explicit so a future refactor that
re-inlines the raw object fails loudly):

4. `test_no_json_schema_ref_url_in_argv` — assert `"json-schema.org"` does not
   appear anywhere in the generated argv.

## Verification

```bash
.venv/bin/pytest tests/unit/adapters/reviewer/ -v
.venv/bin/pytest -q
.venv/bin/ruff check .
```

Then a live proof that the actual failure is gone — freeze a round on this change
and run the claude invocation contract for real; it must produce a non-empty
`result.raw.json` that `review result import` accepts.

## Scope

- `src/super_harness/adapters/reviewer/claude_cli.py`
- `tests/unit/adapters/reviewer/test_claude_cli.py`
- `docs/plans/2026-07-29-claude-cli-schema-fix.md`

## Not in scope

- `core/review_verdict.py` — the on-disk schema keeps its `$schema`; Codex accepts it.
- The stale `gpt-5.6-sol` Codex model pin in the gitignored
  `.harness/review-profiles.local.yaml` — an operator config change, not a code fix.
- Any change to the review execution protocol, governance, or verdict shape.
