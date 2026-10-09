# ve-submit 0.2.8 — installation for consolidated VirtualEmbryo staging

This is the **installation and upgrade guide** for `v0.2.8-staging`. The release
preserves v0.2.7's runtime, dependencies, and five-board catalog; it corrects
participation guidance. Use the archive, checksum, and guide from the same release.

For accounts, data preparation, local prediction/validation, task rules, research
notes, and recovery, use the complete
[Participation guide](https://github.com/Layr-Labs/ve-bprime-benchmark/blob/main/docs/participation.md)
after the organizer grants private repository access. Installation does not grant
that access, contest membership, or individual activation.

All five boards were open on **October 9, 2026**, including T1. Check the selected
live board before starting; availability and capacity can change.

## Install

Download the archive and accompanying checksum from the
[v0.2.8-staging release](https://github.com/MC1823315/ve-submit/releases/tag/v0.2.8-staging).
Verify the archive's SHA-256 against that release's checksum, then extract it.
Run the installer from the extracted directory, outside your model checkout.
You need **CPython 3.12 on macOS or Linux** and access to PyPI for the locked
dependencies. Windows users can use WSL.

For a first installation:

```sh
python3.12 -I install.py
export PATH="$HOME/.local/bin:$PATH"
ve-submit hosted start --help
```

The installer verifies the bundled manifest, wheel, policy/catalog, and locked
dependencies. The launcher uses isolated Python. Installation never requests
keys, submits a model, or opens a board.

Upgrading from v0.2.7 preserves the same board bindings and needs no catalog
migration. Run the same installer; retain your existing configuration and saved
requests. If upgrading from **v0.2.6 or earlier**, first back up the old contest
configuration because the heart-interpolation identity changed in v0.2.7:

```sh
config_dir="$HOME/.config/ve-submit"
backup_dir=$(mktemp -d "$config_dir/catalog-backup.XXXXXX")
for name in hosted-policy.json hosted-catalog.json; do
  if [ -f "$config_dir/$name" ]; then
    mv "$config_dir/$name" "$backup_dir/$name"
  fi
done
python3.12 -I install.py
```

This retains `.env`, prior installations, and saved request files. Saved requests
keep their original board identity; they do not migrate to the replacement board.
The catalog is installed at `~/.config/ve-submit/hosted-catalog.json`. Never edit
its IDs/endpoints to bypass a conflict. Ask the organizer to reconcile unexpected
configuration differences.

Use the GitHub release channel above until the selected staging API's client
version, guide, installer, and archive are verified as available. Do not substitute
a production installer or an unconfigured API URL.

## Personal API keys

The client reads `YUKON_API_TOKEN` and `VE_API_KEY` from your environment or
`~/.config/ve-submit/.env`; environment values take precedence. Missing keys use
hidden prompts. The file must be owned by you and private:

```sh
chmod 600 "$HOME/.config/ve-submit/.env"
```

Use your own **Yukon dev** and **VE staging** keys. Keep them outside the model
checkout and out of model execution, command arguments, research notes, and chat.
`VE_SUBMIT_ENV` can select another private env file. The client asks for submission
confirmation; a coding assistant does not handle your keys.

## Select the board

| Board | `--board` selection | Editable source folder |
| --- | --- | --- |
| T2 heart interpolation | `t2-heart-interp` | `solution/` |
| T2 heart extrapolation | `t2-heart-extrap` | `solution-t2-heart-extrap/` |
| T2 embryo interpolation | `t2-embryo-interp` | `solution-t2-embryo-interp/` |
| T3 Gata4 | `t3-gata4` | `solution-t3/` |
| T1 temporal | `t1-temporal-val` | `solution-t1/` |

Complete data preparation, local validation, research note, and account activation
using the full Participation guide. Pass the checkout root to `--checkout`; only
the selected source folder is packaged. Keep data, generated predictions, notes,
and credentials outside that folder.

Our EigenLab Yukon team uses the **human track**, including when a coding
assistant helps. Supply a scientific model description and research note;
omit `--agent-framework`, `--agent-model`, and `--evidence`.

```sh
~/.local/bin/ve-submit hosted start \
  --board t2-heart-interp \
  --checkout /absolute/path/to/model-checkout \
  --note /absolute/path/to/research-note.txt \
  --model "Your scientific model description"
```

Retain the local run ID, Yukon submission ID, and private files under
`~/.local/state/ve-submit-hosted/`. Follow with `ve-submit hosted status RUN_ID`.
If admission is uncertain, follow the full guide to resume the same saved request
with `ve-submit hosted submit RUN_ID`; do not start another request to recover
an uncertain upload.

Each board advertised two concurrent Yukon workflows on October 9, 2026.
Your daily personal VE staging allowance is separate; the client's live quota
check is authoritative. Direct staging uploads use that same allowance. Official
production contest rules have separate phase-specific limits. Staging scores are
development feedback, not evidence of final P3 performance.
