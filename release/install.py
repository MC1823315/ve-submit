#!/usr/bin/env python3
"""Install an official ve-submit bundle. Requires CPython 3.12 on macOS/Linux."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import uuid


class InstallError(ValueError):
    pass


def read_file(path, limit=64 << 20):
    path = Path(path)
    if path.parent.resolve(strict=True) != path.parent:
        raise InstallError("Refusing a redirected bundle or installation path.")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise InstallError("A required release file is missing or unsafe.") from error
    with os.fdopen(fd, "rb") as source:
        info = os.fstat(source.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.getuid() or info.st_mode & 0o022
                or info.st_size > limit):
            raise InstallError("Release files must be owned by you and not writable by others.")
        data = source.read(limit + 1)
        if len(data) > limit:
            raise InstallError("Release file is too large.")
        return data


def json_object(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise InstallError("Duplicate release manifest field.")
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def verify_bundle(bundle):
    bundle = Path(bundle).absolute()
    raw = read_file(bundle / "release.json", 16384)
    manifest = json_object(raw)
    fields = {"schema_version", "release", "python", "source_commit", "wheel", "files"}
    if (type(manifest) is not dict or set(manifest) != fields
            or type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1
            or manifest["python"] != "3.12"):
        raise InstallError("Unsupported release manifest.")
    patterns = {"release": r"v[0-9][A-Za-z0-9._-]{0,78}",
                "source_commit": r"[0-9a-f]{40}",
                "wheel": r"ve_submit-[0-9][A-Za-z0-9.!+_]*-py3-none-any\.whl"}
    for field, pattern in patterns.items():
        if type(manifest[field]) is not str or not re.fullmatch(pattern, manifest[field]):
            raise InstallError("Invalid release identity.")
    names = {manifest["wheel"], "requirements.lock", "hosted-policy.json", "install.py", "README.md"}
    if type(manifest["files"]) is not dict or set(manifest["files"]) not in (names, names | {"legacy-policy.json"}):
        raise InstallError("Unexpected release contents.")
    payload = {}
    for name, digest in manifest["files"].items():
        if type(digest) is not str or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise InstallError("Invalid release checksum.")
        payload[name] = read_file(bundle / name)
        if hashlib.sha256(payload[name]).hexdigest() != digest:
            raise InstallError("Release checksum failed. Download the official bundle again.")
    payload["release.json"] = raw
    return manifest, payload


def safe_directory(path, *, create=False):
    path = Path(path).absolute()
    if not path.exists() and not path.is_symlink():
        safe_directory(path.parent, create=create)
        if not create:
            return path
        path.mkdir(mode=0o700)
    info = path.lstat()
    sticky_root = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
    if (not stat.S_ISDIR(info.st_mode) or path.resolve(strict=True) != path
            or info.st_uid not in (0, os.getuid()) or (info.st_mode & 0o022 and not sticky_root)):
        raise InstallError("Installation directories must not be redirected or writable by others.")
    if path.parent != path:
        safe_directory(path.parent)
    return path


def launcher_bytes(python):
    # The shell only executes this absolute trusted interpreter; candidate imports
    # and PYTHONPATH are disabled by Python isolated mode.
    return ("#!/bin/sh\nexec " + shlex.quote(str(python)) +
            ' -I -m ve_submit "$@"\n').encode()


def run(arguments, *, cwd):
    # Do not pass submission keys or Python/pip environment overrides to installers.
    environment = {key: os.environ[key] for key in ("HOME", "TMPDIR", "LANG", "LC_ALL")
                   if key in os.environ}
    environment["PATH"] = os.defpath
    result = subprocess.run(arguments, cwd=cwd, env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise InstallError("Installation check failed. Verify Python 3.12 and access to PyPI, "
                           "then rerun setup. Your previous installation was kept.")
    return result.stdout


def write_new(path, data, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def compatible_policy(previous, proposed):
    old, new = json_object(previous), json_object(proposed)
    for value in (old, new):
        legacy_fields = {"pilot", "key_id", "public_key", "recipient_fingerprint", "binding"}
        if type(value) is dict and set(value) == legacy_fields and value["binding"] is None:
            value.pop("binding")
    canonical = lambda value: json.dumps(value, sort_keys=True, separators=(",", ":"))
    if canonical(old) == canonical(new):
        return True
    public_fields = {"version", "catalog", "key_id", "public_key", "recipient_fingerprint"}
    if (type(old) is not dict or type(new) is not dict or set(old) != public_fields
            or set(new) != public_fields or old["version"] != 2 or new["version"] != 2
            or any(old[key] != new[key] for key in public_fields - {"catalog"})):
        return False
    before, after = old["catalog"], new["catalog"]
    if (set(before) != {"version", "boards"} or set(after) != {"version", "boards"}
            or before["version"] != 1 or after["version"] != 1):
        return False
    # Permit only additive catalog updates. Existing authorizations keep exact
    # board bindings and recipient identity; changed bindings need operator review.
    return all(any(canonical(board) == canonical(candidate) for candidate in after["boards"])
               for board in before["boards"])


def policy_files(payload):
    primary = json_object(payload["hosted-policy.json"])
    catalog = type(primary) is dict and primary.get("version") == 2
    result = {"hosted-catalog.json" if catalog else "hosted-policy.json": payload["hosted-policy.json"]}
    if "legacy-policy.json" in payload:
        legacy = json_object(payload["legacy-policy.json"])
        if not catalog or type(legacy) is not dict or legacy.get("version") == 2:
            raise InstallError("Legacy policy can accompany only a multi-board catalog.")
        result["hosted-policy.json"] = payload["legacy-policy.json"]
    return result


def replace_file(path, data, mode=0o600):
    fd, temporary = tempfile.mkstemp(prefix=".ve-submit-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def validate_installation(destination, payload):
    python = destination / "venv/bin/python"
    if not python.is_file():
        raise InstallError("The existing release environment is damaged. No launcher was changed; "
                           "contact the organizer for recovery.")
    for name, expected in payload.items():
        if read_file(destination / name) != expected:
            raise InstallError("Installed release files differ. No launcher was changed.")
    run([str(python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "check"],
        cwd=destination)
    for name in ("hosted-policy.json", "legacy-policy.json"):
        if name not in payload:
            continue
        run([str(python), "-I", "-c",
             "import sys; from pathlib import Path; from ve_submit.hosted import load_hosted_policy; "
             "from ve_submit.contracts import strict_json; "
             "p=Path(sys.argv[1]).absolute(); v=strict_json(p.read_bytes()); "
             "load_hosted_policy(p,board=v['catalog']['boards'][0]['name']) "
             "if v.get('version')==2 else load_hosted_policy(p)", name], cwd=destination)
    run([str(python), "-I", "-m", "ve_submit", "hosted", "start", "--help"], cwd=destination)


def install(bundle, home, *, wheelhouse=None):
    if sys.version_info[:2] != (3, 12) or sys.implementation.name != "cpython" or sys.platform not in ("darwin", "linux"):
        raise InstallError("Use CPython 3.12 on macOS or Linux (Windows users can use WSL).")
    manifest, payload = verify_bundle(bundle)
    home = Path(home).absolute()
    config = home / ".config/ve-submit"
    policies = policy_files(payload)
    releases = home / ".local/share/ve-submit/releases"
    destination = releases / manifest["release"]
    bin_dir = home / ".local/bin"
    launcher = bin_dir / "ve-submit"
    for directory in (home, config, releases, bin_dir):
        safe_directory(directory)
    for name, data in policies.items():
        policy = config / name
        if policy.exists() or policy.is_symlink():
            if not compatible_policy(read_file(policy), data):
                raise InstallError("An existing policy differs. Keep it for saved submissions; "
                                   "ask the organizer for the compatible policy transition.")
    if launcher.exists() or launcher.is_symlink():
        read_file(launcher, 16384)
    if destination.exists() or destination.is_symlink():
        safe_directory(destination)
        previous_manifest = destination / "release.json"
        if previous_manifest.exists() and read_file(previous_manifest, 16384) != payload["release.json"]:
            raise InstallError("That release name already contains different files.")
        if (destination / "complete").exists() or (destination / "complete").is_symlink():
            if read_file(destination / "complete", 32) != b"verified\n":
                raise InstallError("Invalid installation completion record.")
            validate_installation(destination, payload)
        else:
            # Preserve interrupted data, then retry from the original verified bundle.
            # Never delete or execute an earlier incomplete installation.
            destination.rename(releases / (".incomplete-" + manifest["release"] + "-" + uuid.uuid4().hex))
    if not destination.exists():
        safe_directory(releases, create=True)
        # Build in the final path: venv scripts contain absolute interpreter paths.
        destination.mkdir(mode=0o700)
        try:
            for name, data in payload.items():
                write_new(destination / name, data)
            python = destination / "venv/bin/python"
            run([sys.executable, "-I", "-m", "venv", str(destination / "venv")], cwd=destination)
            pip = [str(python), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "--no-cache-dir"]
            source = (["--no-index", "--find-links", str(Path(wheelhouse).resolve(strict=True))]
                      if wheelhouse else ["--index-url", "https://pypi.org/simple"])
            run(pip + ["install", "--require-hashes", "--only-binary=:all:", "--no-deps",
                       *source, "-r", str(destination / "requirements.lock")], cwd=destination)
            run(pip + ["install", "--no-index", "--no-deps", str(destination / manifest["wheel"])],
                cwd=destination)
            validate_installation(destination, payload)
            write_new(destination / "complete", b"verified\n")
        except BaseException:
            shutil.rmtree(destination)
            raise
    safe_directory(config, create=True)
    safe_directory(bin_dir, create=True)
    # Recheck after dependency installation, before exposing the new release.
    for name, data in policies.items():
        policy = config / name
        if policy.exists() or policy.is_symlink():
            previous = read_file(policy)
            if not compatible_policy(previous, data):
                raise InstallError("Installed policy changed during setup. No launcher was changed.")
            if previous != data:
                replace_file(policy, data)
        else:
            write_new(policy, data)
    if launcher.exists() or launcher.is_symlink():
        read_file(launcher, 16384)
    replace_file(launcher, launcher_bytes(destination / "venv/bin/python"), 0o700)
    return launcher


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        launcher = install(Path(__file__).resolve().parent, Path.home().resolve())
    except (InstallError, OSError, ValueError) as error:
        # InstallError messages contain only local fixed guidance, never API data.
        print(str(error) if isinstance(error, InstallError) else
              "Setup could not finish. Check the release files and directory permissions.",
              file=sys.stderr)
        return 2
    print("Installed ve-submit. Your personal API keys will be requested only when submitting.")
    print("Run: " + shlex.quote(str(launcher)) + " hosted start --help")
    if str(launcher.parent) not in os.environ.get("PATH", "").split(os.pathsep):
        print('To use ve-submit by name, add this to your shell profile: export PATH="$HOME/.local/bin:$PATH"')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
