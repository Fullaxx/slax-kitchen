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
| `vendor/linux-live/` — Linux Live Kit + Slax build system | **GPLv2** — © Tomáš Matějíček |

`vendor/linux-live` is a git submodule pinned to an upstream commit and is **never modified**. A
pre-commit gate (`ci/checks/20-vendor-pristine.sh`) rejects any commit that writes into that tree,
so the license boundary cannot erode by accident. Upstream carries its license text at
`vendor/linux-live/DOC/LICENSE` and `vendor/linux-live/DOC/GNU_GPL` (GPL v2, June 1991).

Note: upstream has no repository-root `LICENSE` file, so GitHub's license detector reports `null`
for it. The intent is unambiguously GPLv2 from `DOC/LICENSE`, but it is not machine-detectable.

Any file in this repository that is derived from a `linux-live` script is **GPLv2**, not MIT, and
says so in its header.

## Third-party components inside a Slax ISO

A built ISO is an aggregate. Redistributing one means shouldering the obligations of everything in
it, which this project does not and cannot waive on your behalf:

- **Debian** or **Slackware** packages, each under its own license
- **Non-free firmware** (`firmware-iwlwifi`, `firmware-realtek`, `firmware-atheros`,
  `firmware-brcm80211`, …) with per-package redistribution terms
- **Chromium** (BSD-family) in `05-chromium.sb`
- The **Linux kernel** (GPLv2), custom-built with the out-of-tree
  [aufs](https://github.com/sfjro/aufs5-standalone) patch set
- Prebuilt static binaries in `vendor/linux-live/initramfs/static/` shipped with no in-tree source

GPLv2 components carry a source-offer obligation. If you publish a customized ISO, that obligation
is yours.
