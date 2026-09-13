"""Pure predicates for plan authority and externally supplied review evidence.

The lifecycle event log remains the source of truth, but the meaning of an
authorizing event is deliberately kept in one small module.  This module does
not execute reviewers, load local model profiles, or make semantic claims about
an agent's prose.  It only answers the mechanical questions that the writer,
CLI, attestation gate, and tests must answer consistently:

* is a structured record canonical and supported;
* does a plan/evidence record identify the exact object it claims to cover;
* is a plan approval still applicable to the current candidate; and
* is a committed code subject bound to the complete Git change set.

The I/O helpers are intentionally thin.  All decisions are represented as
plain mappings so the event payload can preserve the original evidence without
introducing a second persistence format.
"""

from __future__ import annotations

import json
import posixpath
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

from super_harness.core.scope_match import GitScopeError, file_text_at_commit, resolve_commit

PLAN_SUBJECT_VERSION = "plan-authority/v1"
CODE_SUBJECT_VERSION = "code-subject/v1"
EVIDENCE_VERSION = "review-evidence/v1"
RECOGNITION_VERSION = "review-recognition/v1"


class ApprovalError(ValueError):
    """A record cannot be used as authority or evidence."""


class DuplicateKeyError(ApprovalError):
    """A JSON object contains the same key more than once."""


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKeyError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def load_json_record(path: Path) -> dict[str, Any]:
    """Read one JSON object with duplicate-key rejection."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApprovalError(f"cannot read JSON record {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ApprovalError(f"JSON record {path} must be an object")
    return value


def canonical_json(value: object) -> str:
    """Serialize structured records deterministically for identity hashes."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ApprovalError(f"record is not canonically serializable: {exc}") from exc


def digest_record(value: object) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_plan_text(text: str) -> str:
    """Normalize line endings for plan artifacts only."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def artifact_digest(content: str, *, plan_text: bool = True) -> str:
    normalized = normalize_plan_text(content) if plan_text else content
    return sha256(normalized.encode("utf-8")).hexdigest()


def _relative_path(root: Path, raw: str) -> str:
    p = Path(raw)
    try:
        resolved = (p if p.is_absolute() else root / p).resolve()
        relative = resolved.relative_to(root.resolve()).as_posix()
    except (OSError, RuntimeError, ValueError) as exc:
        raise ApprovalError(f"artifact path is outside the workspace: {raw!r}") from exc
    if not relative or relative == "." or relative.startswith("../"):
        raise ApprovalError(f"artifact path is invalid: {raw!r}")
    return relative


def _artifact_snapshot(root: Path, path: str, role: str, ref: str | None) -> dict[str, str]:
    relative = _relative_path(root, path)
    try:
        if ref is None:
            content = (root / relative).read_text(encoding="utf-8")
        else:
            content = file_text_at_commit(root, ref, relative)
    except (OSError, UnicodeDecodeError, GitScopeError) as exc:
        raise ApprovalError(f"cannot snapshot adopted artifact {relative!r}: {exc}") from exc
    normalized = normalize_plan_text(content)
    return {
        "path": relative,
        "role": role,
        "content": normalized,
        "digest": artifact_digest(normalized),
    }


def make_plan_subject(
    root: Path,
    *,
    change_id: str,
    artifacts: Iterable[tuple[str, str]],
    commitments: Iterable[dict[str, Any]],
    prior_approval: str | None = None,
    ref: str | None = None,
) -> dict[str, Any]:
    """Build an immutable plan subject from a finite adopted artifact set."""
    manifest = [
        _artifact_snapshot(root, path, role, ref)
        for path, role in sorted(artifacts, key=lambda pair: pair[0])
    ]
    if not manifest:
        raise ApprovalError("a plan subject must adopt at least one artifact")
    manifest_by_path = {item["path"]: item for item in manifest}
    plan_artifact = next(
        (item for item in manifest if item.get("role") == "plan"),
        manifest[0],
    )
    commitment_list: list[dict[str, Any]] = []
    for item in commitments:
        commitment = dict(item)
        identifier = commitment.get("id")
        text = commitment.get("text")
        if not isinstance(identifier, str) or not identifier:
            raise ApprovalError("commitments need a stable non-empty id")
        if not isinstance(text, str) or not text:
            raise ApprovalError(f"commitment {identifier!r} needs non-empty text")
        artifact_path = commitment.get("artifact_path", commitment.get("artifact"))
        if artifact_path is None:
            artifact_path = plan_artifact["path"]
        if not isinstance(artifact_path, str) or artifact_path not in manifest_by_path:
            raise ApprovalError(
                f"commitment {identifier!r} references an artifact outside the snapshot"
            )
        artifact_digest_value = commitment.get("artifact_digest")
        expected_digest = manifest_by_path[artifact_path]["digest"]
        if artifact_digest_value is not None and artifact_digest_value != expected_digest:
            raise ApprovalError(f"commitment {identifier!r} artifact digest does not match")
        commitment["artifact_path"] = artifact_path
        commitment["artifact_digest"] = expected_digest
        commitment_list.append(commitment)
    subject: dict[str, Any] = {
        "version": PLAN_SUBJECT_VERSION,
        "change_id": change_id,
        "artifacts": manifest,
        "commitments": commitment_list,
    }
    if prior_approval:
        subject["prior_approval"] = prior_approval
    subject["subject_id"] = f"plan:{digest_record(subject)}"
    return subject


def validate_plan_subject(subject: object, *, change_id: str | None = None) -> dict[str, Any]:
    """Validate shape, digests, and references of a plan subject."""
    if not isinstance(subject, dict):
        raise ApprovalError("plan subject must be an object")
    if subject.get("version") != PLAN_SUBJECT_VERSION:
        raise ApprovalError("unrecognized plan subject version")
    if not isinstance(subject.get("change_id"), str) or not subject["change_id"]:
        raise ApprovalError("plan subject needs a change_id")
    if change_id is not None and subject["change_id"] != change_id:
        raise ApprovalError("plan subject belongs to a different Change")
    artifacts = subject.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise ApprovalError("plan subject needs a non-empty artifact manifest")
    seen: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ApprovalError("plan artifact entries must be objects")
        path = artifact.get("path")
        role = artifact.get("role")
        content = artifact.get("content")
        digest = artifact.get("digest")
        if not all(isinstance(value, str) and value for value in (path, role, content, digest)):
            raise ApprovalError("plan artifact entries need path, role, content, and digest")
        assert isinstance(path, str)
        assert isinstance(content, str)
        assert isinstance(digest, str)
        normalized_path = posixpath.normpath(path)
        if (
            normalized_path != path
            or path.startswith("/")
            or path.startswith("../")
            or path == ".."
            or "\\" in path
        ):
            raise ApprovalError(f"plan artifact path is not repository-relative: {path!r}")
        if path in seen:
            raise ApprovalError(f"duplicate adopted artifact {path!r}")
        seen.add(path)
        if digest != artifact_digest(content):
            raise ApprovalError(f"artifact digest mismatch for {path!r}")
    commitments = subject.get("commitments")
    if not isinstance(commitments, list) or any(not isinstance(item, dict) for item in commitments):
        raise ApprovalError("plan subject commitments must be a list of objects")
    artifact_by_path = {item["path"]: item for item in artifacts}
    seen_commitments: set[str] = set()
    for commitment in commitments:
        identifier = commitment.get("id")
        text = commitment.get("text")
        artifact_path = commitment.get("artifact_path")
        artifact_digest_value = commitment.get("artifact_digest")
        if not all(isinstance(value, str) and value for value in (identifier, text, artifact_path)):
            raise ApprovalError("commitments need id, text, and artifact_path")
        assert isinstance(identifier, str)
        assert isinstance(artifact_path, str)
        if identifier in seen_commitments:
            raise ApprovalError(f"duplicate commitment id {identifier!r}")
        seen_commitments.add(identifier)
        artifact = artifact_by_path.get(artifact_path)
        if artifact is None or artifact_digest_value != artifact.get("digest"):
            raise ApprovalError(f"commitment {identifier!r} does not reference its snapshot")
    expected = dict(subject)
    supplied_id = expected.pop("subject_id", None)
    expected_id = f"plan:{digest_record(expected)}"
    if supplied_id != expected_id:
        raise ApprovalError("plan subject digest does not match its contents")
    return dict(subject)


def approval_record(
    *, subject: dict[str, Any], evidence_id: str, evidence_digest: str, skipped: bool = False
) -> dict[str, Any]:
    """Return the compact approval reference stored on a lifecycle event."""
    validated = validate_plan_subject(subject)
    if skipped:
        raise ApprovalError("a skipped conclusion cannot create plan authority")
    return {
        "version": PLAN_SUBJECT_VERSION,
        "approval_id": f"approval:{evidence_id}",
        "evidence_id": evidence_id,
        "evidence_digest": evidence_digest,
        "subject_id": validated["subject_id"],
        "change_id": validated["change_id"],
        "subject": validated,
        "skipped": bool(skipped),
    }


def validate_approval(value: object, *, change_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApprovalError("approval must be an object")
    if value.get("version") != PLAN_SUBJECT_VERSION:
        raise ApprovalError("unrecognized approval version")
    if not isinstance(value.get("approval_id"), str) or not value["approval_id"]:
        raise ApprovalError("approval needs an approval_id")
    subject = validate_plan_subject(value.get("subject"), change_id=change_id)
    if value.get("subject_id") != subject["subject_id"]:
        raise ApprovalError("approval subject_id does not match its subject")
    if value.get("change_id") != subject["change_id"]:
        raise ApprovalError("approval change_id does not match its subject")
    if value.get("skipped") is True:
        raise ApprovalError("a skipped conclusion cannot create plan authority")
    if not isinstance(value.get("evidence_id"), str) or not value["evidence_id"]:
        raise ApprovalError("approval needs an evidence_id")
    if not isinstance(value.get("evidence_digest"), str) or not value["evidence_digest"]:
        raise ApprovalError("approval needs an evidence digest")
    if value["approval_id"] != f"approval:{value['evidence_id']}":
        raise ApprovalError("approval_id must be derived from evidence_id")
    return dict(value)


def validate_evidence(value: object, *, change_id: str | None = None) -> dict[str, Any]:
    """Validate the mechanical portion of a review evidence record."""
    if not isinstance(value, dict):
        raise ApprovalError("review evidence must be an object")
    if value.get("version") != EVIDENCE_VERSION:
        raise ApprovalError("unrecognized review evidence version")
    required = ("evidence_id", "kind", "subject_id", "decision", "process", "issuer")
    if any(not isinstance(value.get(key), str) or not value[key] for key in required[:4]):
        raise ApprovalError("review evidence needs evidence_id, kind, subject_id, and decision")
    if value["kind"] not in {"plan", "code"}:
        raise ApprovalError("review evidence kind must be plan or code")
    if value["decision"] not in {"approve", "reject"}:
        raise ApprovalError("review evidence decision must be approve or reject")
    process = value.get("process")
    if not isinstance(process, dict) or not all(
        isinstance(process.get(key), str) and process[key] for key in ("id", "version")
    ):
        raise ApprovalError("review evidence needs a process id and version")
    if not isinstance(value.get("issuer"), str) or not value["issuer"]:
        raise ApprovalError("review evidence needs an issuer")
    original = value.get("original_evidence")
    if not original:
        raise ApprovalError("review evidence must retain original evidence")
    provenance = value.get("provenance")
    if not isinstance(provenance, dict):
        raise ApprovalError("review evidence needs provenance")
    if change_id is not None and provenance.get("change_id") not in {None, change_id}:
        raise ApprovalError("review evidence provenance belongs to another Change")
    supersedes = value.get("supersedes")
    if supersedes is not None and (not isinstance(supersedes, str) or not supersedes):
        raise ApprovalError("review evidence supersedes must be a non-empty evidence_id")
    subject = value.get("subject_id")
    if not isinstance(subject, str) or not subject:
        raise ApprovalError("review evidence needs a subject identifier")
    return dict(value)


@dataclass(frozen=True)
class Recognition:
    """One user-recognized evidence process."""

    version: str
    process_id: str
    process_version: str
    kinds: frozenset[str]
    issuers: frozenset[str]
    evidence_forms: frozenset[str]
    policy_digest: str | None = None


def recognition_policy_digest(raw: dict[str, Any]) -> str:
    """Digest the recognition policy without its self-referential digest field."""
    process = raw.get("process")
    if not isinstance(process, dict):
        raise ApprovalError("review recognition needs one process")
    process_without_digest = {
        key: value for key, value in process.items() if key != "policy_digest"
    }
    return digest_record(
        {
            "version": raw.get("version"),
            "enabled": raw.get("enabled"),
            "process": process_without_digest,
        }
    )


def _recognition_from_mapping(raw: object) -> Recognition:
    if not isinstance(raw, dict) or raw.get("version") != RECOGNITION_VERSION:
        raise ApprovalError("unrecognized review recognition policy version")
    if raw.get("enabled") is not True:
        raise ApprovalError("no review process is enabled by user recognition")
    process = raw.get("process")
    if not isinstance(process, dict):
        raise ApprovalError("review recognition needs one process")
    pid = process.get("id")
    pversion = process.get("version")
    if not isinstance(pid, str) or not pid or not isinstance(pversion, str) or not pversion:
        raise ApprovalError("recognized process needs id and version")
    kinds = process.get("kinds")
    issuers = process.get("issuers")
    forms = process.get("evidence_forms", ["json"])
    if not isinstance(kinds, list) or not all(isinstance(x, str) for x in kinds):
        raise ApprovalError("recognized process kinds are invalid")
    if not isinstance(issuers, list) or not all(isinstance(x, str) for x in issuers):
        raise ApprovalError("recognized process issuers are invalid")
    if not isinstance(forms, list) or not all(isinstance(x, str) for x in forms):
        raise ApprovalError("recognized evidence forms are invalid")
    policy_digest = process.get("policy_digest")
    if not isinstance(policy_digest, str) or not policy_digest:
        raise ApprovalError("enabled review recognition needs a policy digest")
    if policy_digest != recognition_policy_digest(raw):
        raise ApprovalError("review recognition policy digest does not match its contents")
    return Recognition(
        version=RECOGNITION_VERSION,
        process_id=pid,
        process_version=pversion,
        kinds=frozenset(kinds),
        issuers=frozenset(issuers),
        evidence_forms=frozenset(forms),
        policy_digest=policy_digest,
    )


def load_recognition(root: Path, *, ref: str | None = None) -> Recognition:
    """Load the owner-recognized policy from the working tree or a Git ref."""
    path = root / ".harness" / "review-recognition.yaml"
    try:
        text = (
            path.read_text(encoding="utf-8")
            if ref is None
            else file_text_at_commit(
                root, resolve_commit(root, ref), path.relative_to(root).as_posix()
            )
        )
        raw = yaml.safe_load(text)
    except FileNotFoundError as exc:
        raise ApprovalError("review recognition policy is not configured") from exc
    except (GitScopeError, OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ApprovalError(f"review recognition policy is invalid: {exc}") from exc
    return _recognition_from_mapping(raw)


def evidence_is_recognized(evidence: dict[str, Any], recognition: Recognition) -> bool:
    process = evidence.get("process")
    if not isinstance(process, dict):
        return False
    return (
        process.get("id") == recognition.process_id
        and process.get("version") == recognition.process_version
        and evidence.get("kind") in recognition.kinds
        and evidence.get("issuer") in recognition.issuers
        and evidence.get("form", "json") in recognition.evidence_forms
    )


def evidence_digest(evidence: dict[str, Any]) -> str:
    return digest_record(evidence)


def validate_evidence_reuse(
    evidence: dict[str, Any], prior: Iterable[dict[str, Any]], *, subject_id: str
) -> str:
    """Return ``new``, ``idempotent`` or raise for conflicting reuse."""
    validated = validate_evidence(evidence)
    if validated["subject_id"] != subject_id:
        raise ApprovalError("review evidence targets a different subject")
    current_digest = evidence_digest(validated)
    for old in prior:
        if old.get("evidence_id") != validated["evidence_id"]:
            continue
        if evidence_digest(old) != current_digest:
            raise ApprovalError("evidence_id was reused with different content")
        return "idempotent"
    validate_evidence_supersession(validated, prior)
    return "new"


def validate_evidence_supersession(
    evidence: dict[str, Any], prior: Iterable[dict[str, Any]]
) -> None:
    """Require replacements to name the latest conclusion for their subject."""
    validated = validate_evidence(evidence)
    same_subject = [
        item
        for item in prior
        if isinstance(item, dict) and item.get("subject_id") == validated["subject_id"]
    ]
    supersedes = validated.get("supersedes")
    if not same_subject:
        if supersedes is not None:
            raise ApprovalError(
                "review evidence supersedes a subject with no current conclusion"
            )
        return
    current = same_subject[-1]
    if supersedes != current.get("evidence_id"):
        raise ApprovalError(
            "a new conclusion for this subject must explicitly supersede the current conclusion"
        )


def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise GitScopeError(f"`git {' '.join(args)}` failed: {exc}") from exc
    return proc.stdout


def resolve_contract_base(root: Path) -> str:
    """Resolve the new contract's default base without old review policy."""
    for candidate in ("origin/main", "main"):
        try:
            return resolve_commit(root, candidate)
        except GitScopeError:
            continue
    raise ApprovalError("new-contract code subjects require a reachable main baseline")


def _tree_entries(root: Path, ref: str) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for line in _git(root, "ls-tree", "-r", ref).splitlines():
        if "\t" not in line:
            continue
        left, path = line.split("\t", 1)
        fields = left.split()
        if len(fields) >= 3:
            entries[path] = (fields[0], fields[2])
    return entries


def git_change_manifest(root: Path, *, base: str, head: str) -> list[dict[str, Any]]:
    """Build a deterministic mode/content manifest, including renames."""
    base_sha = resolve_commit(root, base)
    head_sha = resolve_commit(root, head)
    statuses = _git(root, "diff", "--name-status", "--find-renames", f"{base_sha}..{head_sha}")
    base_tree = _tree_entries(root, base_sha)
    head_tree = _tree_entries(root, head_sha)
    result: list[dict[str, Any]] = []
    for line in statuses.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        paths = [posixpath.normpath(p.replace("\\", "/")) for p in parts[1:]]
        old_path = paths[0] if status.startswith("R") or status.startswith("C") else None
        new_path = paths[-1]
        row: dict[str, Any] = {"status": status, "path": new_path}
        if old_path is not None:
            row["old_path"] = old_path
        row["old"] = {
            "mode": base_tree.get(old_path or new_path, (None, None))[0],
            "blob": base_tree.get(old_path or new_path, (None, None))[1],
        }
        row["new"] = {
            "mode": head_tree.get(new_path, (None, None))[0],
            "blob": head_tree.get(new_path, (None, None))[1],
        }
        # Attestation JSONL is evidence about the product change, not product
        # content itself.  Keep this exemption deliberately narrow: executable
        # files and arbitrary neighboring files under the same directory remain
        # part of the subject.
        if all(
            path.startswith(".harness/attestations/") and path.endswith(".jsonl")
            for path in (old_path or new_path, new_path)
        ):
            continue
        result.append(row)
    return sorted(result, key=lambda row: (str(row.get("path")), str(row.get("old_path", ""))))


def make_code_subject(
    root: Path,
    *,
    change_id: str,
    approval_id: str,
    base: str,
    head: str = "HEAD",
    verification_config_digest: str | None = None,
) -> dict[str, Any]:
    manifest = git_change_manifest(root, base=base, head=head)
    resolved_base = resolve_commit(root, base)
    resolved_head = resolve_commit(root, head)
    subject: dict[str, Any] = {
        "version": CODE_SUBJECT_VERSION,
        "change_id": change_id,
        "approval_id": approval_id,
        "base": resolved_base,
        # Keep HEAD as an audit locator, but do not include it in subject
        # identity.  Appending a validated attestation must not invalidate the
        # product subject merely because the evidence commit moved HEAD.
        "head": resolved_head,
        "manifest": manifest,
    }
    if verification_config_digest:
        subject["verification_config_digest"] = verification_config_digest
    identity = {key: value for key, value in subject.items() if key != "head"}
    subject["subject_id"] = f"code:{digest_record(identity)}"
    return subject


def validate_code_subject(value: object, *, change_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("version") != CODE_SUBJECT_VERSION:
        raise ApprovalError("unrecognized code subject version")
    if not isinstance(value.get("change_id"), str) or not value["change_id"]:
        raise ApprovalError("code subject needs a change_id")
    if change_id is not None and value["change_id"] != change_id:
        raise ApprovalError("code subject belongs to another Change")
    if not isinstance(value.get("approval_id"), str) or not value["approval_id"]:
        raise ApprovalError("code subject needs an approval_id")
    if (
        not isinstance(value.get("base"), str)
        or not value["base"]
        or not isinstance(value.get("head"), str)
        or not value["head"]
    ):
        raise ApprovalError("code subject needs base and head")
    manifest = value.get("manifest")
    if not isinstance(manifest, list):
        raise ApprovalError("code subject needs a complete manifest")
    for row in manifest:
        if not isinstance(row, dict):
            raise ApprovalError("code subject manifest entries must be objects")
        if not isinstance(row.get("status"), str) or not isinstance(row.get("path"), str):
            raise ApprovalError("code subject manifest entries need status and path")
        for side in ("old", "new"):
            details = row.get(side)
            if not isinstance(details, dict):
                raise ApprovalError(f"code subject manifest entries need {side} details")
            if any(
                details.get(key) is not None and not isinstance(details.get(key), str)
                for key in ("mode", "blob")
            ):
                raise ApprovalError("code subject mode/blob details are invalid")
    expected = dict(value)
    supplied_id = expected.pop("subject_id", None)
    expected.pop("head", None)
    if supplied_id != f"code:{digest_record(expected)}":
        raise ApprovalError("code subject digest does not match its contents")
    return dict(value)


def code_subject_paths(value: object) -> set[str]:
    """Return every product path represented by a validated code subject."""
    subject = validate_code_subject(value)
    paths: set[str] = set()
    for row in subject["manifest"]:
        assert isinstance(row, dict)
        for key in ("path", "old_path"):
            path = row.get(key)
            if isinstance(path, str) and path:
                paths.add(posixpath.normpath(path.replace("\\", "/")))
    return paths


def missing_coverage(value: object, coverage: object) -> list[str]:
    """Return subject paths not named by an implementation coverage manifest."""
    required = code_subject_paths(value)
    covered: set[str] = set()
    if isinstance(coverage, list):
        for item in coverage:
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                covered.add(posixpath.normpath(item["path"].replace("\\", "/")))
    return sorted(required - covered)


def plan_approval_is_applicable(
    value: object, *, change_id: str, pending_candidate: object | None = None
) -> bool:
    try:
        approval = validate_approval(value, change_id=change_id)
    except ApprovalError:
        return False
    if pending_candidate is None:
        return True
    # A pending candidate is not itself a revocation: B1 remains usable for
    # implementation.  Completion predicates separately require disposition.
    return bool(approval.get("approval_id"))


def has_unresolved_candidate(state: object) -> bool:
    candidate = getattr(state, "pending_revision", None)
    if isinstance(candidate, dict):
        return candidate.get("status") in {None, "pending", "rejected"}
    return False


def authorizing_event(event_type: str) -> bool:
    return event_type in {
        "plan_approved",
        "implementation_started",
        "implementation_restarted",
        "implementation_invalidated",
        "implementation_complete",
        "code_review_passed",
        "merged",
        "code_review_failed",
        "plan_rejected",
    }


__all__ = [
    "CODE_SUBJECT_VERSION",
    "EVIDENCE_VERSION",
    "PLAN_SUBJECT_VERSION",
    "RECOGNITION_VERSION",
    "ApprovalError",
    "DuplicateKeyError",
    "Recognition",
    "approval_record",
    "artifact_digest",
    "authorizing_event",
    "canonical_json",
    "code_subject_paths",
    "digest_record",
    "evidence_digest",
    "evidence_is_recognized",
    "git_change_manifest",
    "has_unresolved_candidate",
    "load_json_record",
    "load_recognition",
    "make_code_subject",
    "make_plan_subject",
    "missing_coverage",
    "normalize_plan_text",
    "plan_approval_is_applicable",
    "recognition_policy_digest",
    "resolve_contract_base",
    "validate_approval",
    "validate_code_subject",
    "validate_evidence",
    "validate_evidence_reuse",
    "validate_evidence_supersession",
]
