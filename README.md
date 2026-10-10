# Install ve-submit 0.2.9

Use the [v0.2.9-staging release](https://github.com/Layr-Labs/ve-bprime-benchmark/releases/tag/v0.2.9-staging)
for the current dataset-enabled staging client. It requires access to the private
repository. Download its archive, checksum and separate `CLIENT-TRANSITION.md`
asset. The newest release assets in the older `MC1823315/ve-submit` distribution
are still `v0.2.8-staging`; those bindings differ from this release.

You need **CPython 3.12 on macOS or Linux** and internet access to PyPI for the
locked dependencies. Windows users can use WSL. Install outside your model
checkout. From the download directory:

```sh
shasum -a 256 -c ve-submit-v0.2.9-staging.tar.gz.sha256
tar -xzf ve-submit-v0.2.9-staging.tar.gz
cd ve-submit-v0.2.9-staging
python3.12 -I install.py
export PATH="$HOME/.local/bin:$PATH"
ve-submit hosted start --help
```

Continue only if the checksum succeeds. The archive SHA-256 is
`5da9712520571c5b46ae023b36af33fcd34cb49b9024d61c4b8ac4f92c0ab18d`.
Setup verifies the bundled files, installs an isolated client environment and
the supplied catalog. It does not request submission keys, submit a model, grant
membership or activate a board. Administrator access is not needed.

If your shell cannot find the command, invoke `~/.local/bin/ve-submit` directly
or add the PATH line above to your shell profile. Keep the client installation
and configuration outside your model checkout.

## Upgrade

Read both this guide and the release's `CLIENT-TRANSITION.md`. Identify the
previous **release tag**, because several different releases used package
version 0.2.8.

| Previous release | Upgrade to 0.2.9 |
| --- | --- |
| `v0.2.8-generation-datasets-staging` | Run the verified 0.2.9 installer normally. The client modules, installer, dependencies and five-board catalog are unchanged; the package version becomes 0.2.9. |
| `v0.2.8-reliability-staging` | The catalog differs. Preserve the old installation and request state; use the organizer's reviewed transition for that catalog. |
| Public `v0.2.8-staging`, or an earlier release | The catalog differs. Preserve the old installation and request state; use the organizer's reviewed transition for that catalog. |

Preserve `~/.config/ve-submit/.env`, any private file selected by
`VE_SUBMIT_ENV`, the existing policy/catalog files, prior release directories
under `~/.local/share/ve-submit/releases/`, and all of
`~/.local/state/ve-submit-hosted/`. The installer retains previous release
environments and saved requests.

If setup says an existing policy differs, stop. Do not delete or hand-edit a
catalog, change IDs or recipients, or move private request files to make setup
pass. The published transition note does not provide a universal migration
command for older catalogs. Have the organizer reconcile unresolved requests
and confirm the transition for your exact installed catalog before switching.

## Prepare and submit

Follow the [participation guide](https://github.com/Layr-Labs/ve-bprime-benchmark/blob/staging/consolidated/docs/participation.md)
for accounts, approved dataset access, source preparation, local validation and
research notes. Released supplemental inputs are available during hosted
generation; primary input aliases and output panels remain board-specific.
The two heart boards exclude `E8.5_RNA.h5ad` from their hosted views under the
staging rule interpretation.

Always select the intended board explicitly. For example:

```sh
ve-submit hosted start \
  --board t2-heart-interp \
  --checkout /absolute/path/to/model-checkout \
  --note /absolute/path/to/research-note.txt \
  --model "Your scientific model description"
```

This contest accepts human-team submissions only. Omit `--agent-framework`,
`--agent-model` and `--evidence`, including when a coding assistant helps.
Use your own Yukon dev and VE staging keys. The client reads them from your
environment or private configuration, or prompts privately when missing; it
asks for submission confirmation. Never put keys in command arguments, source,
research notes or chat. Check current board availability and your own quota
before starting.

## Saved requests and recovery

Retain the printed local run ID, Yukon submission ID and private request
directory. With the original compatible configuration:

```sh
ve-submit hosted status SAVED_RUN_ID
```

If admission was interrupted, the participation guide explains when
`ve-submit hosted submit SAVED_RUN_ID` can retry the same saved request.
An acknowledged run should be followed by its original submission ID.
Do not run `hosted start` again to recover an uncertain admission or upload.

Saved requests retain their original binding and recipient. Upgrading does not
migrate them or renew their authorization. If status or recovery reports a
configuration mismatch, expired authorization or uncertain upload, keep the
original files and contact the organizer. Do not guess a board, restore a
catalog merely to force replay, or rewrite a request. An old client environment
alone cannot make an old request compatible with the current service.
