# Upstream as source of truth

Slax is built by **Linux Live Kit**, and both live in one repository:
<https://github.com/Tomas-M/linux-live>. There is **no separate `Tomas-M/slax` repository** — that
URL 404s, and anyone citing one is mistaken. The Slax-specific build lives inside linux-live under
`Slax/debian12/` and `Slax/slackware15/`.

That repository is vendored here as a submodule at `vendor/linux-live`, pinned to
**`9825e937570072a015e2e35ad3330ead9055d21d`** (2024-11-14), so every claim in these pages cites a
path and line a reader can open, and those citations cannot rot.

## Why it is authoritative, not merely plausible

The repository is not a reconstruction of how Slax is built — it is what built it. Verified by
hashing the scripts inside a real shipping `initrfs.img` against the repository copies:

| File | In the shipped ISO | In `vendor/linux-live` |
|---|---|---|
| `/init` | `1b04a359…1153` | **identical** |
| `/lib/livekitlib` | `faaedfa8…76ab` | **identical** |
| `/shutdown` | `751aa032…19bf` | **identical** |
| `/lib/config` | `eada9e4f…e300` | differs by **2 lines** |

Those two lines are the Slax customization, applied by `Slax/debian12/build` at build time:

```sh
sed -i -r 's/^LIVEKITNAME.*/LIVEKITNAME="slax"/' $THIS/../../config
sed -i -r 's/^NETWORK.*/NETWORK=true/' $THIS/../../config
```

## Reading order

| | |
|---|---|
| [repo-map](repo-map.md) | what is in the repository, annotated |
| [livekit-build](livekit-build.md) | `build` — the master script, line by line |
| [livekit-config](livekit-config.md) | `config` — every variable, and the Slax delta |
| [livekitlib-reference](livekitlib-reference.md) | all 49 functions, in execution order |
| [initramfs-create](initramfs-create.md) | how `initrfs.img` is assembled |
| [slax-debian12](slax-debian12.md) | the Debian flavour's build |
| [slax-slackware15](slax-slackware15.md) | the Slackware flavour's build |
| [aufs-kernel](aufs-kernel.md) | why the kernel is custom |
| [tools](tools.md) | `tools/` — the three helpers nobody mentions |
| [runtime-tools](runtime-tools.md) | the scripts shipped *into* Slax |
| [doc-verbatim](doc-verbatim.md) | upstream's own `DOC/`, quoted |

## Licensing

Linux Live Kit and the Slax build scripts are **GPLv2** (`vendor/linux-live/DOC/LICENSE` and
`DOC/GNU_GPL`). There is no repository-root `LICENSE` file, so GitHub's detector reports `null` for
the project — the intent is unambiguous from `DOC/`, but it is not machine-detectable.

We vendor the repository **unmodified** and never relicense it. A commit gate
(`ci/checks/20-vendor-pristine.sh`) rejects any change that writes into `vendor/`. See
[NOTICE.md](../../NOTICE.md).

## Keeping the pin honest

`ci/upstream-watch.sh` compares this pin and the slax.org changelog against
`compat/upstream-baseline.yaml` weekly. Upstream has shipped no release since **2023-10-10** and the
repository has not been touched since **2024-11-14**, so any movement is notable.
