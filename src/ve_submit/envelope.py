"""One bounded sealed package for the trusted VE jobs; never extract its ZIP."""
from dataclasses import asdict, dataclass, fields
from datetime import timedelta
import re
import hashlib
import io
import stat
import zipfile

from nacl.exceptions import CryptoError
from nacl.public import PrivateKey, PublicKey, SealedBox

from .contracts import BOARD, canonical, canonical_sha256, identifier, integer, strict_json, timestamp
from .track import require_track, validate_metadata
from .boards import scientific_track

MAX_CIPHERTEXT = 64 * 1024 * 1024
MAX_ARCHIVE = 25 * 1024 * 1024
PACKAGE_OVERHEAD = 65536


@dataclass(frozen=True)
class EnvelopeContext:
    version: int
    environment: str
    key_id: str
    yukon_account_id: str
    benchmark_id: str
    client_run_id: str
    archive_sha256: str
    source_manifest_sha256: str
    ve_member_id: str
    ve_team_id: str
    board_key: str
    expires_at: str
    uploads_authorized: int
    evidence_manifest_sha256: str
    model: str
    agent_framework: str
    agent_model: str


@dataclass(frozen=True, kw_only=True)
class TrackEnvelopeContext(EnvelopeContext):
    track: str
    agent_framework: str | None = None
    agent_model: str | None = None


@dataclass(frozen=True, kw_only=True)
class BoardEnvelopeContext(TrackEnvelopeContext):
    scientific_track: str
    binding_sha256: str


def context_track(context):
    if type(context) is EnvelopeContext:
        integer(context.version, 3, 3)
        return "agent"
    if type(context) is TrackEnvelopeContext:
        integer(context.version, 4, 4)
        return require_track(context.track)
    if type(context) is BoardEnvelopeContext:
        integer(context.version, 5, 5)
        return require_track(context.track)
    raise ValueError("versioned authorization context required")


def context_metadata(context):
    track = context_track(context)
    value = dict(model=context.model)
    if track == "agent":
        value.update(agent_framework=context.agent_framework, agent_model=context.agent_model)
    elif context.agent_framework is not None or context.agent_model is not None:
        raise ValueError("human authorization contains agent attribution")
    return validate_metadata(track, value)


def context_wire(context):
    validate_context(context)
    value = asdict(context)
    if context_track(context) == "human":
        del value["agent_framework"], value["agent_model"]
    return value


def parse_context(value):
    if type(value) is not dict or type(value.get("version")) is not int:
        raise ValueError("versioned context object required")
    if value["version"] == 3:
        cls = EnvelopeContext
        keys = {f.name for f in fields(cls)}
    elif value["version"] in (4, 5):
        cls = TrackEnvelopeContext if value["version"] == 4 else BoardEnvelopeContext
        track = require_track(value.get("track"))
        keys = {f.name for f in fields(cls)}
        if track == "human":
            keys -= {"agent_framework", "agent_model"}
    else:
        raise ValueError("unsupported authorization version")
    if set(value) != keys:
        raise ValueError("strict context fields required")
    return validate_context(cls(**value))


def validate_context(context):
    context_track(context)
    integer(context.uploads_authorized, 1, 1)
    board = BOARD
    if type(context) is BoardEnvelopeContext:
        board = scientific_track(context.scientific_track).board_key
        identifier(context.binding_sha256, "sha256")
    if context.environment != "staging" or context.board_key != board:
        raise ValueError("staging board authorization required")
    if type(context.key_id) is not str or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,63}", context.key_id):
        raise ValueError("invalid recipient identity")
    for field in ("yukon_account_id", "benchmark_id", "client_run_id"):
        identifier(getattr(context, field))
    for field in ("archive_sha256", "source_manifest_sha256", "evidence_manifest_sha256"):
        identifier(getattr(context, field), "sha256")
    identifier(context.ve_member_id, "member")
    identifier(context.ve_team_id, "ve")
    if (type(context.expires_at) is not str or len(context.expires_at) > 35
            or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)", context.expires_at)
            or timestamp(context.expires_at).utcoffset() != timedelta(0)):
        raise ValueError("UTC authorization expiry required")
    context_metadata(context)
    return context


def context_solution_root(context):
    validate_context(context)
    return scientific_track(context.scientific_track).solution_root if type(context) is BoardEnvelopeContext else "solution"


def require_context_binding(context, binding):
    from .staging_catalog import BoardBinding
    validate_context(context)
    if type(context) is BoardEnvelopeContext:
        if (type(binding) is not BoardBinding or context.binding_sha256 != binding.digest
                or context.scientific_track != binding.name or context.board_key != binding.board_key
                or context.benchmark_id != binding.policy.track_id or context.ve_team_id != binding.policy.ve_team_id):
            raise ValueError("authorized board binding differs")
    elif binding is not None:
        raise ValueError("legacy authorization cannot select a catalog board")
    return binding


def _key(value):
    if type(value) is not str or not 1 <= len(value) <= 4096 or any(not 33 <= ord(c) <= 126 for c in value):
        raise ValueError("invalid private authorization")
    return value


def validate_evidence_item(item):
    if type(item) is not dict or set(item) != {"kind", "name", "sha256", "bytes", "object_id"}:
        raise ValueError("strict evidence descriptor required")
    if item["kind"] not in ("trajectory", "prompts", "harness", "other"):
        raise ValueError("unsupported VE evidence kind")
    if (type(item["name"]) is not str or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", item["name"])
            or item["name"] in (".", "..") or type(item["object_id"]) is not str
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item["object_id"])):
        raise ValueError("bounded evidence identity required")
    identifier(item["sha256"], "sha256")
    integer(item["bytes"], 1, MAX_CIPHERTEXT)
    return item


def evidence_manifest(value, *, track="agent"):
    if require_track(track) == "human":
        if type(value) is not list or value:
            raise ValueError("human authorization must contain no evidence")
        return value
    if type(value) is not list or not 2 <= len(value) <= 8:
        raise ValueError("trajectory and another evidence kind required")
    for item in value:
        validate_evidence_item(item)
    if (len({i["name"] for i in value}) != len(value) or len({i["object_id"] for i in value}) != len(value)
            or "trajectory" not in {i["kind"] for i in value} or len({i["kind"] for i in value}) < 2
            or sum(i["bytes"] for i in value) > MAX_CIPHERTEXT):
        raise ValueError("distinct bounded authoring evidence required")
    return value


def package_size(context, evidence):
    """Conservative bound checked before key collection; actual encryption is bounded too."""
    validate_context(context)
    manifest = evidence_manifest([item for item, _ in evidence], track=context_track(context))
    if canonical_sha256(manifest) != context.evidence_manifest_sha256:
        raise ValueError("package evidence binding differs")
    for item, data in evidence:
        if type(data) is not bytes or len(data) != item["bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError("package evidence differs")
    size = sum(i["bytes"] for i in manifest) + PACKAGE_OVERHEAD
    if size > MAX_CIPHERTEXT:
        raise ValueError("encrypted package exceeds 64 MiB")
    return size


@dataclass(frozen=True)
class OpenedPackage:
    context: EnvelopeContext
    ve_key: str
    evidence: list


def seal_package(context, ve_key, evidence, public_key):
    package_size(context, evidence)
    _key(ve_key)
    if type(public_key) is not bytes or len(public_key) != 32:
        raise ValueError("invalid recipient public key")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", canonical(dict(context=context_wire(context), ve_key=ve_key,
            evidence=[item for item, _ in evidence])))
        for index, (_, data) in enumerate(evidence):
            archive.writestr(f"evidence/{index}", data)
    wire = SealedBox(PublicKey(public_key)).encrypt(out.getvalue())
    if len(wire) > MAX_CIPHERTEXT:
        raise ValueError("encrypted package exceeds 64 MiB")
    return wire


def open_package(ciphertext, private_key):
    if (type(ciphertext) is not bytes or not 48 < len(ciphertext) <= MAX_CIPHERTEXT
            or type(private_key) is not bytes or len(private_key) != 32):
        raise ValueError("invalid encrypted package")
    try:
        raw = SealedBox(PrivateKey(private_key)).decrypt(ciphertext)
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            expected = ["manifest.json"] + [f"evidence/{i}" for i in range(len(entries)-1)]
            if (not 1 <= len(entries) <= 9 or [i.filename for i in entries] != expected or archive.comment
                    or any(i.compress_type != zipfile.ZIP_STORED or i.compress_size != i.file_size
                           or i.flag_bits & 1 or i.extra or i.comment
                           or stat.S_IFMT(i.external_attr >> 16) not in (0, stat.S_IFREG)
                           or not 0 < i.file_size <= MAX_CIPHERTEXT for i in entries)
                    or entries[0].file_size > PACKAGE_OVERHEAD
                    or sum(i.file_size for i in entries) > MAX_CIPHERTEXT):
                raise ValueError()
            manifest_bytes = archive.read(entries[0])
            value = strict_json(manifest_bytes)
            if (type(value) is not dict or set(value) != {"context", "ve_key", "evidence"}
                    or type(value["context"]) is not dict
                    or canonical(value) != manifest_bytes):
                raise ValueError()
            context = parse_context(value["context"])
            manifest = evidence_manifest(value["evidence"], track=context_track(context))
            if len(manifest) != len(entries)-1 or any(i["bytes"] != e.file_size for i, e in zip(manifest, entries[1:])):
                raise ValueError()
            evidence = [(item, archive.read(entry)) for item, entry in zip(manifest, entries[1:])]
            package_size(context, evidence)
            return OpenedPackage(context, _key(value["ve_key"]), evidence)
    except (CryptoError, TypeError, ValueError, UnicodeError, KeyError, zipfile.BadZipFile, RuntimeError, EOFError):
        raise ValueError("invalid encrypted package") from None
