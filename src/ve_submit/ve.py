from .http import HTTP
from .contracts import identifier, ReconciliationRequired
from .staging_catalog import require_board_binding


def reconcile_evidence(listing, item):
    rows = listing.get("files")
    if type(rows) is not list:
        raise ReconciliationRequired("evidence acknowledgment needs manual reconciliation")
    matches = [row for row in rows if all(row.get(k) == item[k]
               for k in ("kind", "bytes", "sha256"))
               and row.get("filename") == str(row.get("id")) + "-" + item["name"]]
    if len(matches) != 1:
        raise ReconciliationRequired("evidence acknowledgment needs manual reconciliation")
    return identifier(matches[0].get("id"), "ve")


class VEClient(HTTP):
    def __init__(self, token, policy, *, binding=None, **kwargs):
        self.board_binding = require_board_binding(policy, binding)
        super().__init__(token, policy, "ve_origin", **kwargs)

    def me(self):
        return self.json("GET", "/challenge/me")

    def quota(self):
        return self.json("GET", "/challenge/integration/quota")

    def phase(self):
        return self.json("GET", "/challenge/phase")

    def upload(self, path, metadata, idempotency_key):
        allowed = {"model", "agent_framework", "agent_model", "architecture", "sha256", "bytes"}
        if set(metadata) - allowed:
            raise ValueError("unexpected upload metadata")
        fields = {k: v for k, v in metadata.items() if k not in ("sha256", "bytes")}
        fields.update(self.board_binding.official_contract["upload"] if self.board_binding is not None else
                      dict(task="T2", setting="heart", mode="interp", format_only="false"))
        response = self.multipart(
            "/challenge/submissions", path, field="file", filename="prediction.h5ad", fields=fields,
            expected_hash=metadata["sha256"], expected_bytes=metadata["bytes"],
            maximum=self.policy.max_artifact_bytes, request_key=idempotency_key)
        identifier(response.get("id"), "ve")
        # Save this ID in the journal before any subsequent readback. The detail
        # response, including SHA, is verified even after an idempotent replay.
        return {"id": response["id"]}

    def detail(self, submission_id):
        return self.json("GET", f"/challenge/submissions/{identifier(submission_id, 've')}")

    def evidence(self, submission_id):
        return self.json("GET", f"/challenge/teams/evidence/{identifier(submission_id, 've')}")

    def upload_evidence(self, submission_id, item, path):
        response = self.multipart(
            f"/challenge/teams/evidence/{identifier(submission_id, 've')}", path,
            field="file", filename=item["name"], fields={"kind": item["kind"]},
            expected_hash=item["sha256"], expected_bytes=item["bytes"], maximum=64 << 20)
        return {"id": identifier(response.get("id"), "ve")}

    def partner(self, cursor=None):
        return _partner_page(self, cursor)


class VEPartnerClient(HTTP):
    """Team-scoped service reads; never used for participant uploads."""
    def __init__(self, token, policy, **kwargs):
        super().__init__(token, policy, "ve_origin", **kwargs)

    def detail(self, submission_id):
        return self.json("GET", f"/challenge/integration/partner/submissions/{identifier(submission_id, 've')}")

    def partner(self, cursor=None):
        return _partner_page(self, cursor)


def _partner_page(client, cursor):
    params = {"limit": 100}
    if cursor is not None:
        if type(cursor) is not str or not 0 < len(cursor) <= 512:
            raise ValueError("invalid export cursor")
        params["cursor"] = cursor
    return client.json("GET", "/challenge/integration/partner/submissions", params=params)
