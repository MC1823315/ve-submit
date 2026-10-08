# ve-submit

Install the VirtualEmbryo staging submission client from one release bundle.

**Current release: [v0.2.2-five-board-staging](https://github.com/MC1823315/ve-submit/releases/tag/v0.2.2-five-board-staging).**
It includes the updated client and configuration for the current **T2
heart-interpolation staging runner**. Install this bundle even if you already
have v0.2.0: new T2 submissions need the updated configuration.

## Install or update

You need **Python 3.12 on macOS or Linux**. Windows users can use WSL.

1. Download
   [ve-submit-v0.2.2-five-board-staging.tar.gz](https://github.com/MC1823315/ve-submit/releases/download/v0.2.2-five-board-staging/ve-submit-v0.2.2-five-board-staging.tar.gz)
   and its
   [SHA-256 checksum](https://github.com/MC1823315/ve-submit/releases/download/v0.2.2-five-board-staging/ve-submit-v0.2.2-five-board-staging.tar.gz.sha256).
2. Verify the checksum and extract the archive.
3. In the extracted directory, run:

   ```sh
   python3.12 -I install.py
   ```

Setup verifies the bundled client/configuration and installs hash-locked
dependencies from PyPI. It uses a dedicated environment, requires no
administrator access, and does not request API keys or submit a model.
An update preserves previous installations and private saved requests.

Run `~/.local/bin/ve-submit hosted start --help`. To use `ve-submit` by name,
add this to your shell profile:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

## Submit

Complete the organizer's account activation and model/data preparation first.
Installation does not grant contest membership or activate compute.

For the current human-team T2 heart-interpolation pilot:

```sh
ve-submit hosted start \
  --checkout /absolute/path/to/your/model-checkout \
  --note /absolute/path/to/research-note.txt \
  --model "Your scientific model description"
```

Enter your own Yukon and VE staging keys only at the hidden prompts. Review the
selected identity and board, then authorize the submission. Keep keys out of
command arguments, model source, and research notes.

Save the printed run ID and the files under `~/.local/state/ve-submit-hosted/`.
If interrupted, follow the client's instructions to retry the original saved
request with `ve-submit hosted submit RUN_ID`. Do not start another submission
to recover an uncertain upload.

## Saved requests and additional boards

New T2 submissions now use the installed T2 catalog configuration by default.
Saved requests retain their original configuration. Updating the tool does not
retry failed submissions or recover their scores; ask the organizer about a
previously failed submission before starting a replacement.

Configuration for four additional staging boards is included. Only use those
boards once the organizer confirms activation, with the documented `--board`
selection.

Setup permits additive catalog updates while preserving existing board bindings
and encryption recipients. If it reports an incompatible policy change, retain
your configuration and saved requests and contact the organizer.

This repository distributes participant releases. The maintained source and
build process remain in the benchmark repository. No hosted service runs here.
