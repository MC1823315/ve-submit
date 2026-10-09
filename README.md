# ve-submit 0.2.6 — consolidated VirtualEmbryo staging

This release preserves v0.2.5's client features and consolidated contest
configuration. Use **v0.2.6-staging** for the consolidated contest.
Use the archive, checksum, and installation guide from the same release.

Four boards are open: T2 embryo interpolation, T2 heart extrapolation,
T2 heart interpolation, and T3 Gata4. T1 remains paused while Yukon's
artifact-selection issue is fixed.

## Install

Download the archive and its `.sha256` file from
[v0.2.6-staging](https://github.com/MC1823315/ve-submit/releases/tag/v0.2.6-staging),
verify the checksum, extract the archive, and enter the extracted directory.
You need **CPython 3.12 on macOS or Linux** and access to PyPI for the locked
dependencies. Windows users can use WSL.

For a first installation, or an upgrade from **v0.2.5-staging** or
**v0.2.3-consolidated-staging**:

```sh
python3.12 -I install.py
```

If upgrading from **v0.2.4-staging or an earlier staging contest**, first move
the two old contest configuration files into a backup directory:

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

This retains your `.env`, prior installations, and saved request files.
Submissions from the old contest do not carry over to the new contest.
Installation does not request keys, grant membership, or submit a model.
The consolidated catalog is installed at
`~/.config/ve-submit/hosted-catalog.json`. Do not edit its board IDs or endpoints.

## API keys

The client reads `YUKON_API_TOKEN` and `VE_API_KEY` from your environment or
`~/.config/ve-submit/.env`; environment values take precedence. A missing key
is requested at a hidden prompt. The env file must be owned by you and private:

```sh
chmod 600 "$HOME/.config/ve-submit/.env"
```

Use your own Yukon and VE staging keys. Keep the file outside your model
checkout. An optional `VE_SUBMIT_ENV` can select a different private env file.
The client still asks for confirmation before starting a submission.

## Submit

Complete the contest's data preparation and account activation, then choose
an open board explicitly:

| Board | Selection | Editable source folder |
|---|---|---|
| T2 embryo interpolation | `t2-embryo-interp` | `solution-t2-embryo-interp/` |
| T2 heart extrapolation | `t2-heart-extrap` | `solution-t2-heart-extrap/` |
| T2 heart interpolation | `t2-heart-interp` | `solution/` |
| T3 Gata4 | `t3-gata4` | `solution-t3/` |
| T1 temporal — paused | `t1-temporal-val` | `solution-t1/` |

Pass the checkout root to `--checkout`. The client packages only the source
folder for the selected board. Keep data, notes, and credentials outside that
folder. Run local prediction code without your API keys in its environment.

```sh
~/.local/bin/ve-submit hosted start \
  --board t2-heart-interp \
  --checkout /absolute/path/to/model-checkout \
  --note /absolute/path/to/research-note.txt \
  --model "Your scientific model description"
```

Retain the printed run ID and the private files under
`~/.local/state/ve-submit-hosted/`. If a new-contest submission is interrupted,
follow the client's guidance to resume its saved request with
`~/.local/bin/ve-submit hosted submit RUN_ID`. Do not start another request to
recover an uncertain upload.

Each open board allows two concurrent workflows. VE account quotas apply
independently. T1 rejects new submissions while paused.

The launcher uses an isolated Python interpreter. Keep the installation and
configuration outside your model checkout.

This repository distributes participant release bundles. Client source is maintained
in the private benchmark repository; install from the release archive.
