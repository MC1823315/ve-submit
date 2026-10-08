"""Origin-bound, bounded synchronous HTTP. Remote error text is never surfaced."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import re
import uuid

import httpx

from . import secure
from .contracts import (AuthenticationRequired, ArtifactMismatch, Pending, QuotaWait,
                        ReconciliationRequired, IdentityMismatch, Stopped, strict_json)
from .policy import validate_policy


class HTTP:
    def __init__(self, token, policy, origin_field, *, transport=None):
        validate_policy(policy)
        if type(token) is not str or not 1 <= len(token) <= 4096 or any(not 33 <= ord(c) <= 126 for c in token):
            raise AuthenticationRequired("a valid key is required")
        self.policy = policy
        self.origin = getattr(policy, origin_field)
        self.client = httpx.Client(
            transport=transport, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(60, connect=15, pool=15),
            headers={"Authorization": "Bearer " + token, "Accept-Encoding": "identity"})

    def close(self):
        self.client.close()

    @contextmanager
    def response(self, method, path, **kwargs):
        if not re.fullmatch(r"/[A-Za-z0-9/_-]+", path) or ".." in path:
            raise ValueError("fixed API path required")
        try:
            with self.client.stream(method, self.origin + path, **kwargs) as response:
                code = response.status_code
                if 300 <= code < 400 or code in (401, 403):
                    raise AuthenticationRequired("check your key, membership and current terms")
                if code == 429:
                    error = QuotaWait("submission capacity unavailable; resume later")
                    try:
                        error.retry_after = min(3600, max(1, int(response.headers.get("Retry-After", "60"))))
                    except ValueError:
                        error.retry_after = 60
                    raise error
                if code == 409:
                    raise self.conflict_error(response)
                if code >= 500:
                    raise Pending("service unavailable; resume this run")
                if not 200 <= code < 300:
                    raise ReconciliationRequired("request was not accepted; check this run")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise ValueError("encoded API response rejected")
                yield response
        except httpx.HTTPError:
            raise Pending("connection interrupted; resume this run") from None

    def conflict_error(self, response):
        if self.origin == self.policy.gateway_origin:
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=4096):
                if len(data) + len(chunk) > 16384:
                    return ReconciliationRequired("gateway error exceeds bound")
                data.extend(chunk)
            try:
                name = strict_json(data).get("error")
            except (ValueError, AttributeError):
                name = None
            error_type = {"pending": Pending, "quota_wait": QuotaWait,
                "identity_mismatch": IdentityMismatch, "artifact_mismatch": ArtifactMismatch,
                "stopped": Stopped}.get(name, ReconciliationRequired)
            return error_type("gateway run is paused; check its saved status")
        return ReconciliationRequired("saved request conflicts; do not start another run")

    def json(self, method, path, **kwargs):
        with self.response(method, path, **kwargs) as response:
            data = bytearray()
            for chunk in response.iter_bytes(chunk_size=65536):
                if len(data) + len(chunk) > 1 << 20:
                    raise ValueError("API response exceeds limit")
                data.extend(chunk)
            try:
                value = strict_json(data)
            except (TypeError, ValueError):
                raise ValueError("invalid API response") from None
            if type(value) is not dict:
                raise ValueError("API object response required")
            return value

    def multipart(self, path, file_path, *, field, filename, fields, expected_hash,
                  expected_bytes, maximum, request_key=None):
        if request_key is not None and not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_key):
            raise ValueError("invalid request key")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", filename):
            raise ValueError("invalid upload filename")
        # Stable boundary makes retries byte-identical, including multipart framing.
        boundary = "ve-submit-" + hashlib.sha256((request_key or expected_hash).encode()).hexdigest()
        prefix = bytearray()
        for key, value in sorted(fields.items()):
            if not re.fullmatch(r"[a-z_]+", key) or type(value) is not str or len(value.encode()) > 16384:
                raise ValueError("invalid multipart field")
            prefix.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode())
        prefix.extend((f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"'
                       '\r\nContent-Type: application/octet-stream\r\n\r\n').encode())
        suffix = f"\r\n--{boundary}--\r\n".encode()
        with secure.regular(file_path, maximum=maximum) as (stream, info):
            if info.st_size != expected_bytes or secure.digest(stream) != expected_hash:
                raise ArtifactMismatch("frozen upload bytes changed")
            stream.seek(0)
            def body():
                yield bytes(prefix)
                remaining = info.st_size
                while remaining:
                    chunk = stream.read(min(1 << 20, remaining))
                    if not chunk:
                        raise ArtifactMismatch("frozen upload truncated")
                    remaining -= len(chunk)
                    yield chunk
                yield suffix
            headers = {"Content-Type": f"multipart/form-data; boundary={boundary}",
                       "Content-Length": str(len(prefix) + info.st_size + len(suffix))}
            if request_key:
                headers["Idempotency-Key"] = request_key
            return self.json("POST", path, content=body(), headers=headers)

    def download(self, path, destination, sha256, size):
        destination = Path(destination)
        secure.directory(destination.parent)
        if os.path.lexists(destination):
            raise FileExistsError("download destination already exists")
        temporary = destination.parent / ("." + uuid.uuid4().hex)
        try:
            with self.response("GET", path) as response:
                if (response.headers.get("X-Artifact-SHA256") != sha256
                        or response.headers.get("Content-Length") != str(size)):
                    raise ArtifactMismatch("download manifest differs")
                fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                count, digest = 0, hashlib.sha256()
                with os.fdopen(fd, "wb") as output:
                    for chunk in response.iter_bytes(chunk_size=65536):
                        count += len(chunk)
                        if count > size:
                            raise ArtifactMismatch("download exceeds manifest")
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if count != size or digest.hexdigest() != sha256:
                    raise ArtifactMismatch("download bytes differ")
            if os.path.lexists(destination):
                raise FileExistsError("download destination already exists")
            # The caller holds the run lock. Rename keeps the verified file
            # single-linked even if the process stops before directory fsync.
            os.rename(temporary, destination)
            dfd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(dfd)
            finally:
                os.close(dfd)
        finally:
            temporary.unlink(missing_ok=True)
