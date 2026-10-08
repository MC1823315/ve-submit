# Install ve-submit

Use the release linked by the official participant instructions. Download the
bundle and its checksum from that release, verify the archive checksum, and
extract the bundle.

You need **Python 3.12 on macOS or Linux**, plus internet access to PyPI for the
locked dependencies. Windows users can use a Linux environment through WSL.

From the extracted directory, run:

```sh
python3.12 -I install.py
```

Setup verifies the bundled files, installs the client in its own environment,
and installs the supplied public policy. It does not request an API key or
submit anything. Administrator access is not needed.

If your shell cannot find `ve-submit`, add this to your shell profile:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

Or invoke `~/.local/bin/ve-submit` directly.

## Submit

Follow your contest's source/data preparation and account-activation instructions.
For the existing human-team heart-interpolation pilot:

```sh
ve-submit hosted start \
  --checkout /absolute/path/to/your/model-checkout \
  --note /absolute/path/to/research-note.txt \
  --model "Your scientific model description"
```

Put your Yukon dev key and personal VE staging key in the environment, or in
`~/.config/ve-submit/.env` (mode `0600`). `VE_SUBMIT_ENV` can name another
private file. The client does not read a `.env` inside the model checkout.
A missing key is still requested at a hidden prompt. `hosted start` still asks
you to confirm the submission. Do not put keys in command arguments, source
files, or research notes. Installing the tool does not grant membership or
activate a board. Only use boards confirmed ready by the organizer.

Retain the printed run ID and the private files under
`~/.local/state/ve-submit-hosted/`. If interrupted, follow the client's guidance
to retry the saved request with `ve-submit hosted submit RUN_ID`. Do not start a
new submission to recover an uncertain upload.

For an additional installed and activated board, add its selection, for example
`--board t3-gata4`. One installed catalog can include all five boards even when
they belong to separate Yukon challenges. Setup keeps the original T2 policy
for saved requests. New T2 submissions use the installed T2 catalog binding
when available; saved requests keep their original legacy or catalog binding.

## Updates

Download a new official bundle and rerun setup. Previous environments and saved
requests are preserved. Setup can add catalog boards while keeping every
existing board binding and recipient unchanged. It stops if an existing binding
or recipient would change; use the organizer's compatible policy-transition
instructions rather than deleting the old configuration or saved requests.

The launcher always uses an isolated Python interpreter. Keep the installation
and configuration outside your model checkout.
