"""Track-specific authorization rules; the live VE identity selects the track."""
from .contracts import canonical_sha256, identifier, timestamp


def require_track(value):
    if type(value) is not str or value not in ("human", "agent"):
        raise ValueError("known VE team track required")
    return value


def validate_metadata(track, value):
    keys = {"model"} if require_track(track) == "human" else {
        "model", "agent_framework", "agent_model"}
    if (type(value) is not dict or set(value) != keys
            or any(type(v) is not str or not 1 <= len(v.encode("utf-8")) <= 256
                   or any(ord(c) < 32 or ord(c) == 127 for c in v) for v in value.values())):
        raise ValueError("bounded track-specific model metadata required")
    return value


def validate_evidence_observation(value, submission_id, track):
    if (type(value) is not dict or set(value) != {
            "version", "submission_id", "track", "required", "observed_at", "response_sha256"}
            or type(value["version"]) is not int or value["version"] != 1
            or value["submission_id"] != identifier(submission_id, "ve")
            or value["track"] != require_track(track) or type(value["required"]) is not bool
            or timestamp(value["observed_at"]).utcoffset().total_seconds() != 0):
        raise ValueError("bound evidence contract observation required")
    identifier(value["response_sha256"], "sha256")
    return value


def evidence_observation(response, submission_id, track, now):
    if (type(response) is not dict or response.get("submission_id") != submission_id
            or type(response.get("required")) is not bool):
        raise ValueError("matching VE evidence contract required")
    value = dict(version=1, submission_id=submission_id, track=track,
                 required=response["required"], observed_at=now.isoformat(),
                 response_sha256=canonical_sha256(response))
    return validate_evidence_observation(value, submission_id, track)
