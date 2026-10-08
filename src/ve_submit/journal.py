"""Fsynced recovery metadata. A lock reloads current state for every update."""
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import stat
import uuid

from .contracts import (canonical, canonical_sha256, parse_intent, strict_json,
                        wire, ReconciliationRequired, Stopped)
from . import secure

MILESTONES = ("prepared", "yukon_intent", "yukon_bound", "generating",
              "artifact_ready", "downloaded", "ve_intent", "ve_bound",
              "evidence_pending", "scoring", "verified", "published")
REASONS = (None, "authentication_required", "quota_wait", "reconciliation_required",
           "generation_failed", "stopped", "pending", "artifact_mismatch", "identity_mismatch")


def nonsecret(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if (type(key) is not str or any(word in key.lower() for word in
                    ("authorization", "password", "secret", "api_key", "access_token", "refresh_token"))):
                raise ValueError("authentication data cannot enter recovery state")
            nonsecret(item)
    elif isinstance(value, list):
        for item in value:
            nonsecret(item)
    canonical(value)


class Journal:
    def __init__(self, root):
        self.root = secure.directory(root)
        self.archive = self.root / "submission.tar.gz"

    @classmethod
    def create(cls, root, intent):
        parse_intent(wire(intent))
        root = secure.directory(root, create=True)
        secure.atomic_write(root / "journal.json", canonical({
            "schema_version": wire(intent)["schema_version"], "intent": wire(intent), "milestone": "prepared",
            "stopped": False, "suspension": None, "intents": {}, "fields": {},
        }))
        return cls.open(root)

    @classmethod
    def open(cls, root):
        journal = cls(root)
        journal.read()
        return journal

    def read(self):
        value = strict_json(secure.read(self.root / "journal.json"))
        if (type(value) is not dict or set(value) != {
            "schema_version", "intent", "milestone", "stopped", "suspension", "intents", "fields"}
                or type(value["schema_version"]) is not int or value["schema_version"] not in (1, 2, 3)
                or value["milestone"] not in MILESTONES or type(value["stopped"]) is not bool
                or value["suspension"] not in REASONS or type(value["intents"]) is not dict
                or type(value["fields"]) is not dict):
            raise ValueError("invalid recovery journal")
        parsed = parse_intent(value["intent"])
        if value["schema_version"] != wire(parsed)["schema_version"]:
            raise ValueError("journal and intent versions differ")
        nonsecret(value)
        return value

    @contextmanager
    def lock(self, name="journal"):
        if name not in ("journal", "flow"):
            raise ValueError("invalid lock")
        path = self.root / f".{name}.lock"
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_mode & 0o077 or info.st_uid != os.getuid()):
                raise ValueError("unsafe recovery lock")
            fcntl.flock(fd, fcntl.LOCK_EX | (fcntl.LOCK_NB if name == "flow" else 0))
            yield
        finally:
            os.close(fd)

    def _change(self, change):
        with self.lock():
            value = self.read()
            result = change(value)
            nonsecret(value)
            secure.atomic_write(self.root / "journal.json", canonical(value), replace=True)
            return result

    def intent(self, kind, body):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,80}", kind):
            raise ValueError("invalid request kind")
        nonsecret(body)
        def change(value):
            if value["stopped"]:
                raise Stopped("run is stopped")
            existing = value["intents"].get(kind)
            digest = canonical_sha256(body)
            if existing is not None:
                if existing["body_sha256"] != digest:
                    raise ReconciliationRequired("saved request differs")
                return existing
            result = {"key": str(uuid.uuid4()), "body": body, "body_sha256": digest}
            value["intents"][kind] = result
            return result
        return self._change(change)

    def advance(self, milestone, fields):
        if milestone not in MILESTONES or type(fields) is not dict:
            raise ValueError("invalid transition")
        nonsecret(fields)
        def change(value):
            if value["stopped"]:
                raise Stopped("run is stopped")
            if MILESTONES.index(milestone) < MILESTONES.index(value["milestone"]):
                raise ReconciliationRequired("milestone cannot move backwards")
            for key, item in fields.items():
                if key in value["fields"] and value["fields"][key] != item:
                    raise ReconciliationRequired("saved field differs")
                value["fields"][key] = item
            value["milestone"] = milestone
            value["suspension"] = None
        self._change(change)

    def suspend(self, reason):
        if reason not in REASONS:
            reason = "reconciliation_required"
        self._change(lambda value: value.update(suspension=reason))

    def stop(self):
        self._change(lambda value: value.update(stopped=True, suspension="stopped"))
