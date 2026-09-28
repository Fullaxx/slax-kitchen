# NOTICE

## Credit

**Slax** and **Linux Live Kit** are the work of **Tomáš Matějíček** (`Tomas-M`). This project
customizes *his* work; without it there would be nothing here to customize.

- <https://www.slax.org> — the project, downloads, changelog, blog, and his donate page
- <https://www.linux-live.org> — Linux Live Kit, the framework Slax is built on
- <https://github.com/Tomas-M/linux-live> — the source: Linux Live Kit *and* the complete official
  Slax build system (`Slax/debian12/`, `Slax/slackware15/`). There is no separate `Tomas-M/slax`
  repository — this is it.
- <https://github.com/Tomas-M> — components Slax depends on directly: `dynfilefs` (persistence
  container), `httpfs2-enhanced` (`from=http://` boot), `ncurses-menu` (session/device pickers),
  `xlunch` (app launcher), plus `mini-commander`, `GTKdialog`, `posixovl`
- <https://ftp.linux.cz/pub/linux/slax/> — official release mirror

If you find this toolkit useful, support Slax upstream. This repository is not the project's home.

## Licensing boundary

| Component | License |
|---|---|
| slax-kitchen (this repository's own code, docs and recipes) | **MIT** — see `LICENSE` |
| `vendor/linux-live/` — Linux Live Kit + Slax build system | **GPL** — © Tomáš Matějíček |

`vendor/linux-live` is a git submodule pinned to an upstream commit and is **never modified**. A
pre-commit gate (`ci/checks/20-vendor-pristine.sh`) rejects any commit that writes into that tree,
so the license boundary cannot erode by accident.

Upstream's `DOC/LICENSE` says "released under GNU GENERAL PUBLIC LICENSE" and names no version. The
license text shipped beside it, `DOC/GNU_GPL`, is version 2 (June 1991). There is no
repository-root `LICENSE` file, so GitHub's license detector reports `null` for the repository.

Any file in this repository that is derived from a `linux-live` script is **GPLv2**, not MIT, and
says so in its header.

## What a built image contains

A Slax ISO — stock, or built with this toolkit — is an aggregate. Each part keeps its own license,
and each part's upstream publishes its own source:

| Part | Where its upstream publishes source |
|---|---|
| **Slax 12.2.0, as Tomáš built it**: the Linux Live Kit scripts; the kernel, 6.1.38 — Debian's `linux-source-6.1` plus aufs `6.1-20230724`, with its build configuration embedded in `vmlinuz`; the initramfs userland — busybox and seven helpers, prebuilt (upstream's `initramfs/static/README` names buildroot), of which `mkfs.xfs.custom` has its source at `tools/mkfs.xfs.custom.c`; and the desktop tools compiled during the build | <https://github.com/Tomas-M/linux-live>, and the repositories it names |
| **Debian packages** — everything in the Debian flavour that `dpkg` knows about | `https://snapshot.debian.org/package/<source>/<version>/` |
| **Slackware packages** | the Slackware tree named in each package's `PACKAGE LOCATION` |
| **Anything a recipe adds** — Tor Browser, memtest86+, a package from a vendor's apt repository | that recipe's upstream, named in the recipe and on its cookbook page |

[`docs/30-inventory/`](docs/30-inventory/README.md) records what is in each stock image, including
[the initramfs binaries](docs/30-inventory/initramfs-userland.md) one by one and
[the kernel](docs/30-inventory/kernel.md).

Upstream's build removes `/usr/share/doc` from every bundle (`Slax/debian12/cleanup`), so a stock
image carries no per-package `copyright` files. `/usr/share/common-licenses` survives.

## Firmware

Slax ships non-free firmware, and an image built with this toolkit may add more. **Using an image
that contains firmware implies acceptance of each firmware's license terms.** Nothing asks you to
click through them.

- Firmware from **Debian packages** is used as Debian ships it, under the terms Debian ships with it.
- Stock Slax's `01-firmware.sb` records twelve Debian packages — ten that ship firmware, plus
  `firmware-b43-installer`, which ships only a catalog, and `b43-fwcutter`, the extractor — and
  Slax's build removed every one of their `copyright` files. `ipw2x00.LICENSE` is the one license
  file left beside them, under `/usr/lib/firmware` on the Debian images and `/lib/firmware` on the
  Slackware ones, which are not usr-merged. The bundle also holds 155 files of Broadcom b43 firmware
  that `firmware-b43-installer` extracted during Slax's build, beside the catalog listing them. That is not the content of any Debian
  package, and it was published with no license text. Images built with `firmware-refresh` keep it
  as Slax shipped it.
- [`firmware-refresh`](docs/50-cookbook/firmware-refresh.md) reinstalls those Debian packages, which
  brings their `copyright` files back, and adds more of Debian's firmware the same way. The firmware
  it copies from linux-firmware travels with the license file linux-firmware's `WHENCE` names for
  it, under `/usr/lib/firmware/LICENSES/`.
- If you would rather not accept those terms, build without the firmware:
  [`remove-bundle`](docs/50-cookbook/remove-bundle.md#building-without-firmware) with
  `drop: 01-firmware`.

## Publishing an image built with slax-kitchen

Whoever publishes an image decides whether to, and what to publish with it. Nothing in this
toolkit decides that for them.

A published image travels with:

- **`SHA256SUMS`**, made with `sha256sum`. It lets anyone check that the file they downloaded is
  the file that was published. It does not promise that a rebuild will match — images are not
  byte-reproducible; see [reproducibility](docs/40-workflow/reproducibility.md).
- **`<image>.provenance.json`**, which `kitchen pack` writes beside the image. It records the base
  image, the kitchen commit, the project commit, every recipe applied and what each one fetched.
  What the build changed is in the project's repository and in this one, at those commits. No
  source is attached for what the build did not change.
- optionally, **where each part's upstream publishes its source**, where that is known — the
  table above, worked out per image.
  [`kitchen sources <iso> --markdown F`](docs/90-reference/cli.md#sources-iso---json-f---markdown-f)
  writes it as `SOURCES.md`, from the record `kitchen pack` wrote.
- **An identity that does not claim to be an official Slax release** —
  [`iso-identity`](docs/50-cookbook/iso-identity.md) and [`branding`](docs/50-cookbook/branding.md).

[Publishing an image](docs/40-workflow/publishing-images.md) is the procedure, as commands.

## This repository's releases

A release of slax-kitchen is the toolkit: a tag, and notes saying what was verified. No image is
attached to one.
