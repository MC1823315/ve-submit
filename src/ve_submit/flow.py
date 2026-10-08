"""One authorized, frozen run. Every external mutation has a durable intent."""
from dataclasses import asdict
import hashlib
from pathlib import Path
import re
import uuid

from . import secure
from .contracts import (RunIntent, TrackRunIntent, BoardBaselineIntent, intent_track, SubmissionError, AuthenticationRequired, IdentityMismatch,
    ArtifactMismatch, Pending, QuotaWait, ReconciliationRequired, Stopped, GenerationFailed, canonical,
    canonical_sha256, identifier, parse_intent, parse_manifest, strict_json, wire)
from .journal import Journal, MILESTONES
from .package import freeze_package
from .ve import reconcile_evidence
from .policy import BASELINE_OWNER, baseline_id
from .track import require_track, validate_metadata, evidence_observation, validate_evidence_observation
from .staging_catalog import require_board_binding


def preflight(intent, yukon, ve, gateway, *, capacity=True, uploads_pending=True, expected_track=None, board_binding=None):
    policy = gateway.policy
    require_board_binding(policy, board_binding)
    contract = board_binding.official_contract if board_binding is not None else None
    account = identifier(yukon.me()["account"]["id"])
    binding, member, phase, quota = gateway.me(), ve.me(), ve.phase(), ve.quota()
    if (binding.get("yukon_account_id") != account or binding.get("environment") != "staging"
            or binding.get("board_key") != policy.board_key or binding.get("track_id") != policy.track_id
            or binding.get("ve_team_id") != policy.ve_team_id
            or member.get("member_id") != binding.get("ve_member_id")
            or member.get("team_id") != policy.ve_team_id
            or (intent is not None and intent.ve_member_id != member.get("member_id"))):
        raise IdentityMismatch("use the linked VE and Yukon accounts")
    identifier(member.get("member_id"), "member")
    if (member.get("registered") is not True or member.get("stale_consents") != []
            or member.get("partner_kind") != "eigenlab_yukon"):
        raise AuthenticationRequired("register and accept current VE rules and terms")
    track = require_track(member.get("track"))
    bound = (intent_track(intent) if type(intent) in (RunIntent, TrackRunIntent, BoardBaselineIntent)
             else expected_track if expected_track is not None else "agent" if intent is not None else None)
    if ((expected_track is not None and require_track(expected_track) != track)
            or (bound is not None and bound != track)):
        raise IdentityMismatch("VE team track changed; resume only the original authorization")
    phase_name, split = (contract["phase"], contract["split"]) if contract else ("p2", "val")
    if (phase.get("phase") != phase_name or phase.get("split") != split
            or (uploads_pending and phase.get("accepts_submissions") is not True)
            or phase.get("shows_scores") is not True):
        raise Pending("the configured staging board is not open")
    scope = quota.get("quota_scope")
    if (quota.get("team_id") != policy.ve_team_id or scope not in ("member_subtask", "member_task")
            or member.get("quota_scope") != scope or quota.get("phase") != phase_name
            or (contract is not None and scope != contract["quota"]["scope"])):
        raise IdentityMismatch("quota contract differs")
    # VE's current member/subtask contract uses slash-separated quota keys.
    # Only explicit legacy member_task responses may select the task-wide bucket.
    quota_key = contract["quota"]["key"] if contract else {"T2:heart:val_interp": "T2/heart/interp"}.get(policy.board_key)
    if quota_key is None:
        raise IdentityMismatch("quota board differs")
    limits = quota.get("daily", {}).get(quota_key if contract or scope == "member_subtask" else "T2", {})
    if any(type(limits.get(k)) is not int or limits[k] < 0 for k in ("used", "limit", "uploads", "upload_limit")):
        raise ValueError("invalid quota response")
    if capacity and (limits["used"] >= limits["limit"] or limits["uploads"] >= limits["upload_limit"]):
        raise QuotaWait("your selected submission allowance is unavailable today; resume after reset")
    return {"account": account, "member": member["member_id"], "utc_day": quota["utc_day"], "track": track}


def prepare(checkout, state_root, note, metadata, evidence, yukon, ve, gateway, clock, confirm, *, baseline=False,
            board_binding=None):
    if board_binding is not None and baseline is not True:
        raise ValueError("board journals are operator baseline only")
    identity = preflight(None, yukon, ve, gateway, board_binding=board_binding)
    if baseline:
        if identity["account"] != BASELINE_OWNER:
            raise IdentityMismatch("baseline operator account required")
        baseline_id(gateway.policy)
    validate_metadata(identity["track"], metadata)
    if type(note) is not str or not 1 <= len(note.encode()) <= 16384:
        raise ValueError("bounded scientific model note required")
    if identity["track"] == "human" and evidence:
        raise ValueError("human submission contains agent evidence")
    day = clock.now().date().isoformat()
    if identity["utc_day"] != day:
        raise Pending("local clock and VE quota day differ")
    run_id = str(uuid.uuid4())
    base = secure.directory(state_root, create=True)
    if base.is_relative_to(Path(checkout).resolve()):
        raise ValueError("recovery state must be outside the checkout")
    root = secure.directory(base / run_id, create=True)
    frozen = freeze_package(checkout, root, solution_root=board_binding.solution_root if board_binding else "solution")
    items = []
    for index, (kind, path) in enumerate(evidence):
        path = Path(path).absolute()
        if kind not in ("trajectory", "prompts", "harness", "other") or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", path.name):
            raise ValueError("invalid evidence selection")
        payload = secure.read(path, 64 << 20, private=False)
        if not payload:
            raise ValueError("evidence must be nonempty")
        saved = f"evidence-{index}"
        secure.atomic_write(root / saved, payload)
        items.append(dict(kind=kind, name=path.name, sha256=hashlib.sha256(payload).hexdigest(),
                          bytes=len(payload), local=saved))
    if identity["track"] == "agent" and (len(items) > 8 or len({i["kind"] for i in items}) < 2
            or "trajectory" not in {i["kind"] for i in items}):
        raise ValueError("select your trajectory and at least one other evidence kind")
    secure.atomic_write(root / "note.txt", note.encode())
    secure.atomic_write(root / "evidence.json", canonical(items))
    secure.atomic_write(root / "metadata.json", canonical(metadata))
    intent = TrackRunIntent(run_id, frozen.source_manifest_sha256, frozen.archive_sha256,
                       hashlib.sha256(note.encode()).hexdigest(), canonical_sha256(items),
                       identity["member"], gateway.policy.board_key, 1, identity["track"])
    if board_binding is not None:
        intent = BoardBaselineIntent(**asdict(intent), benchmark_id=board_binding.policy.track_id,
            scientific_track=board_binding.name, binding_sha256=board_binding.digest)
    journal = Journal.create(root, intent)
    journal.advance("prepared", {"account": identity["account"], "policy_sha256": canonical_sha256(asdict(gateway.policy)),
                                "metadata_sha256": canonical_sha256(metadata), "baseline": baseline})
    summary = dict(track=identity["track"], run_id=run_id, member_id=identity["member"], board=gateway.policy.board_key,
                   model=metadata["model"], evidence=[i["name"] for i in items],
                   action="submit_existing_baseline" if baseline else "generate_and_submit", utc_day=day)
    if not confirm(summary):
        journal.stop()
        raise Stopped("run not authorized")
    journal.advance("prepared", {"consent_" + day: True})
    return journal


def mark(journal, milestone, **fields):
    current = journal.read()["milestone"]
    journal.advance(max((current, milestone), key=MILESTONES.index), fields)


def _frozen(journal):
    state = journal.read()
    intent = parse_intent(state["intent"])
    note = secure.read(journal.root / "note.txt", 16384)
    items = strict_json(secure.read(journal.root / "evidence.json", 16384))
    metadata = strict_json(secure.read(journal.root / "metadata.json", 16384))
    with secure.regular(journal.archive, maximum=64 << 20) as (stream, _):
        if secure.digest(stream) != intent.archive_sha256:
            raise ArtifactMismatch("frozen source archive changed")
    if (hashlib.sha256(note).hexdigest() != intent.note_sha256
            or canonical_sha256(items) != intent.evidence_manifest_sha256
            or canonical_sha256(metadata) != state["fields"]["metadata_sha256"]):
        raise ArtifactMismatch("frozen run metadata changed")
    if type(intent) is RunIntent:
        # Version 1 authorized characters, not UTF-8 bytes. Never reinterpret
        # an existing authorization using the stricter version 2 rules.
        if (type(metadata) is not dict or set(metadata) != {"model", "agent_framework", "agent_model"}
                or any(type(v) is not str or not 1 <= len(v) <= 256
                       or any(ord(c) < 32 for c in v) for v in metadata.values())):
            raise ValueError("legacy model metadata required")
    else:
        validate_metadata(intent_track(intent), metadata)
    if intent_track(intent) == "human" and items != []:
        raise ArtifactMismatch("human authorization contains evidence")
    for item in items:
        if not re.fullmatch(r"evidence-[0-7]", item["local"]):
            raise ValueError("invalid evidence file")
        with secure.regular(journal.root / item["local"], maximum=64 << 20) as (stream, info):
            if secure.digest(stream) != item["sha256"] or info.st_size != item["bytes"]:
                raise ArtifactMismatch("frozen evidence changed")
    return intent, note.decode(), items, metadata


def _active(journal, gateway, run_id):
    if journal.read()["stopped"]:
        raise Stopped("run stopped")
    row = gateway.status(run_id)
    if row.get("stopped"):
        journal.stop()
        raise Stopped("run stopped")
    if row.get("status") == "generation_failed":
        raise GenerationFailed("generation failed; reconcile this existing run")
    if row.get("status") == "publication_failed":
        raise ReconciliationRequired("Yukon publication failed; reconcile this existing run")
    return row


def _evidence(journal, ve, sid, items, *, before_upload=None, track="agent", now=None):
    listing = ve.evidence(sid)
    if require_track(track) == "human":
        if items:
            raise IdentityMismatch("human authorization contains evidence")
        observation = evidence_observation(listing, sid, track, now)
        saved = journal.read()["fields"].get("evidence_contract")
        if saved is None:
            mark(journal, "ve_bound", evidence_contract=observation)
        else:
            validate_evidence_observation(saved, sid, track)
        if observation["required"] is not False or (saved is not None and saved["required"] is not False):
            raise ReconciliationRequired("accepted submission requires review")
        return
    if listing.get("submission_id") != sid or listing.get("required") is not True:
        raise ReconciliationRequired("evidence requirements differ")
    allowed = {k["id"] for k in listing["kinds"]}
    if (type(listing.get("min_kinds")) is not int or listing["min_kinds"] < 1
            or len({i["kind"] for i in items}) < listing["min_kinds"]
            or listing.get("required_kind") not in {i["kind"] for i in items}
            or any(i["kind"] not in allowed or i["bytes"] > listing["max_bytes"] for i in items)
            or sum(i["bytes"] for i in items) > listing["max_total_bytes"]):
        raise ReconciliationRequired("selected evidence does not meet current requirements")
    for index, item in enumerate(items):
        kind, ack = f"evidence-{index}", f"evidence_id_{index}"
        state = journal.read()
        if state["stopped"]:
            raise Stopped("run stopped")
        if ack in state["fields"]:
            continue
        if kind in state["intents"]:
            eid = reconcile_evidence(ve.evidence(sid), item)
        else:
            if before_upload is not None:
                before_upload()
            journal.intent(kind, {"submission_id": sid, "item": item})
            eid = ve.upload_evidence(sid, item, journal.root / item["local"])["id"]
        identifier(eid, "ve")
        mark(journal, "evidence_pending", **{ack: eid})


def run_until_pause(journal, yukon, ve, gateway, clock, confirm, *, reconcile_submission=None):
    try:
        with journal.lock("flow"):
            state = journal.read()
            if state["stopped"] or state["milestone"] == "published":
                return state
            intent, note, items, metadata = _frozen(journal)
            run_id = intent.client_run_id
            if canonical_sha256(asdict(gateway.policy)) != state["fields"]["policy_sha256"]:
                raise IdentityMismatch("installed policy changed; reconcile this run")
            reconciling = (reconcile_submission is not None and "ve" in state["intents"]
                           and "ve_id" not in state["fields"])
            observed = preflight(intent, yukon, ve, gateway,
                capacity=state["milestone"] == "prepared" and "ve" not in state["intents"],
                uploads_pending=not reconciling and ("ve_id" not in state["fields"] or any(
                    f"evidence_id_{index}" not in state["fields"] for index in range(len(items)))))
            if observed["account"] != state["fields"]["account"]:
                raise IdentityMismatch("Yukon account differs")
            if not any(k.startswith("consent_") for k in state["fields"]):
                raise Stopped("run was not authorized")
            def authorize(*, capacity=False, uploads_pending=False):
                fresh = preflight(intent, yukon, ve, gateway, capacity=capacity, uploads_pending=uploads_pending)
                if fresh["account"] != state["fields"]["account"]:
                    raise IdentityMismatch("Yukon account differs")
                if fresh["utc_day"] != clock.now().date().isoformat():
                    raise Pending("quota day differs")
            create = journal.intent("create", state["intent"])
            baseline = state["fields"].get("baseline", False)
            if baseline and observed["account"] != BASELINE_OWNER:
                raise IdentityMismatch("baseline operator account required")
            authorize()
            created = (gateway.create_baseline(intent, create["key"]) if baseline
                       else gateway.create(intent, create["key"]))
            if created.get("stopped"):
                journal.stop()
                raise Stopped("run stopped")
            if baseline:
                sid = baseline_id(gateway.policy)
                if created.get("yukon_id") != sid:
                    raise IdentityMismatch("baseline generation identity differs")
                mark(journal, "yukon_bound", yukon_id=sid)
            elif "yukon_id" not in state["fields"]:
                request = journal.intent("yukon", {"archive_sha256": intent.archive_sha256,
                                                  "note_sha256": intent.note_sha256})
                mark(journal, "yukon_intent")
                _active(journal, gateway, run_id)
                authorize(capacity=True, uploads_pending=True)
                sid = yukon.submit(journal.archive, note, request["key"])["id"]
                identifier(sid)
                mark(journal, "yukon_bound", yukon_id=sid)
            state = journal.read()
            if not baseline:
                authorize()
                gateway.attach(run_id, state["fields"]["yukon_id"])
            mark(journal, "generating")
            if "manifest" not in state["fields"]:
                for attempt in range(8):
                    row = _active(journal, gateway, run_id)
                    if row.get("manifest") is not None:
                        manifest = parse_manifest(row["manifest"])
                        if (manifest.run_id != run_id or manifest.yukon_account_id != observed["account"]
                                or manifest.yukon_submission_id != state["fields"]["yukon_id"]
                                or manifest.source_manifest_sha256 != intent.source_manifest_sha256
                                or manifest.ve_member_id != intent.ve_member_id
                                or manifest.ve_team_id != gateway.policy.ve_team_id
                                or manifest.artifact_bytes > gateway.policy.max_artifact_bytes):
                            raise ArtifactMismatch("generated artifact identity differs")
                        mark(journal, "artifact_ready", manifest=wire(manifest))
                        break
                    if row.get("status") in ("failed", "quarantined", "canceled"):
                        raise ReconciliationRequired("generation did not produce a usable artifact")
                    clock.sleep(min(30, 2**attempt))
                else:
                    raise Pending("artifact is still generating; resume this run")
            manifest = parse_manifest(journal.read()["fields"]["manifest"])
            path = journal.root / "prediction.h5ad"
            if not path.exists():
                gateway.download(run_id, path, manifest)
            with secure.regular(path, maximum=gateway.policy.max_artifact_bytes) as (stream, info):
                if info.st_size != manifest.artifact_bytes or secure.digest(stream) != manifest.artifact_sha256:
                    raise ArtifactMismatch("downloaded artifact changed")
            mark(journal, "downloaded")
            state = journal.read()
            if "ve_id" not in state["fields"]:
                fresh = preflight(intent, yukon, ve, gateway, capacity="ve" not in state["intents"],
                                  uploads_pending="ve" not in state["intents"])
                day = clock.now().date().isoformat()
                if fresh["utc_day"] != day:
                    raise Pending("quota day and local clock differ")
                if "consent_" + day not in state["fields"]:
                    if not confirm(dict(action="submit_existing", run_id=run_id, member_id=intent.ve_member_id,
                                        board=intent.board_key, utc_day=day)):
                        raise Pending("existing artifact awaits permission for this UTC day")
                    mark(journal, "downloaded", **{"consent_" + day: True})
                if "ve" in state["intents"]:
                    if reconcile_submission is None:
                        raise ReconciliationRequired("unknown upload outcome; reconcile the existing VE submission")
                    sid = identifier(reconcile_submission, "ve")
                    detail = ve.detail(sid)
                    if any(detail.get(k) != v for k, v in dict(id=sid, member_id=intent.ve_member_id,
                            sha256=manifest.artifact_sha256, bundle_key=intent.board_key,
                            bytes=manifest.artifact_bytes).items()):
                        raise ArtifactMismatch("reconciled prediction differs")
                else:
                    if reconcile_submission is not None:
                        raise ReconciliationRequired("no prior upload intent to reconcile")
                    request = journal.intent("ve", metadata | {"sha256": manifest.artifact_sha256,
                                                               "bytes": manifest.artifact_bytes})
                    _active(journal, gateway, run_id)
                    authorize(capacity=True, uploads_pending=True)
                    gateway.lease(run_id, request["key"])
                    mark(journal, "ve_intent")
                    authorize(capacity=True, uploads_pending=True)
                    sid = ve.upload(path, request["body"], request["key"])["id"]
                    identifier(sid, "ve")
                mark(journal, "ve_bound", ve_id=sid)
            sid = journal.read()["fields"]["ve_id"]
            detail = ve.detail(sid)
            if any(detail.get(k) != v for k, v in dict(id=sid, member_id=intent.ve_member_id,
                    sha256=manifest.artifact_sha256, bundle_key=intent.board_key, bytes=manifest.artifact_bytes).items()):
                raise ArtifactMismatch("VE readback differs from this artifact")
            _active(journal, gateway, run_id)
            authorize()
            _evidence(journal, ve, sid, items, track=intent_track(intent), now=clock.now(),
                      before_upload=lambda: authorize(uploads_pending=True))
            mark(journal, "scoring")
            for attempt in range(8):
                _active(journal, gateway, run_id)
                authorize()
                row = gateway.claim(run_id, sid)
                if row.get("result") is not None:
                    mark(journal, "verified", result=row["result"])
                if row.get("status") == "published":
                    mark(journal, "published")
                    return journal.read()
                clock.sleep(min(30, 2**attempt))
            raise Pending("official scoring or publication is pending; resume this run")
    except SubmissionError as error:
        journal.suspend(error.code)
    except (ValueError, KeyError, TypeError, OSError):
        journal.suspend("reconciliation_required")
    return journal.read()
