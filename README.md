# ve-submit — consolidated VirtualEmbryo staging challenge

This dev release replaces the previous staging challenges with one challenge
containing five boards. Four boards are open for this release; T1 remains paused
while Yukon’s artifact-selection issue is fixed. Previous dev submissions do not carry over.

**Current release: [v0.2.3-consolidated-staging](https://github.com/MC1823315/ve-submit/releases/tag/v0.2.3-consolidated-staging).**

Use this release for the consolidated challenge, including if you previously
installed v0.2.4-staging. The client code remains 0.2.3; this release name
identifies the new five-board catalog. Access covers all five boards.

## Install

Download the [release archive](https://github.com/MC1823315/ve-submit/releases/download/v0.2.3-consolidated-staging/ve-submit-v0.2.3-consolidated-staging.tar.gz)
and its [checksum](https://github.com/MC1823315/ve-submit/releases/download/v0.2.3-consolidated-staging/ve-submit-v0.2.3-consolidated-staging.tar.gz.sha256). Verify the checksum,
extract the archive, and enter the extracted directory. Python 3.12 is required.

If you installed an earlier VE staging client, remove its two old staging
configuration files before installing this replacement catalog:

```sh
rm -f "$HOME/.config/ve-submit/hosted-policy.json" \
      "$HOME/.config/ve-submit/hosted-catalog.json"
python3.12 -I install.py
```

For a first installation, only the second command is needed. Installation does
not ask for keys or submit a model. The bundle's generic instructions describe
an older pilot alongside additional boards; this release uses only the new
five-board catalog and the explicit board selections below.

## Submit

Choose a board explicitly:

| Board | Selection |
|---|---|
| T1 temporal — paused | `t1-temporal-val` |
| T2 embryo interpolation | `t2-embryo-interp` |
| T2 heart extrapolation | `t2-heart-extrap` |
| T2 heart interpolation | `t2-heart-interp` |
| T3 Gata4 | `t3-gata4` |

```sh
~/.local/bin/ve-submit hosted start \
  --board t2-heart-interp \
  --checkout /absolute/path/to/model-checkout \
  --note /absolute/path/to/research-note.txt \
  --model "Your model description"
```

The client privately requests your own Yukon and VE staging keys. Keep the
printed run ID. If a new-challenge submission is interrupted, resume that
saved request with `ve-submit hosted submit RUN_ID`.

Each of the four open boards has two concurrent workflows. T1 rejects new submissions while paused. VE account
quotas continue to apply independently of these workflow slots.

This repository distributes participant client bundles. Installation does not
grant challenge membership. Client source remains in the private benchmark
repository; install from the release archive.
