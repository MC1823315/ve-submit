"""Freeze permitted bytes without importing or running the submitted package."""
import gzip
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import tarfile

from .contracts import FrozenPackage, canonical_sha256
from .boards import require_solution_root
from . import secure

MAX_SOURCE = 64 << 20
MAX_FILES = 4096
# Includes tar headers, PAX/long-name metadata, padding and trailing data.
MAX_TAR_BYTES = MAX_SOURCE + (16 << 20)


def safe_name(name, *, solution_root="solution", directory=False):
    require_solution_root(solution_root)
    parts = PurePosixPath(name).parts
    if (not parts or parts[0] != solution_root or len(parts) < (1 if directory else 2)
            or name != "/".join(parts) or any(p.startswith(".") or p in
                ("__pycache__", "node_modules") for p in parts)
            or "\\" in name or any(ord(c) < 32 for c in name)
            or name.lower().endswith((".pem", ".key"))):
        raise ValueError("unsupported source path")
    return name


def _row(name, mode, data):
    # The trusted generation snapshot normalizes all regular files to 0444.
    # Preserve original upload modes in the archive, but bind the bytes as run.
    return {"path": name, "mode": 0o444,
            "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def freeze_package(checkout, destination, *, solution_root="solution"):
    require_solution_root(solution_root)
    checkout = Path(checkout).resolve(strict=True)
    destination = Path(destination).absolute()
    if destination.is_relative_to(checkout):
        raise ValueError("recovery state must be outside the checkout")
    secure.directory(destination, create=True)
    source = checkout / solution_root
    if source.is_symlink() or not source.is_dir():
        raise ValueError("solution directory required")
    rows, payloads, total = [], [], 0
    for folder, directories, files in os.walk(source, followlinks=False):
        for name in directories:
            entry = Path(folder) / name
            if entry.is_symlink() or name.startswith(".") or name in ("__pycache__", "node_modules"):
                raise ValueError("unsupported source directory")
        for name in files:
            path = Path(folder) / name
            relative = safe_name(path.relative_to(checkout).as_posix(), solution_root=solution_root)
            with secure.regular(path, maximum=MAX_SOURCE-total, private=False) as (stream, info):
                data = stream.read(MAX_SOURCE-total+1)
                mode = info.st_mode
            total += len(data)
            if total > MAX_SOURCE or len(rows) >= MAX_FILES:
                raise ValueError("source package exceeds bounds")
            rows.append(_row(relative, mode, data))
            payloads.append((relative, mode, data))
    if not rows:
        raise ValueError("source package is empty")
    rows.sort(key=lambda row: row["path"])
    tar_bytes = io.BytesIO()
    with tarfile.open(fileobj=tar_bytes, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, mode, data in sorted(payloads):
            info = tarfile.TarInfo(name)
            info.size = len(data); info.mode = 0o755 if mode & 0o111 else 0o644
            info.mtime = info.uid = info.gid = 0
            archive.addfile(info, io.BytesIO(data))
    raw = tar_bytes.getvalue()
    if len(raw) > MAX_TAR_BYTES:
        raise ValueError("source archive exceeds bounds")
    body = gzip.compress(raw, mtime=0)
    path = destination / "submission.tar.gz"
    secure.atomic_write(path, body)
    return FrozenPackage(path, hashlib.sha256(body).hexdigest(), canonical_sha256(rows))


def source_manifest_from_archive(path, *, solution_root="solution"):
    with secure.regular(path, maximum=MAX_SOURCE + (2 << 20), private=False) as (stream, _):
        return source_manifest_from_bytes(stream.read(MAX_SOURCE + (2 << 20) + 1), solution_root=solution_root)


def source_manifest_from_bytes(source, *, solution_root="solution"):
    require_solution_root(solution_root)
    if len(source) > MAX_SOURCE + (2 << 20):
        raise ValueError("source archive exceeds bounds")
    # Bound inflation before tarfile processes extension headers. Its metadata
    # reads then operate on a bounded BytesIO, never on an unbounded decompressor.
    raw = source
    if source.startswith(b"\x1f\x8b"):
        with gzip.GzipFile(fileobj=io.BytesIO(source), mode="rb") as compressed:
            raw = compressed.read(MAX_TAR_BYTES + 1)
    if len(raw) > MAX_TAR_BYTES:
        raise ValueError("source archive exceeds bounds")
    rows, seen, total = [], set(), 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for entry in archive:
            if entry.isdir():
                safe_name(entry.name, solution_root=solution_root, directory=True)
                continue
            name = safe_name(entry.name, solution_root=solution_root)
            if not entry.isfile() or name in seen or entry.size < 0:
                raise ValueError("invalid source archive member")
            seen.add(name); total += entry.size
            if total > MAX_SOURCE or len(seen) > MAX_FILES:
                raise ValueError("source archive exceeds bounds")
            with archive.extractfile(entry) as body:
                data = body.read(entry.size + 1)
            if len(data) != entry.size:
                raise ValueError("truncated source archive")
            rows.append(_row(name, entry.mode, data))
    if not rows:
        raise ValueError("empty source archive")
    return sorted(rows, key=lambda row: row["path"])
