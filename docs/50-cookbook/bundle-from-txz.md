# `bundle-from-txz` — install Slackware packages, properly

**Status: verified** — `nano` and `rsync` fetched from a pinned 15.0 mirror and installed with
`installpkg` on Slackware 64-bit; resulting bundle 1.1 MiB, 143 files, package database updated.

```sh
kitchen apply bundle-from-txz
```

```yaml
vars:
  bundle: 07-slackpkgs
  mirror: "https://mirrors.slackware.com/slackware"
  release: "15.0"
```

**Slackware only** — `.txz` is its package format. The Debian equivalent is
[`add-packages`](add-packages.md).

## Why this is not `bundle.fromTarball`

A `.txz` *is* a `tar.xz`, and [`bundle-from-tarball`](bundle-from-tarball.md) would unpack one
happily. It would also be wrong twice over:

- **`install/doinst.sh` and `install/slack-desc` would land in the union root.** Every Slackware
  package carries them; neither belongs on a running system.
- **`doinst.sh` would never run.** It is what creates a package's symlinks and installs its `.new`
  config files. Skipping it gives a bundle whose binaries are present and whose symlinks are
  missing — which fails later, somewhere else, for reasons that do not point back here.

`installpkg` does both correctly and registers the package in `/var/log/packages`, which is what
makes the result a real installation rather than an unpacked archive. So this recipe uses
`bundle.script`, and that is why it needs `privilege: chroot`.

`wget` and `/sbin/installpkg` are both in the stock `01-core`, so there is nothing to bootstrap.

## Pin the full filename, and pin 15.0

```
PKGS="ap/nano-6.0-ARCH-1.txz
n/rsync-3.2.3-ARCH-4.txz"
```

A bare package name is not something a Slackware mirror can resolve — there is no dependency
resolver and no "latest" indirection. That is the honest difference from `apt`, and pinning the
exact versioned filename is a feature: the build is reproducible.

`ARCH` is substituted with `x86_64` or `i586`, derived from the target's own `/var/log/packages`
rather than `uname -m`, which inside a chroot reports the **build host's** kernel.

**Use 15.0, never `-current`.** The stock `/etc/slackpkg/mirrors` points at `slackware64-current`,
which has drifted years past this frozen 2023 base — see
[issues 8 and 9](../30-inventory/known-upstream-bugs.md). A `-current` package linked against a
newer glibc installs cleanly and then fails to run.

## It fixes TLS first, because TLS is broken

The script's first real action is:

```sh
[ -s /etc/ssl/cert.pem ] || cat /etc/ssl/certs/*.pem > /etc/ssl/cert.pem
```

**A stock Slackware Slax cannot verify any TLS certificate.** Measured: `01-core` ships 144 CA
certificates as `.pem` files in `/etc/ssl/certs`, and `OPENSSLDIR` is `/etc/ssl` — but neither of
the two places OpenSSL actually looks exists. There is no `/etc/ssl/cert.pem` (the single-file
default) and there are **zero hash symlinks** in `/etc/ssl/certs` (the directory default).
`c_rehash` cannot create them either, because Perl is not installed. Every OpenSSL client fails,
and `wget` exits 5 with *"Unable to locally verify the issuer's authority"*.

This is not a chroot artefact — it is equally true of the booted system. Debian's `01-core` ships a
working bundle; Slackware's ships none. See
[issue 13](../30-inventory/known-upstream-bugs.md) and [`fix-slackware-bugs`](fix-slackware-bugs.md).

`update-ca-certificates` does **not** help: it writes Debian's `certs/ca-certificates.crt` path,
which this OpenSSL build never consults. Concatenating the shipped PEMs into OpenSSL's own default
path does, and it trusts nothing that was not already on the image.

Do **not** reach for `--no-check-certificate` instead. This script installs code that will run as
root on every boot of every image built from it.

**Deliberate side effect:** `/etc/ssl/cert.pem` lands in the delta, so the built bundle carries a
working CA store and the resulting image has working TLS. 218 KiB for a fix to something genuinely
broken is a good trade.

## What lands in the bundle

`bundle.script` packages **added and modified** files, never names alone — a filename-only diff
would miss `/var/log/packages/*`, and a bundle without those entries leaves the new binaries
invisible to the package database.

```
delta: 204 added, 38 modified, 235 kept after exclusions
built slax/modules/07-slackpkgs.sb (1112 KiB, 143 files)
```

`BUNDLE_EXCLUDE` strips the runtime directories the chroot needed but a bundle must not ship, plus
caches and lockfiles.
