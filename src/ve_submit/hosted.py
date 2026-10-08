"""Frozen local encrypted requests. No plaintext VE key is persisted or uploaded."""
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import time
import uuid

from . import secure
from .contracts import (ArtifactMismatch, IdentityMismatch, QuotaWait, ReconciliationRequired,
    canonical, canonical_sha256, decode, identifier, integer, strict_json, timestamp)
from .deadline import deadline
from .envelope import (context_wire, context_track, parse_context, MAX_CIPHERTEXT, MAX_ARCHIVE, PACKAGE_OVERHEAD,
    seal_package, validate_context, evidence_manifest as _evidence_manifest, context_solution_root,
    require_context_binding, BoardEnvelopeContext)
from .http import HTTP
from .package import freeze_package, source_manifest_from_archive
from .policy import PilotPolicy, validate_policy
from .staging_catalog import BoardBinding, MAX_BINDING, MAX_CATALOG, parse_catalog, require_board_binding


@dataclass(frozen=True)
class HostedPolicy:
    pilot: PilotPolicy
    key_id: str
    public_key: str
    recipient_fingerprint: str
    binding: BoardBinding | None = None


def public_recipient(policy):
    require_board_binding(policy.pilot, policy.binding)
    identifier(policy.recipient_fingerprint, "sha256")
    try:
        key = base64.b64decode(policy.public_key, validate=True)
    except (ValueError, TypeError):
        raise ValueError("invalid installed recipient") from None
    if len(key) != 32 or hashlib.sha256(key).hexdigest() != policy.recipient_fingerprint:
        raise IdentityMismatch("installed recipient fingerprint differs")
    return key


def _public_catalog(value):
    """Selectable client boards may belong to separate protected challenges."""
    if (type(value) is not dict or type(value.get("boards")) is not list
            or not 1 <= len(value["boards"]) <= 5 or len(canonical(value)) > MAX_CATALOG):
        raise ValueError("bounded public board catalog required")
    # Reuse the strict entry/schema validator without relaxing server catalogs.
    boards = [parse_catalog(value | {"boards": [entry]}).boards[0] for entry in value["boards"]]
    common = ("gateway_origin", "yukon_origin", "ve_origin", "controller_sha",
              "repository", "repository_id", "state_namespace", "ve_team_id")
    if (len({b.name for b in boards}) != len(boards)
            or len({b.policy.track_id for b in boards}) != len(boards)
            or any(getattr(b.policy, key) != getattr(boards[0].policy, key)
                   for b in boards for key in common)):
        raise ValueError("ambiguous or mismatched public board catalog")
    return {b.name: b for b in boards}


def load_hosted_policy(path, board=None):
    value = strict_json(secure.read_policy(Path(path).absolute(), MAX_CATALOG + 16384))
    if type(value) is dict and value.get("version") == 2:
        if set(value) != {"version", "catalog", "key_id", "public_key", "recipient_fingerprint"}:
            raise ValueError("closed installed hosted catalog required")
        integer(value["version"], 2, 2)
        catalog = _public_catalog(value["catalog"])
        if board is None:
            if len(catalog) != 1:
                raise ValueError("select an installed board with --board")
            board = next(iter(catalog))
        if type(board) is not str or board not in catalog:
            raise ValueError("board is not installed")
        binding = catalog[board]
        result = HostedPolicy(binding.policy, value["key_id"], value["public_key"],
                              value["recipient_fingerprint"], binding)
        public_recipient(result)
        return result
    if board not in (None, "t2-heart-interp"):
        raise ValueError("board is not installed")
    # Dataclass exports from this client include the absent optional binding.
    # Only null is compatible with the historical four-field installed policy.
    if type(value) is dict and "binding" in value and value["binding"] is None:
        value = {k: v for k, v in value.items() if k != "binding"}
    if type(value) is not dict or set(value) != {"pilot", "key_id", "public_key", "recipient_fingerprint"}:
        raise ValueError("installed hosted policy required")
    result = HostedPolicy(decode(PilotPolicy, value["pilot"]), value["key_id"], value["public_key"],
                          value["recipient_fingerprint"])
    public_recipient(result)
    return result


def require_saved_policy(policy, request, context):
    public_recipient(policy)
    require_context_binding(context, policy.binding)
    if (request.get("binding") != (policy.binding.value if policy.binding else None)
            or request["recipientFingerprint"] != policy.recipient_fingerprint
            or context.key_id != policy.key_id
            or context.benchmark_id != policy.pilot.track_id or context.ve_team_id != policy.pilot.ve_team_id):
        raise IdentityMismatch("saved request and installed configuration differ")
    return policy


def select_installed_policy(config, *, board=None, context=None):
    """Select only fixed trusted files, preserving legacy T2 and saved bindings."""
    config = Path(config).absolute()
    legacy = config / "hosted-policy.json"
    catalog = config / "hosted-catalog.json"
    has_catalog = catalog.exists() or catalog.is_symlink()
    has_legacy = legacy.exists() or legacy.is_symlink()
    if context is not None:
        board = context.scientific_track if type(context) is BoardEnvelopeContext else None
    if context is None and has_catalog and has_legacy and board in (None, "t2-heart-interp"):
        value = strict_json(secure.read_policy(catalog, MAX_CATALOG + 16384))
        if (type(value) is not dict or value.get("version") != 2
                or set(value) != {"version", "catalog", "key_id", "public_key", "recipient_fingerprint"}):
            raise ValueError("closed installed hosted catalog required")
        installed = _public_catalog(value["catalog"])
        if "t2-heart-interp" in installed:
            return catalog, load_hosted_policy(catalog, board="t2-heart-interp")
    wants_catalog = type(context) is BoardEnvelopeContext or board not in (None, "t2-heart-interp")
    selected = catalog if has_catalog and (
        wants_catalog or (context is None and not has_legacy)) else legacy
    return selected, load_hosted_policy(selected, board=board)


def _expiry(value):
    seconds = (timestamp(value) - datetime.now(timezone.utc)).total_seconds()
    if not 0 < seconds <= 7 * 86400:
        raise ValueError("authorization must expire within seven days")
    return seconds


def _metadata(context, wire):
    return dict(version=1, clientRunId=context.client_run_id, archiveSha256=context.archive_sha256,
        keyId=context.key_id, sha256=hashlib.sha256(wire).hexdigest(), byteLength=len(wire), expiresAt=context.expires_at)


def _write_request(root, context, ve_key, public_key, note, evidence, *, binding=None):
    validate_context(context); _expiry(context.expires_at)
    require_context_binding(context, binding)
    if type(note) is not str or not 1 <= len(note.encode()) <= 16384:
        raise ValueError("bounded research note required")
    source = secure.read(root / "submission.tar.gz", MAX_ARCHIVE)
    if (hashlib.sha256(source).hexdigest() != context.archive_sha256
            or canonical_sha256(source_manifest_from_archive(root / "submission.tar.gz",
                solution_root=context_solution_root(context))) != context.source_manifest_sha256):
        raise ArtifactMismatch("frozen source differs")
    wire = seal_package(context, ve_key, evidence, public_key)
    blobs = {"note.txt": note.encode(), "evidence.json": canonical([d for d, _ in evidence]), "ciphertext.bin": wire}
    for name, data in blobs.items():
        secure.atomic_write(root / name, data)
    request = dict(version={3: 2, 4: 3, 5: 4}[context.version], context=context_wire(context), recipientFingerprint=hashlib.sha256(public_key).hexdigest(),
        idempotencyKey=context.client_run_id, metadata=_metadata(context, wire),
        hashes={name: hashlib.sha256(data).hexdigest() for name, data in blobs.items()})
    if binding is not None:
        request["binding"] = binding.value
    encoded = canonical(request)
    secure.atomic_write(root / "request.json", encoded)
    secure.atomic_write(root / "request.sha256", hashlib.sha256(encoded).hexdigest().encode())
    return root


def freeze_hosted_inputs(checkout, state_root, evidence, *, binding=None):
    """Freeze local source and private evidence before prompting for a VE key."""
    base = secure.directory(state_root, create=True)
    checkout = Path(checkout).resolve(strict=True)
    if base.is_relative_to(checkout) or (len(evidence) != 0 and not 2 <= len(evidence) <= 8):
        raise ValueError("private state outside source and bounded evidence required")
    root = base / str(uuid.uuid4())
    root.mkdir(mode=0o700)
    if binding is not None and type(binding) is not BoardBinding:
        raise ValueError("installed board binding required")
    solution_root = binding.solution_root if binding is not None else "solution"
    frozen = freeze_package(checkout, root, solution_root=solution_root)
    with secure.regular(frozen.archive_path, maximum=MAX_ARCHIVE):
        pass
    items, total = [], PACKAGE_OVERHEAD
    evidence_root = secure.directory(root / "evidence", create=True)
    for index, (kind, selected) in enumerate(evidence):
        selected = Path(selected).absolute()
        if selected.resolve(strict=True).is_relative_to(checkout / solution_root):
            raise ValueError("private evidence must remain outside packaged source")
        data = secure.read(selected, MAX_CIPHERTEXT - total, private=False)
        total += len(data)
        item = dict(kind=kind, name=selected.name, sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), object_id=str(index))
        items.append((item, data))
        secure.atomic_write(evidence_root / str(index), data)
    if items:
        _evidence_manifest([i for i, _ in items])
    return root, frozen, items


def prepare_hosted_request(checkout, state_root, context, ve_key, public_key, note, evidence, *, frozen=None, binding=None):
    validate_context(context)
    require_context_binding(context, binding)
    base = secure.directory(state_root, create=True)
    if base.is_relative_to(Path(checkout).resolve()):
        raise ValueError("private request state must be outside the checkout")
    root = base / context.client_run_id
    root.mkdir(mode=0o700)
    package = freeze_package(checkout, root, solution_root=context_solution_root(context)) if frozen is None else frozen
    if frozen is not None:
        secure.atomic_write(root / "submission.tar.gz", secure.read(frozen.archive_path, MAX_ARCHIVE))
    if (package.archive_sha256 != context.archive_sha256 or package.source_manifest_sha256 != context.source_manifest_sha256):
        raise ArtifactMismatch("frozen source differs from authorized model")
    return _write_request(root, context, ve_key, public_key, note, evidence, binding=binding)


def _read_request(path):
    root = secure.directory(path)
    encoded = secure.read(root / "request.json", MAX_BINDING + 32768)
    if hashlib.sha256(encoded).hexdigest().encode() != secure.read(root / "request.sha256", 64):
        raise ArtifactMismatch("frozen request changed")
    request = strict_json(encoded)
    fields = {"version", "context", "recipientFingerprint", "idempotencyKey", "metadata", "hashes"}
    if type(request) is dict and request.get("version") == 4:
        fields.add("binding")
    if (type(request) is not dict or set(request) != fields
            or type(request["version"]) is not int or request["version"] not in (2, 3, 4)):
        raise ValueError("invalid frozen request")
    context = parse_context(request["context"])
    if (request["version"], context.version) not in ((2, 3), (3, 4), (4, 5)):
        raise ValueError("saved request and authorization versions differ")
    if request["version"] == 4:
        require_context_binding(context, BoardBinding(canonical(request["binding"])))
        if request["idempotencyKey"] != context.client_run_id or root.name != context.client_run_id:
            raise ArtifactMismatch("saved request identity differs")
    identifier(request["idempotencyKey"])
    identifier(request["recipientFingerprint"], "sha256")
    expected_files = {"note.txt": 16384, "evidence.json": 16384, "ciphertext.bin": MAX_CIPHERTEXT}
    if set(request["hashes"]) != set(expected_files):
        raise ArtifactMismatch("frozen file manifest differs")
    blobs = {}
    for name, maximum in expected_files.items():
        blobs[name] = secure.read(root / name, maximum)
        if hashlib.sha256(blobs[name]).hexdigest() != request["hashes"][name]:
            raise ArtifactMismatch("frozen request bytes changed")
    evidence = _evidence_manifest(strict_json(blobs["evidence.json"]), track=context_track(context))
    archive = secure.read(root / "submission.tar.gz", MAX_ARCHIVE)
    if (hashlib.sha256(archive).hexdigest() != context.archive_sha256
            or canonical_sha256(source_manifest_from_archive(root / "submission.tar.gz",
                solution_root=context_solution_root(context))) != context.source_manifest_sha256
            or canonical_sha256(evidence) != context.evidence_manifest_sha256):
        raise ArtifactMismatch("frozen model or evidence differs")
    if request["metadata"] != _metadata(context, blobs["ciphertext.bin"]):
        raise ArtifactMismatch("encrypted descriptor differs")
    return root, request, context, blobs, archive


class HostedConflict(ReconciliationRequired):
    def __init__(self, existing_submission_id=None):
        self.existing_submission_id = existing_submission_id
        super().__init__("existing candidate requires its original request or manual reconciliation")


class HostedYukonClient(HTTP):
    def __init__(self, token, policy, **kwargs):
        public_recipient(policy)
        self.hosted_policy = policy
        super().__init__(token, policy.pilot, "yukon_origin", **kwargs)

    def conflict_error(self, response):
        body = bytearray()
        for chunk in response.iter_bytes(chunk_size=4096):
            body.extend(chunk)
            if len(body) > 16384:
                return HostedConflict()
        try:
            value = strict_json(body)["error"].get("existingSubmissionId")
            return HostedConflict(identifier(value) if value else None)
        except (ValueError, KeyError, TypeError, AttributeError):
            return HostedConflict()

    def me(self):
        return self.json("GET", "/api/me")

    def check_config(self):
        descriptor = self.json("GET", f"/api/benchmarks/{self.policy.track_id}/attachment-config")["config"]
        if (descriptor.get("environment") != "staging" or descriptor.get("keyId") != self.hosted_policy.key_id
                or descriptor.get("publicKey") != self.hosted_policy.public_key):
            raise IdentityMismatch("trusted recipient and platform configuration differ")
        if descriptor.get("admissionEnabled") is not True or descriptor.get("deliveryEnabled") is not True:
            raise ValueError("encrypted admission is unavailable")
        integer(descriptor.get("maxBytes"), 1, MAX_CIPHERTEXT)
        return descriptor

    def check_recipient(self, context, fingerprint):
        validate_context(context)
        require_context_binding(context, self.hosted_policy.binding)
        if (context.key_id != self.hosted_policy.key_id or fingerprint != self.hosted_policy.recipient_fingerprint
                or context.benchmark_id != self.policy.track_id):
            raise IdentityMismatch("installed recipient differs")
        _expiry(context.expires_at)
        return self.check_config()

    def _authorize(self, root, request, context, target):
        require_saved_policy(self.hosted_policy, request, context)
        if (context.benchmark_id != self.policy.track_id or context.ve_team_id != self.policy.ve_team_id
                or self.me().get("account", {}).get("id") != context.yukon_account_id):
            raise IdentityMismatch("use the original Yukon account and configured contest")
        intent = dict(requestSha256=hashlib.sha256(canonical(request)).hexdigest(), origin=self.origin, **target)
        path = root / "network-intent.json"
        if path.exists():
            if strict_json(secure.read(path, 4096)) != intent:
                raise ValueError("resume the original request target and expected revision")
        else:
            config = self.check_recipient(context, request["recipientFingerprint"])
            if request["metadata"]["byteLength"] > config["maxBytes"]:
                raise ValueError("package exceeds configured size limit")
            secure.atomic_write(path, canonical(intent))

    def _multipart(self, method, path, request, parts):
        boundary = "ve-hosted-" + request["idempotencyKey"]
        chunks = []
        for name, content, filename in parts:
            suffix = f'; filename="{filename}"' if filename else ""
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{suffix}\r\n\r\n'.encode())
            chunks.extend((content, b"\r\n"))
        chunks.append(f"--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        return self.json(method, path, content=body, headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)), "Idempotency-Key": request["idempotencyKey"]})

    def submit(self, request_dir, *, on_wait=None):
        root, request, context, blobs, archive = _read_request(request_dir)
        self._authorize(root, request, context, dict(kind="admission", benchmarkId=context.benchmark_id))
        parts = [("archive", archive, "submission.tar.gz"), ("note", blobs["note.txt"], None),
            ("attachmentMetadata", canonical(request["metadata"]), None), ("attachment", blobs["ciphertext.bin"], "package.bin")]
        expires = time.monotonic() + 120
        # Only an explicit 429 permits automatic resending. Lost acknowledgments,
        # timeouts, conflicts and server failures retain the existing resume path.
        with deadline(120):
            for attempt in range(6):
                if attempt and time.monotonic() >= expires:
                    raise QuotaWait("upload capacity wait expired; resume this saved request")
                # Yukon checks new-admission expiry; exact existing receipts
                # remain recoverable even after this authorization expires.
                try:
                    response = self._multipart("POST", f"/api/benchmarks/{context.benchmark_id}/submissions", request, parts)
                    break
                except QuotaWait as error:
                    delay = max(error.retry_after, 2 ** attempt)
                    if attempt == 5 or time.monotonic() + delay >= expires:
                        raise
                    if on_wait is not None:
                        on_wait(delay)
                    time.sleep(delay)
        identifier(response.get("submission", {}).get("id"))
        if response.get("benchmark", {}).get("id") != context.benchmark_id:
            raise IdentityMismatch("admission receipt differs")
        receipt = dict(submission={"id": response["submission"]["id"]}, benchmark={"id": context.benchmark_id})
        path = root / "receipt.json"
        if path.exists():
            if strict_json(secure.read(path, 4096)) != receipt:
                raise IdentityMismatch("saved admission receipt differs")
        else:
            secure.atomic_write(path, canonical(receipt))
        return receipt

    def status(self, submission_id):
        return self.json("GET", f"/api/submissions/{identifier(submission_id)}")

    def revoke(self, submission_id):
        with self.response("POST", f"/api/submissions/{identifier(submission_id)}/attachment/revoke"):
            pass


def submit_hosted_request(request_dir, yukon):
    return yukon.submit(request_dir)
