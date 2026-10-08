"""Operator-installed policy. A submitted checkout never chooses endpoints."""
from dataclasses import dataclass, fields
import os
from pathlib import Path
import re
import stat
import uuid
from urllib.parse import urlsplit

from .contracts import BOARD, decode, identifier, integer, strict_json
from .boards import scientific_track


@dataclass(frozen=True)
class PilotPolicy:
    gateway_origin: str
    yukon_origin: str
    ve_origin: str
    challenge_id: str
    track_id: str
    source_branch: str
    controller_sha: str
    repository: str
    repository_id: int
    state_namespace: str
    ve_team_id: str
    board_key: str
    max_artifact_bytes: int
    baseline_source_sha: str | None = None


@dataclass(frozen=True, kw_only=True)
class BoardPolicy(PilotPolicy):
    scientific_track: str
    binding_sha256: str
    schema_version: int = 2


BASELINE_OWNER = "7a4b457c-9ff5-45e6-aec3-832018fb0f1c"


def baseline_id(policy):
    validate_policy(policy)
    identifier(policy.baseline_source_sha, "commit")
    board = "" if policy_track(policy).name == "t2-heart-interp" else policy.board_key + ":"
    return str(uuid.uuid5(uuid.UUID(policy.challenge_id), "ve-staging-baseline:" + board + policy.baseline_source_sha))


def result_ref(policy):
    name = policy_track(policy).name
    return "refs/heads/ve-state/api-staging" + ("" if name == "t2-heart-interp" else "-" + name)


def submission_state_ref(policy):
    name = policy_track(policy).name
    return "refs/heads/ve-api-staging-submission-state" + ("" if name == "t2-heart-interp" else "-" + name)


def validate_policy(p):
    if type(p) is BoardPolicy:
        integer(p.schema_version, 2, 2)
        identifier(p.binding_sha256, "sha256")
        board = scientific_track(p.scientific_track).board_key
    elif type(p) is PilotPolicy:
        board = BOARD
    else:
        raise ValueError("versioned staging policy required")
    for origin in (p.gateway_origin, p.yukon_origin, p.ve_origin):
        if type(origin) is not str:
            raise ValueError("fixed HTTPS origin required")
        u = urlsplit(origin)
        if (u.scheme != "https" or not u.hostname or u.username or u.password
                or u.path or u.query or u.fragment or u.port is not None
                or origin != f"https://{u.hostname}"):
            raise ValueError("fixed HTTPS origin required")
    if (p.yukon_origin != "https://api-dev.ecdsa.fail"
            or p.ve_origin != "https://ve-staging-api.aristoteleo.com"
            or p.gateway_origin in (p.yukon_origin, p.ve_origin)
            or p.state_namespace != "api-staging" or p.board_key != board
            or type(p.source_branch) is not str or not re.fullmatch(r"staging/[a-z0-9-]{1,100}", p.source_branch)
            or p.repository != "Layr-Labs/ve-bprime-benchmark"):
        raise ValueError("qualified staging policy required")
    identifier(p.challenge_id); identifier(p.track_id)
    if (p.challenge_id == "8a04b24f-cfbb-497a-ac3f-003c586b2c30"
            or p.track_id == "cc2c5620-3c9b-49dc-9c5c-e0920951b428"):
        raise ValueError("a separate staging challenge and track are required")
    identifier(p.controller_sha, "commit"); identifier(p.ve_team_id, "ve")
    integer(p.repository_id, 1, (1 << 53)-1)
    integer(p.max_artifact_bytes, 1, 1 << 30)
    if p.baseline_source_sha is not None:
        identifier(p.baseline_source_sha, "commit")
    return p


def policy_track(policy):
    validate_policy(policy)
    return scientific_track(policy.scientific_track if type(policy) is BoardPolicy else "t2-heart-interp")


def parse_policy(value):
    if type(value) is dict and value.get("schema_version") == 2:
        if set(value) != {f.name for f in fields(BoardPolicy)}:
            raise ValueError("closed board policy required")
        return validate_policy(BoardPolicy(**value))
    return validate_policy(decode(PilotPolicy, value))


def load_policy(path):
    path = Path(path).absolute()
    if path.resolve(strict=True) != path:
        raise ValueError("policy must not traverse symlinks")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_mode & 0o022 or info.st_size > 16384
                or info.st_uid not in (0, os.getuid())):
            raise ValueError("trusted policy file required")
        data = os.read(fd, 16385)
    finally:
        os.close(fd)
    return parse_policy(strict_json(data))
