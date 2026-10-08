"""Installed participant entrypoint. Keys are entered privately and never saved."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time

from .contracts import identifier, SubmissionError
from .secrets import SecretFileError, YUKON_API_TOKEN, VE_API_KEY, configured_values, has_secret, prompt_secret
from .flow import prepare, run_until_pause
from .gateway import GatewayClient
from .journal import Journal
from .policy import load_policy
from .ve import VEClient
from .yukon import YukonClient


class Clock:
    def now(self):
        return datetime.now(timezone.utc)
    def sleep(self, seconds):
        time.sleep(seconds)


def confirm(summary):
    if summary["action"] in ("generate_and_submit", "submit_existing_baseline"):
        print(f"Model: {summary['model']}\nBoard: {summary['board']}\nVE member: {summary['member_id']}")
        print("Evidence: " + (", ".join(summary["evidence"]) or "No agent evidence"))
        print("EigenLabs provides the compute for this run.")
        prompt = ("Submit the separately approved baseline's exact prediction and this evidence to VE? [y/N] "
                  if summary["action"] == "submit_existing_baseline"
                  else "Generate this frozen model and submit its prediction and evidence to VE? [y/N] ")
    else:
        prompt = f"Submit the existing artifact on {summary['utc_day']} UTC? [y/N] "
    if summary.get("track") == "human":
        prompt = "Authorize this prediction upload with no agent evidence? [y/N] "
    return input(prompt).strip().lower() in ("y", "yes")


def show(state):
    run_id = identifier(state["intent"]["client_run_id"])
    label = state["suspension"] or state["milestone"]
    print(f"Run {run_id}: {label.replace('_', ' ')}.")
    if state["stopped"]:
        print("Further local uploads are stopped. Already accepted VE submissions and compute are not withdrawn.")
    elif state["milestone"] != "published":
        print(f"Continue with: ve-submit resume {run_id}")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "hosted":
        from .hosted_cli import main as hosted_main
        return hosted_main(argv[1:])
    parser = argparse.ArgumentParser(description="Generate and submit your model to the VE staging pilot.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("hosted", help="optional encrypted staging submission flow")
    start = commands.add_parser("start", help="freeze, generate and submit one authorized run")
    start.add_argument("--checkout", type=Path, default=Path.cwd())
    start.add_argument("--note", type=Path, required=True)
    start.add_argument("--model", required=True)
    start.add_argument("--agent-framework")
    start.add_argument("--agent-model")
    start.add_argument("--evidence", action="append", default=[], metavar="KIND=PATH")
    start.add_argument("--adopt-baseline", action="store_true",
                       help="operator only: submit the separately generated pinned staging baseline")
    for name in ("resume", "status", "stop"):
        command = commands.add_parser(name)
        command.add_argument("run_id")
        if name == "resume":
            command.add_argument("--reconcile-submission")
    args = parser.parse_args(argv)
    base = Path.home().resolve() / ".local" / "state" / "ve-submit"
    journal = None
    try:
        if args.command != "start":
            journal = Journal.open(base / identifier(args.run_id))
        if args.command == "status":
            show(journal.read())
            return 0
        if args.command == "stop":
            journal.stop()
            show(journal.read())
            # The local stop is durable even if network/authentication is unavailable.
        file_values = configured_values()
        # start and resume still confirm in the terminal. stop only needs a terminal when the Yukon key is missing.
        needs_terminal = args.command in ("start", "resume") or not has_secret(YUKON_API_TOKEN, os.environ, file_values)
        if needs_terminal and (not sys.stdin.isatty() or not sys.stderr.isatty()):
            raise ValueError("a private interactive terminal is required")
        policy_path = Path.home().resolve() / ".config" / "ve-submit" / "policy.json"
        policy = load_policy(policy_path)
        if args.command == "start" and policy_path.is_relative_to(args.checkout.resolve()):
            raise ValueError("install trusted policy outside the submission checkout")
        with ExitStack() as resources:
            yukon_key = prompt_secret("Your Yukon API key", YUKON_API_TOKEN, os.environ, file_values)
            gateway = GatewayClient(yukon_key, policy)
            resources.callback(gateway.close)
            if args.command == "stop":
                gateway.stop(args.run_id)
                print("Gateway stop recorded.")
                return 0
            yukon = YukonClient(yukon_key, policy)
            resources.callback(yukon.close)
            del yukon_key
            ve_key = prompt_secret("Your personal VE API key", VE_API_KEY, os.environ, file_values)
            ve = VEClient(ve_key, policy)
            resources.callback(ve.close)
            del ve_key
            if args.command == "start":
                from . import secure
                evidence = []
                for item in args.evidence:
                    kind, separator, name = item.partition("=")
                    if not separator:
                        raise ValueError("evidence must use KIND=PATH")
                    evidence.append((kind, Path(name)))
                journal = prepare(args.checkout.absolute(), base,
                    secure.read(args.note.absolute(), 16384, private=False).decode(),
                    {k: v for k, v in dict(model=args.model, agent_framework=args.agent_framework,
                                          agent_model=args.agent_model).items() if v is not None},
                    evidence, yukon, ve, gateway, Clock(), confirm, baseline=args.adopt_baseline)
                print("Saved run: " + journal.read()["intent"]["client_run_id"])
            state = run_until_pause(journal, yukon, ve, gateway, Clock(), confirm,
                reconcile_submission=getattr(args, "reconcile_submission", None))
            show(state)
            return 0 if state["milestone"] == "published" else 2
    except SecretFileError as error:
        print(str(error), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        if journal is not None:
            journal.suspend("pending")
            show(journal.read())
        return 2
    except Exception:
        # Avoid library tracebacks, response bodies, filesystem paths or keys.
        print("Unable to continue safely. Check the installed policy, linked accounts and saved run.", file=sys.stderr)
        if journal is not None:
            show(journal.read())
        return 2
