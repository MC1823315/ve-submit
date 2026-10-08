"""Public provenance of an original staging baseline; no service capabilities."""
from dataclasses import asdict
import re
from .contracts import canonical, canonical_sha256, identifier, integer, strict_json
from .policy import BoardPolicy, PilotPolicy, BASELINE_OWNER, baseline_id, parse_policy, result_ref, validate_policy

FIELDS = {
    "schema_version", "environment", "generation_policy", "generation_policy_sha256",
    "baseline_submission_id", "owner_account_id", "ve_member_id", "ve_team_id", "board_key",
    "source_sha", "source_manifest_sha256", "generation_receipt_sha256", "checkpoint_commit",
    "workflow_run_id", "workflow_run_attempt", "artifact_id", "artifact_sha256", "artifact_bytes",
    "result_ref", "result_commit", "result_path", "result_sha256", "ve_submission_id",
}


def validate_retained_baseline(value):
    if type(value) is not dict:
        raise ValueError("retained baseline object required")
    version = integer(value.get("schema_version"), 1, 2)
    if set(value) != FIELDS | ({"generation_binding"} if version == 2 else set()) or len(canonical(value)) > 32768:
        raise ValueError("bounded closed retained baseline required")
    old = parse_policy(value["generation_policy"])
    if version == 2:
        from .staging_catalog import BoardBinding
        raw = value["generation_binding"]
        if type(raw) is not dict or raw.get("retained_baseline") is not None:
            raise ValueError("original baseline binding cannot contain recursive provenance")
        binding = BoardBinding(canonical(raw))
        if type(old) is not BoardPolicy or binding.policy != old:
            raise ValueError("original baseline scientific binding differs")
    elif type(old) is not PilotPolicy:
        raise ValueError("legacy baseline policy required")
    if (value["environment"] != "staging" or value["owner_account_id"] != BASELINE_OWNER
            or value["result_ref"] != result_ref(old)
            or value["generation_policy_sha256"] != canonical_sha256(asdict(old))
            or value["baseline_submission_id"] != baseline_id(old)
            or value["source_sha"] != old.baseline_source_sha
            or value["ve_team_id"] != old.ve_team_id or value["board_key"] != old.board_key):
        raise ValueError("retained baseline scope differs")
    for key in ("source_sha", "checkpoint_commit", "result_commit"):
        identifier(value[key], "commit")
    for key in ("generation_policy_sha256", "source_manifest_sha256",
                "generation_receipt_sha256", "artifact_sha256", "result_sha256"):
        identifier(value[key], "sha256")
    for key in ("ve_team_id", "ve_submission_id"):
        identifier(value[key], "ve")
    identifier(value["ve_member_id"], "member")
    integer(value["workflow_run_attempt"], 1, 1)
    for key in ("workflow_run_id", "artifact_id"):
        integer(value[key], 1, (1 << 53) - 1)
    integer(value["artifact_bytes"], 1, old.max_artifact_bytes)
    path = value["result_path"]
    if type(path) is not str or not re.fullmatch(r"results/[0-9a-f-]{36}\.json", path):
        raise ValueError("fixed result path required")
    identifier(path[8:-5])
    return strict_json(canonical(value))


def original_policy(binding):
    return parse_policy(validate_retained_baseline(binding)["generation_policy"])


def require_compatible_policy(binding, current):
    old = original_policy(binding)
    expected, present = asdict(old), asdict(validate_policy(current))
    expected["controller_sha"] = current.controller_sha
    if type(current) is BoardPolicy:
        if type(old) is PilotPolicy and current.scientific_track != "t2-heart-interp":
            raise ValueError("legacy baseline is heart interpolation only")
        if type(old) is BoardPolicy and current.scientific_track != old.scientific_track:
            raise ValueError("original baseline board differs")
        for row in (expected, present):
            for key in ("binding_sha256", "scientific_track", "schema_version"):
                row.pop(key, None)
    if present != expected:
        raise ValueError("retained baseline policy scope differs")
    return old


def require_baseline_binding(retained, current_entry):
    """Only controller/provenance may change when retaining a generated baseline."""
    current = BoardPolicy(**current_entry["policy"], scientific_track=current_entry["name"],
                          binding_sha256=canonical_sha256(current_entry))
    old = require_compatible_policy(retained, current)
    if type(old) is BoardPolicy:
        previous = strict_json(canonical(retained["generation_binding"]))
        selected = strict_json(canonical(current_entry))
        for entry in (previous, selected):
            entry.pop("retained_baseline")
            entry["policy"].pop("controller_sha")
        if previous != selected:
            raise ValueError("retained scientific inputs or official contract changed")
    return old
