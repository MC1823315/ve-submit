# ve-submit

Install the VirtualEmbryo staging submission client from one release bundle.

**Current release: [v0.2.0-staging](https://github.com/MC1823315/ve-submit/releases/tag/v0.2.0-staging).**
It includes the configuration for the existing **T2 heart-interpolation staging
pilot**. Additional-board support is included in the client; those boards need
their own released configuration and activation before use.

## Install

You need **Python 3.12 on macOS or Linux**. Windows users can use WSL.

1. Download and extract
   [ve-submit-v0.2.0-staging.tar.gz](https://github.com/MC1823315/ve-submit/releases/download/v0.2.0-staging/ve-submit-v0.2.0-staging.tar.gz).
2. In the extracted directory, run:

   ```sh
   python3.12 -I install.py
   ```

Setup verifies the bundled client/configuration and installs hash-locked
dependencies from PyPI. It installs the client in a dedicated environment,
requires no administrator access, and does not request API keys or submit a model.
The release also publishes an archive SHA-256 checksum for independent checking.

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

Save the printed run ID. If interrupted, follow the client's instructions to
retry the original saved request with `ve-submit hosted submit RUN_ID`.
Do not start another submission to recover an uncertain upload.

## Updates and additional boards

Download the next official release and run its installer. Setup preserves older
installations and private saved requests.

The original T2 policy and the expansion-board catalog are installed separately.
The default submission command continues using the original T2 pilot when that
policy is present. For an additional installed and activated board, use its
documented `--board` selection.

An update can add catalog boards without changing existing board bindings or
encryption recipients. If setup reports an incompatible policy change, retain
the old policy and saved requests and contact the organizer.

This repository distributes participant releases. The maintained source and
build process remain in the benchmark repository. No hosted service runs here.
