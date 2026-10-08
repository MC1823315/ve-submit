"""Strict public identities shared with the gateway; no credentials."""
from dataclasses import asdict, dataclass, fields
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

BOARD = "T2:heart:val_interp"
SCHEMA_VERSION = 1


class SubmissionError(ValueError):
    code = "submission_error"


class AuthenticationRequired(SubmissionError):
    code = "authentication_required"


class QuotaWait(SubmissionError):
    code = "quota_wait"


class IdentityMismatch(SubmissionError):
    code = "identity_mismatch"


class ArtifactMismatch(SubmissionError):
    code = "artifact_mismatch"


class ReconciliationRequired(SubmissionError):
    code = "reconciliation_required"


class Stopped(SubmissionError):
    code = "stopped"


class Pending(SubmissionError):
    code = "pending"


class GenerationFailed(SubmissionError):
    code = "generation_failed"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode()


def canonical_sha256(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def strict_json(data):
    def pairs(rows):
        result = {}
        for key, value in rows:
            if key in result:
                raise ValueError("duplicate JSON field")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("nonfinite JSON value")
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


def identifier(value, kind="uuid"):
    if type(value) is not str:
        raise ValueError("invalid identifier")
    patterns = {"sha256": r"[0-9a-f]{64}", "commit": r"[0-9a-f]{40}",
                "ve": r"[0-9a-f]{32}", "member": r"vem_[0-9a-f]{32}"}
    if kind == "uuid":
        try:
            valid = str(uuid.UUID(value)) == value
        except ValueError:
            valid = False
    else:
        valid = re.fullmatch(patterns[kind], value) is not None
    if not valid:
        raise ValueError("invalid identifier")
    return value


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError("invalid bounded integer")
    return value


def timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.utcoffset() is None:
            raise ValueError()
        return result
    except (ValueError, TypeError):
        raise ValueError("timezone-aware timestamp required") from None


@dataclass(frozen=True)
class FrozenPackage:
    archive_path: Path
    archive_sha256: str
    source_manifest_sha256: str


@dataclass(frozen=True)
class MemberBinding:
    yukon_account_id: str
    ve_member_id: str
    ve_team_id: str
    environment: str
    active: bool
    evidence_ref: str


@dataclass(frozen=True)
class RunIntent:
    client_run_id: str
    source_manifest_sha256: str
    archive_sha256: str
    note_sha256: str
    evidence_manifest_sha256: str
    ve_member_id: str
    board_key: str
    consent_version: int


@dataclass(frozen=True)
class TrackRunIntent(RunIntent):
    track: str


@dataclass(frozen=True, kw_only=True)
class BoardBaselineIntent(TrackRunIntent):
    benchmark_id: str
    scientific_track: str
    binding_sha256: str


def intent_track(intent):
    from .track import require_track
    if type(intent) is RunIntent:
        return "agent"
    if type(intent) in (TrackRunIntent, BoardBaselineIntent):
        return require_track(intent.track)
    raise ValueError("versioned run intent required")


@dataclass(frozen=True)
class ArtifactManifest:
    run_id: str
    yukon_submission_id: str
    yukon_account_id: str
    ve_member_id: str
    ve_team_id: str
    environment: str
    board_key: str
    source_manifest_sha256: str
    generation_receipt_sha256: str
    checkpoint_commit: str
    artifact_sha256: str
    artifact_bytes: int


@dataclass(frozen=True, kw_only=True)
class BoardArtifactManifest(ArtifactManifest):
    benchmark_id: str
    scientific_track: str
    binding_sha256: str


@dataclass(frozen=True)
class VerifiedResult:
    run_id: str
    ve_submission_id: str
    member_id: str
    team_id: str
    environment: str
    board_key: str
    artifact_sha256: str
    score: float
    observed_at: str
    response_sha256: str


@dataclass(frozen=True, kw_only=True)
class BoardVerifiedResult(VerifiedResult):
    benchmark_id: str
    scientific_track: str
    binding_sha256: str


def decode(cls, value):
    if type(value) is not dict:
        raise ValueError("object required")
    value = dict(value)
    if "schema_version" in value:
        integer(value.pop("schema_version"), 1, 1)
    if set(value) != {f.name for f in fields(cls)}:
        raise ValueError("invalid object fields")
    return cls(**value)


def wire(value):
    version = (3 if type(value) is BoardBaselineIntent else
               2 if type(value) in (TrackRunIntent, BoardArtifactManifest, BoardVerifiedResult) else SCHEMA_VERSION)
    return {"schema_version": version, **asdict(value)}


def parse_intent(value):
    if type(value) is not dict:
        raise ValueError("object required")
    value = dict(value)
    version = integer(value.pop("schema_version", 1), 1, 3)
    result = decode({1: RunIntent, 2: TrackRunIntent, 3: BoardBaselineIntent}[version], value)
    board = BOARD
    if version == 3:
        from .boards import scientific_track
        board = scientific_track(result.scientific_track).board_key
        identifier(result.benchmark_id)
        identifier(result.binding_sha256, "sha256")
    intent_track(result)
    identifier(result.client_run_id)
    for field in ("source_manifest_sha256", "archive_sha256", "note_sha256",
                  "evidence_manifest_sha256"):
        identifier(getattr(result, field), "sha256")
    identifier(result.ve_member_id, "member")
    if result.board_key != board:
        raise ValueError("unsupported pilot board")
    integer(result.consent_version, 1, 1)
    return result


def parse_manifest(value):
    if type(value) is dict and value.get("schema_version") == 2:
        integer(value["schema_version"], 2, 2)
        result = decode(BoardArtifactManifest, {k: v for k, v in value.items() if k != "schema_version"})
        from .boards import scientific_track
        board = scientific_track(result.scientific_track).board_key
        identifier(result.benchmark_id)
        identifier(result.binding_sha256, "sha256")
    else:
        result = decode(ArtifactManifest, value)
        board = BOARD
    for field in ("run_id", "yukon_submission_id", "yukon_account_id"):
        identifier(getattr(result, field))
    identifier(result.ve_member_id, "member")
    identifier(result.ve_team_id, "ve")
    identifier(result.checkpoint_commit, "commit")
    for field in ("source_manifest_sha256", "generation_receipt_sha256",
                  "artifact_sha256"):
        identifier(getattr(result, field), "sha256")
    integer(result.artifact_bytes, 1, 1 << 30)
    if result.environment != "staging" or result.board_key != board:
        raise ValueError("unsupported result scope")
    return result


def parse_result(value):
    if type(value) is dict and value.get("schema_version") == 2:
        integer(value["schema_version"], 2, 2)
        result = decode(BoardVerifiedResult, {k: v for k, v in value.items() if k != "schema_version"})
        from .boards import scientific_track
        board = scientific_track(result.scientific_track).board_key
        identifier(result.benchmark_id)
        identifier(result.binding_sha256, "sha256")
        lower, upper = -1e12, 1e12
    else:
        result = decode(VerifiedResult, value)
        board, lower, upper = BOARD, 0, 100
    identifier(result.run_id)
    identifier(result.ve_submission_id, "ve")
    identifier(result.member_id, "member")
    identifier(result.team_id, "ve")
    identifier(result.artifact_sha256, "sha256")
    identifier(result.response_sha256, "sha256")
    timestamp(result.observed_at)
    if (result.environment != "staging" or result.board_key != board
            or type(result.score) not in (float, int) or not math.isfinite(result.score)
            or not lower <= result.score <= upper):
        raise ValueError("invalid official result")
    return result
