from .http import HTTP
from . import secure
from .contracts import identifier


class YukonClient(HTTP):
    def __init__(self, token, policy, **kwargs):
        super().__init__(token, policy, "yukon_origin", **kwargs)

    def me(self):
        return self.json("GET", "/api/me")

    def submit(self, archive, note, idempotency_key):
        with secure.regular(archive, maximum=64 << 20) as (stream, info):
            digest, size = secure.digest(stream), info.st_size
        value = self.multipart(
            f"/api/benchmarks/{self.policy.track_id}/submissions", archive,
            field="archive", filename="submission.tar.gz", fields={"note": note},
            expected_hash=digest, expected_bytes=size, maximum=64 << 20,
            request_key=idempotency_key)
        identifier(value.get("submission", {}).get("id"))
        return {"id": value["submission"]["id"]}

    def submission(self, submission_id):
        return self.json("GET", f"/api/submissions/{identifier(submission_id)}")["submission"]
