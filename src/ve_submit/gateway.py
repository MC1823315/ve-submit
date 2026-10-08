from .http import HTTP
from .contracts import identifier, parse_manifest, wire


class GatewayClient(HTTP):
    def __init__(self, token, policy, **kwargs):
        super().__init__(token, policy, "gateway_origin", **kwargs)

    def me(self):
        return self.json("GET", "/v1/me")

    def create(self, intent, request_key):
        return self.json("POST", "/v1/runs", json=wire(intent), headers={"Idempotency-Key": request_key})

    def create_baseline(self, intent, request_key):
        return self.json("POST", "/v1/baseline-runs", json=wire(intent), headers={"Idempotency-Key": request_key})

    def attach(self, run_id, submission_id):
        return self.json("PUT", f"/v1/runs/{identifier(run_id)}/yukon",
                         json={"submission_id": identifier(submission_id)})

    def status(self, run_id):
        return self.json("GET", f"/v1/runs/{identifier(run_id)}")

    def download(self, run_id, path, manifest):
        manifest = parse_manifest(wire(manifest))
        return super().download(f"/v1/runs/{identifier(run_id)}/artifact", path,
                                manifest.artifact_sha256, manifest.artifact_bytes)

    def lease(self, run_id, request_key):
        return self.json("POST", f"/v1/runs/{identifier(run_id)}/upload-lease", json={"request_key": request_key})

    def claim(self, run_id, ve_submission_id):
        return self.json("PUT", f"/v1/runs/{identifier(run_id)}/ve",
                         json={"submission_id": identifier(ve_submission_id, "ve")})

    def stop(self, run_id):
        return self.json("POST", f"/v1/runs/{identifier(run_id)}/stop", json={})
