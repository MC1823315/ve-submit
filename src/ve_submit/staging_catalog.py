"""Immutable operator-installed board bindings; never load from a candidate."""
from dataclasses import dataclass, fields
import hashlib
import re

from .boards import scientific_track
from .board_contract import validate_board_contract
from .contracts import canonical, identifier, integer, strict_json
from .policy import BoardPolicy, PilotPolicy, validate_policy

MAX_CATALOG = 256 << 10
MAX_BINDING = 48 << 10


def _fields(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError("closed staging catalog fields required")


def _validate_entry(value):
    _fields(value, {"name", "policy", "fixture", "runtime", "official_contract", "retained_baseline"})
    track = scientific_track(value["name"])
    _fields(value["policy"], {f.name for f in fields(PilotPolicy)})
    policy = validate_policy(BoardPolicy(**value["policy"],
        scientific_track=track.name, binding_sha256=hashlib.sha256(canonical(value)).hexdigest()))
    if policy.baseline_source_sha is None:
        raise ValueError("board baseline source required")
    fixture = value["fixture"]
    _fields(fixture, {"files", "artifact", "package"})
    limits = {"genes.txt": 1 << 20, "index.json": 1 << 20, "input.h5ad": 1 << 30}
    if type(fixture["files"]) is list and len(fixture["files"]) == 4 and track.name == "t2-heart-interp":
        limits["E8.75.h5ad"] = 1 << 30
    if type(fixture["files"]) is not list or len(fixture["files"]) != len(limits):
        raise ValueError("pinned board fixture files required")
    for item, name in zip(fixture["files"], sorted(limits)):
        _fields(item, {"name", "sha256", "size_bytes"})
        if item["name"] != name:
            raise ValueError("ordered scientific fixture required")
        identifier(item["sha256"], "sha256")
        integer(item["size_bytes"], 1, limits[name])
    artifact = fixture["artifact"]
    _fields(artifact, {"repository_id", "run_id", "artifact_id", "sha256", "size_bytes"})
    for key in ("repository_id", "run_id", "artifact_id"):
        integer(artifact[key], 1, (1 << 53)-1)
    if artifact["repository_id"] != policy.repository_id:
        raise ValueError("fixture repository differs")
    identifier(artifact["sha256"], "sha256")
    integer(artifact["size_bytes"], 1, sum(limits.values()) + (2 << 20))
    _fields(fixture["package"], {"sha256", "size_bytes"})
    identifier(fixture["package"]["sha256"], "sha256")
    integer(fixture["package"]["size_bytes"], 1, sum(limits.values()) + (1 << 20))
    runtime = value["runtime"]
    _fields(runtime, {"workspace", "environment", "image_id", "execution_profile", "runtime_policy"})
    for key in ("workspace", "environment", "image_id"):
        if type(runtime[key]) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", runtime[key]):
            raise ValueError("bounded runtime identity required")
    if (not runtime["image_id"].startswith("im-") or runtime["execution_profile"] != "gpu24-16x64"
            or runtime["runtime_policy"] != ("t1-60-10-v1" if track.name == "t1-temporal-val" else "long-60-10-v1")):
        raise ValueError("scientific runtime policy differs")
    validate_board_contract(value["official_contract"], track.name)
    # A missing retained baseline is explicit and cannot confer qualification.
    # Non-null provenance is accepted only by the versioned baseline validator.
    if value["retained_baseline"] is not None:
        from .baseline import require_baseline_binding
        require_baseline_binding(value["retained_baseline"], value)
    return policy


@dataclass(frozen=True)
class BoardBinding:
    payload: bytes

    def __post_init__(self):
        if type(self.payload) is not bytes or not 1 <= len(self.payload) <= MAX_BINDING:
            raise ValueError("bounded immutable board binding required")
        value = strict_json(self.payload)
        if canonical(value) != self.payload:
            raise ValueError("canonical board binding required")
        _validate_entry(value)

    @property
    def value(self):
        return strict_json(self.payload)

    @property
    def name(self):
        return self.value["name"]

    @property
    def digest(self):
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def policy(self):
        return BoardPolicy(**self.value["policy"], scientific_track=self.name, binding_sha256=self.digest)

    @property
    def board_key(self):
        return scientific_track(self.name).board_key

    @property
    def solution_root(self):
        return scientific_track(self.name).solution_root

    @property
    def fixture(self):
        return self.value["fixture"]

    @property
    def runtime(self):
        return self.value["runtime"]

    @property
    def official_contract(self):
        return self.value["official_contract"]

    @property
    def retained_baseline(self):
        return self.value["retained_baseline"]


@dataclass(frozen=True)
class StagingCatalog:
    boards: tuple[BoardBinding, ...]

    def __post_init__(self):
        if (type(self.boards) is not tuple or not 1 <= len(self.boards) <= 5
                or any(type(b) is not BoardBinding for b in self.boards)):
            raise ValueError("one to five immutable board bindings required")
        if (len({b.name for b in self.boards}) != len(self.boards)
                or len({b.policy.track_id for b in self.boards}) != len(self.boards)):
            raise ValueError("duplicate scientific board or benchmark")
        common = ("gateway_origin", "yukon_origin", "ve_origin", "challenge_id", "controller_sha",
                  "repository", "repository_id", "state_namespace", "ve_team_id")
        first = self.boards[0].policy
        if any(getattr(b.policy, key) != getattr(first, key) for b in self.boards for key in common):
            raise ValueError("shared staging identity differs")

    def by_name(self, name):
        scientific_track(name)
        return self._select(lambda b: b.name == name)

    def by_benchmark(self, benchmark_id):
        identifier(benchmark_id)
        return self._select(lambda b: b.policy.track_id == benchmark_id)

    def _select(self, predicate):
        selected = [b for b in self.boards if predicate(b)]
        if len(selected) != 1:
            raise ValueError("board is not installed")
        return selected[0]


def parse_catalog(value):
    _fields(value, {"version", "boards"})
    integer(value["version"], 1, 1)
    if type(value["boards"]) is not list or not 1 <= len(value["boards"]) <= 5 or len(canonical(value)) > MAX_CATALOG:
        raise ValueError("bounded installed board catalog required")
    return StagingCatalog(tuple(BoardBinding(canonical(v)) for v in value["boards"]))


def require_board_binding(policy, binding):
    validate_policy(policy)
    if type(policy) is BoardPolicy:
        if type(binding) is not BoardBinding or binding.policy != policy:
            raise ValueError("installed policy and board binding differ")
    elif binding is not None:
        raise ValueError("legacy policy cannot select a catalog binding")
    return binding
