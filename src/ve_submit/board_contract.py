"""Bounded literal staging API contracts. No endpoints or executable expressions."""
import math
import re

from .boards import scientific_track
from .contracts import canonical, integer, strict_json


def _fields(value, expected):
    if type(value) is not dict or set(value) != set(expected):
        raise ValueError("closed official contract fields required")


def _literal(value, *, maximum=128):
    if (type(value) is not str or not 1 <= len(value) <= maximum
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_:./-]*", value)):
        raise ValueError("bounded literal official value required")


def _measurement(value, *, metric=False):
    _fields(value, {"detail", "export", "minimum", "maximum"} | ({"name"} if metric else set()))
    if metric and (type(value["name"]) is not str
                   or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", value["name"])):
        raise ValueError("literal metric name required")
    for side in ("detail", "export"):
        path = value[side]
        if (type(path) is not list or not 1 <= len(path) <= 6
                or any(type(k) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", k) for k in path)):
            raise ValueError("bounded literal result path required")
    for key in ("minimum", "maximum"):
        number = value[key]
        if type(number) not in (int, float) or not math.isfinite(number) or abs(number) > 1e12:
            raise ValueError("finite bounded metric range required")
    if value["minimum"] >= value["maximum"]:
        raise ValueError("ordered metric range required")


def validate_board_contract(value, name):
    track = scientific_track(name)
    _fields(value, {"version", "board_key", "phase", "split", "upload", "identity", "quota", "headline", "metrics"})
    integer(value["version"], 1, 1)
    if value["board_key"] != track.board_key or len(canonical(value)) > 16384:
        raise ValueError("selected board contract required")
    for key in ("phase", "split"):
        _literal(value[key], maximum=64)
    # Both dictionaries are operator-observed literals. Their closed key sets
    # prevent introducing URL/file/credential fields through configuration.
    allowed = {"task", "setting", "mode", "target", "perturbation"}
    for key in ("upload", "identity"):
        row = value[key]
        if (type(row) is not dict or "task" not in row
                or set(row) - (allowed | ({"format_only"} if key == "upload" else set()))):
            raise ValueError("literal board routing required")
        for field, literal in row.items():
            if literal is None and field != "task" and key == "identity":
                continue
            _literal(literal, maximum=64)
        if row["task"] != track.board_key.split(":")[0]:
            raise ValueError("scientific task differs")
    if value["upload"].get("format_only") != "false":
        raise ValueError("scored prediction upload required")
    for key in set(value["identity"]) & set(value["upload"]):
        if value["identity"][key] != value["upload"][key]:
            raise ValueError("upload and result identity differ")
    if track.board_key.startswith("T2:"):
        _, setting, split = track.board_key.split(":")
        expected = {"task": "T2", "setting": setting, "mode": split.removeprefix("val_")}
        if (value["identity"] != expected
                or value["upload"] != expected | {"format_only": "false"}):
            raise ValueError("T2 scientific routing differs")
    _fields(value["quota"], {"scope", "key"})
    if value["quota"]["scope"] not in ("member_subtask", "member_task"):
        raise ValueError("member quota scope required")
    _literal(value["quota"]["key"])
    _measurement(value["headline"])
    metrics = value["metrics"]
    if type(metrics) is not list or len(metrics) > 16:
        raise ValueError("bounded metric inventory required")
    for metric in metrics:
        _measurement(metric, metric=True)
    for key in ("name", "detail", "export"):
        entries = [tuple(m[key]) if type(m[key]) is list else m[key] for m in metrics]
        if len(set(entries)) != len(entries):
            raise ValueError("duplicate metric alias")
    return strict_json(canonical(value))


def measurement(row, field, side):
    value = row
    for key in field[side]:
        if type(value) is not dict or key not in value:
            raise ValueError("official measurement missing")
        value = value[key]
    if (type(value) not in (int, float) or not math.isfinite(value)
            or not field["minimum"] <= value <= field["maximum"]):
        raise ValueError("official measurement outside installed range")
    return value


def validate_board_metrics(metrics, binding):
    fields = binding.official_contract["metrics"]
    if type(metrics) is not dict or set(metrics) != {f["name"] for f in fields}:
        raise ValueError("selected official metrics required")
    for field in fields:
        measurement({field["name"]: metrics[field["name"]]},
                    field | {"detail": [field["name"]]}, "detail")
    return dict(metrics)


def normalize_board_metrics(detail, exported, binding):
    from .contracts import IdentityMismatch
    fields = binding.official_contract["metrics"]
    a = {f["name"]: measurement(detail, f, "detail") for f in fields}
    b = {f["name"]: measurement(exported, f, "export") for f in fields}
    if a != b:
        raise IdentityMismatch("official metric reads disagree")
    return detail | {"metrics": a}, exported | {"metrics": b}
