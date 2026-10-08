"""Participant consent and private local preparation for the optional staging flow."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import sys
from types import SimpleNamespace

from . import secure
from .secrets import SecretFileError, YUKON_API_TOKEN, VE_API_KEY, configured_values, has_secret, prompt_secret
from .contracts import (canonical_sha256, identifier, strict_json, IdentityMismatch,
    AuthenticationRequired, QuotaWait, Pending, ReconciliationRequired)
from .envelope import TrackEnvelopeContext, BoardEnvelopeContext, PACKAGE_OVERHEAD, context_track, package_size
from .flow import preflight
from .hosted import (HostedConflict, HostedYukonClient, _read_request, _write_request,
    freeze_hosted_inputs, public_recipient, require_saved_policy, select_installed_policy)
from .ve import VEClient


class PrivateArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid command arguments. Enter keys only at the private prompt.\n")


def _confirm(context):
    print(f"VE member: {context.ve_member_id}\nBoard: {context.board_key}\nModel: {context.model}")
    evidence = "no agent evidence" if context_track(context) == "human" else "its authoring evidence"
    return input(f"Generate this frozen model and authorize one VE prediction upload with {evidence}? [y/N] ").strip().lower() in ("y", "yes")


def _upload_wait(seconds):
    print(f"Waiting for upload capacity; retrying the saved request in {seconds} seconds.", file=sys.stderr, flush=True)


def _identity(yukon, ve, policy, board_binding=None):
    account = identifier(yukon.me()["account"]["id"])
    member = ve.me()
    binding = dict(yukon_account_id=account, environment="staging", board_key=policy.board_key,
        track_id=policy.track_id, ve_team_id=policy.ve_team_id, ve_member_id=member.get("member_id"))
    # This links the two entered keys for local preparation only. Trusted preflight
    # independently requires the signed operator mapping before any generation.
    gateway = SimpleNamespace(policy=policy, me=lambda: binding)
    result = preflight(None, yukon, ve, gateway, board_binding=board_binding)
    if result["utc_day"] != datetime.now(timezone.utc).date().isoformat():
        raise ValueError("current VE quota day required")
    return result


def main(argv=None):
    parser = PrivateArgumentParser(description="Authorize one encrypted VE staging submission.")
    commands = parser.add_subparsers(dest="command", required=True)
    start = commands.add_parser("start", help="freeze, authorize and submit one run")
    start.add_argument("--checkout", type=Path, default=Path.cwd())
    start.add_argument("--board", help="installed scientific board name")
    start.add_argument("--note", type=Path, required=True)
    start.add_argument("--model", required=True)
    start.add_argument("--agent-framework")
    start.add_argument("--agent-model")
    start.add_argument("--evidence", action="append", default=[], metavar="KIND=PATH")
    for command in ("submit", "status", "revoke"):
        commands.add_parser(command).add_argument("run_id")
    args = parser.parse_args(argv)
    try:
        file_values = configured_values()
        # start still confirms the upload in the terminal. Other commands only need a terminal when a key is missing.
        needs_terminal = args.command == "start" or not has_secret(YUKON_API_TOKEN, os.environ, file_values)
        if needs_terminal and (not sys.stdin.isatty() or not sys.stderr.isatty()):
            raise ValueError("private interactive terminal required")
        base = Path.home().resolve() / ".local/state/ve-submit-hosted"
        config_path = Path.home().resolve() / ".config/ve-submit"
        context = None
        if args.command == "start":
            board = args.board
        else:
            root = base / identifier(args.run_id)
            _, request, context, _, _ = _read_request(root)
            board = context.scientific_track if type(context) is BoardEnvelopeContext else None
        try:
            policy_path, policy = select_installed_policy(config_path, board=board, context=context)
        except (OSError, ValueError):
            print("Client policy is missing, unsafe, or invalid, or the selected board is not installed. "
                  "Use the official release installer, choose an installed --board, "
                  "or contact the organizer.", file=sys.stderr)
            return 2
        if args.command != "start":
            require_saved_policy(policy, request, context)
        public = public_recipient(policy)
        if args.command == "start" and policy_path.is_relative_to(args.checkout.resolve()):
            raise ValueError("install trusted policy outside candidate source")
        with ExitStack() as resources:
            yukon = HostedYukonClient(prompt_secret("Your Yukon API key", YUKON_API_TOKEN, os.environ, file_values), policy)
            resources.callback(yukon.close)
            if args.command == "start":
                config = yukon.check_config()
                evidence = []
                for entry in args.evidence:
                    kind, separator, path = entry.partition("=")
                    if not separator:
                        raise ValueError("KIND=PATH evidence required")
                    evidence.append((kind, Path(path)))
                note = secure.read(args.note.absolute(), 16384, private=False).decode()
                root, frozen, items = freeze_hosted_inputs(args.checkout, base, evidence, binding=policy.binding)
                if sum(i["bytes"] for i, _ in items) + PACKAGE_OVERHEAD > config["maxBytes"]:
                    raise ValueError("package exceeds configured size limit")
                key = prompt_secret("Your personal VE API key", VE_API_KEY, os.environ, file_values)
                ve = VEClient(key, policy.pilot, binding=policy.binding); resources.callback(ve.close)
                identity = _identity(yukon, ve, policy.pilot, policy.binding)
                cls = BoardEnvelopeContext if policy.binding else TrackEnvelopeContext
                extra = dict(scientific_track=policy.binding.name, binding_sha256=policy.binding.digest) if policy.binding else {}
                context = cls(version=5 if policy.binding else 4, environment="staging", key_id=policy.key_id,
                    yukon_account_id=identity["account"], benchmark_id=policy.pilot.track_id,
                    client_run_id=root.name, archive_sha256=frozen.archive_sha256,
                    source_manifest_sha256=frozen.source_manifest_sha256, ve_member_id=identity["member"],
                    ve_team_id=policy.pilot.ve_team_id, board_key=policy.pilot.board_key,
                    expires_at=(datetime.now(timezone.utc)+timedelta(hours=24)).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
                    uploads_authorized=1, evidence_manifest_sha256=canonical_sha256([d for d, _ in items]),
                    model=args.model, track=identity["track"], agent_framework=args.agent_framework,
                    agent_model=args.agent_model, **extra)
                package_size(context, items)
                yukon.check_recipient(context, policy.recipient_fingerprint)
                if not _confirm(context):
                    return 2
                _write_request(root, context, key, public, note, items, binding=policy.binding)
                del key
                print("Saved encrypted authorization: " + context.client_run_id)
                print("Retry this saved request if interrupted: ve-submit hosted submit " + context.client_run_id, flush=True)
                if _identity(yukon, ve, policy.pilot, policy.binding) != identity:
                    raise IdentityMismatch("live submission identity changed")
                receipt = yukon.submit(root, on_wait=_upload_wait)
                print("Yukon submission accepted: " + receipt["submission"]["id"])
                return 0
            if args.command == "submit":
                receipt = yukon.submit(root, on_wait=_upload_wait)
                print("Yukon submission accepted: " + receipt["submission"]["id"])
            else:
                # Never replay an admission POST while trying to revoke. A missing
                # acknowledgment requires the operator to reconcile its submission ID.
                submission_id = identifier(strict_json(secure.read(root / "receipt.json", 4096))["submission"]["id"])
                if args.command == "revoke":
                    yukon.revoke(submission_id)
                    print("Future package delivery revoked. Existing uploads may need manual reconciliation.")
                else:
                    yukon.status(submission_id)
                    print("Yukon submission: " + submission_id)
                    print("Expired authorization or uncertain uploads require operator reconciliation.")
        return 0
    except SecretFileError as error:
        print(str(error), file=sys.stderr)
        return 2
    except HostedConflict as error:
        print("This candidate already exists. Use its saved original request or request manual reconciliation.", file=sys.stderr)
        if error.existing_submission_id:
            print("Existing submission: " + error.existing_submission_id, file=sys.stderr)
        return 2
    except AuthenticationRequired:
        print("Authentication or access check failed. Check your personal staging keys, "
              "contest membership, and accepted VE terms. Keep any saved request.", file=sys.stderr)
        return 2
    except IdentityMismatch:
        print("Your account, board, or installed configuration does not match. "
              "Contact the organizer and keep the original saved request.", file=sys.stderr)
        return 2
    except QuotaWait:
        print("Submission capacity or quota is unavailable. Keep the saved request "
              "and retry it later; do not create another submission.", file=sys.stderr)
        return 2
    except (Pending, ReconciliationRequired):
        print("Submission status is uncertain or the service is unavailable. Keep the saved "
              "request for exact retry or contact the organizer before submitting again.", file=sys.stderr)
        return 2
    except (Exception, KeyboardInterrupt):
        print("Unable to continue safely. Keep the saved request for exact retry or operator reconciliation.", file=sys.stderr)
        return 2
