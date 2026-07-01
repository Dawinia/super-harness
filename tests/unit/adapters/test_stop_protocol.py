from __future__ import annotations

import json

from super_harness.adapters.agent import _stop_protocol as sp
from super_harness.core.authoring_check import Verdict, Violation


def test_is_continuation_true_only_when_flag_is_true():
    assert sp.is_continuation({"stop_hook_active": True}) is True
    assert sp.is_continuation({"stop_hook_active": False}) is False
    assert sp.is_continuation({}) is False              # absent → first fire
    assert sp.is_continuation({"stop_hook_active": "true"}) is False  # STRICT: only bool True


def test_block_feedback_empty_when_clean():
    assert sp.block_feedback(Verdict(violations=[])) == ""


def test_block_feedback_is_decision_block_reason_naming_the_decision():
    v = Verdict(violations=[Violation(
        decision_id="d-core-is-base", detail="core imports sensors",
        decision_doc_path="docs/decisions/d-core-is-base.md")])
    obj = json.loads(sp.block_feedback(v))
    assert obj["decision"] == "block"
    assert "d-core-is-base" in obj["reason"]
    assert set(obj) == {"decision", "reason"}  # reason-ONLY (spike: extra fields break Codex Stop)
