"""Private regular-file operations; all paths are canonical and bounded."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import uuid

from .contracts import ArtifactMismatch


def directory(path, *, create=False):
    path = Path(path).absolute()
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.resolve(strict=True) != path:
        raise ValueError("canonical directory required")
    info = path.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o077
            or info.st_uid != os.getuid()):
        raise ValueError("private owned directory required")
    return path


@contextmanager
def regular(path, *, maximum=1 << 30, private=True):
    path = Path(path).absolute()
    if path.parent.resolve(strict=True) != path.parent:
        raise ValueError("canonical parent required")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise ValueError("regular file could not be opened safely") from None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size > maximum or info.st_uid != os.getuid()
                or (private and info.st_mode & 0o077)):
            raise ValueError("bounded owned regular file required")
        with os.fdopen(os.dup(fd), "rb") as stream:
            yield stream, info
        after = os.fstat(fd)
        bound = os.stat(path, follow_symlinks=False)
        fingerprint = lambda st: (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)
        if fingerprint(info) != fingerprint(after) or fingerprint(info) != fingerprint(bound):
            raise ArtifactMismatch("file changed during operation")
    finally:
        os.close(fd)


def read(path, maximum=1 << 20, *, private=True):
    with regular(path, maximum=maximum, private=private) as (stream, _):
        return stream.read(maximum + 1)


def read_policy(path, maximum=16384):
    """Public configuration is readable, but other users cannot replace it."""
    path = Path(path).absolute()
    for parent in path.parents:
        info = parent.lstat()
        # Root-owned sticky directories (e.g. /tmp) cannot replace an owned child.
        sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid())
                or (info.st_mode & 0o022 and not sticky_root)):
            raise ValueError("trusted policy directory required")
    with regular(path, maximum=maximum, private=False) as (stream, info):
        if info.st_mode & 0o022:
            raise ValueError("policy must not be writable by other users")
        return stream.read(maximum + 1)


def digest(stream):
    value = hashlib.sha256()
    while chunk := stream.read(1 << 20):
        value.update(chunk)
    return value.hexdigest()


def atomic_write(path, data, *, replace=False):
    path = Path(path)
    directory(path.parent)
    dfd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    name = "." + uuid.uuid4().hex
    fd = None
    try:
        if path.name in ("", ".", "..") or "/" in path.name:
            raise ValueError("invalid destination")
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=dfd)
        with os.fdopen(fd, "wb") as output:
            fd = None
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        if replace:
            if path.exists() or path.is_symlink():
                with regular(path):
                    pass
            os.replace(name, path.name, src_dir_fd=dfd, dst_dir_fd=dfd)
        else:
            os.link(name, path.name, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
            os.unlink(name, dir_fd=dfd)
        os.fsync(dfd)
    finally:
        if fd is not None:
            os.close(fd)
        try:
            os.unlink(name, dir_fd=dfd)
        except FileNotFoundError:
            pass
        os.close(dfd)
