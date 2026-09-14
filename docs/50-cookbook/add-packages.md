# `add-packages` — install distro packages into a new bundle

**Status: matrix-verified** on Debian — built and structurally asserted on both
Debian targets. Implemented but unsupported on Slackware; see below.

```sh
kitchen unpack isos/slax-64bit-debian-12.2.0.iso
kitchen apply add-packages
kitchen pack
```

Copy `recipes/available/add-packages.yaml`, change the `packages` list, done. Takes about 21 s.

## How it works

The build root is stacked from **the ISO's own bundles**. `01-core.sb` is already a complete
Debian 12 root filesystem with `apt` in it, so there is no `debootstrap` and no external base image,
and everything installed matches the shipped kernel's ABI by construction.

```
unsquashfs 01-core.sb  ->  add the runtime dirs livekit makes at boot  ->  chroot
  ->  apt-get install  ->  diff (added AND modified)  ->  mksquashfs -> 07-<name>.sb
```

Full mechanics, and why real `chroot` is used rather than `proot`, are in
[edit-bundles.md](../40-workflow/edit-bundles.md).

## Bundle numbering

`bundle:` must start with `NN-`, and the schema enforces it, because **load order is the numeric
prefix and higher wins**. `union_append_bundles` inserts each branch at aufs index 1, so each
insertion outranks the previous. Stock bundles occupy `01`–`06` and `savechanges` writes
`99-changes-N.sb`, so **`07` overrides everything stock while still losing to a user's saved
session** — almost always what you want.

## Verified

Debian 12.2.0 64-bit, `packages: [tmux, ncdu]`:

```
delta: 86 added, 32 modified, 61 kept after exclusions
built slax/modules/07-extras.sb (664 KiB, 61 paths)
```

The bundle carries `usr/bin/tmux`, `usr/bin/ncdu`, their dpkg `.list` files, and a
`var/lib/dpkg/status` with 299 `Package:` stanzas where `01-core` ships 295. Booted: livekit mounts
`modules/07-extras.sb` last and reaches `Live Kit done, starting slax`.

---

## Slackware: why it is unsupported

This is worth stating precisely, because the reason is **not** that it fails.

**It works.** Against `slax-64bit-slackware-15.0.4.iso` the same recipe installs `tmux`, produces a
448 KiB bundle containing exactly the binary, its config, man page, doc and `pkgtools` entries, and
the packed ISO boots with `modules/07-extras.sb` mounted last, handing off to
`INIT: version 3.08 booting`. `slackpkg` is official Slackware (it ships in the `ap` series), as are
`pkgtools`.

It is unsupported because of two properties we cannot fix from here:

**1. Slackware has no dependency resolution.** This is a deliberate design choice upstream, not a
bug. `slackpkg install tmux` installs *tmux* — it will not pull `libevent`. A recipe that says
`packages: [foo]` therefore cannot be trusted to yield a working bundle; the author would have to
enumerate the entire transitive closure by hand, which defeats the point of a declarative recipe.
(`slackpkg+`, which Slax preconfigures, adds extra *repositories*, not dependency resolution.)

**2. Mirror drift.** The base is `Slackware 15.0+ (post 15.0 -current)`, frozen October 2023. The
stock config points at `slackware64-current`, which is years ahead — our test pulled **tmux 3.7c**,
where Slackware 15.0 shipped 3.3a. Anything installed from there risks linking against libc symbols
the bundle's glibc does not have. There is no dated archive that matches a *-current snapshot*
precisely, so the `mirror:` field mitigates this but cannot fully solve it.

Neither is a defect in slax-kitchen, and neither has a fix that lives in this repo.

### What is still in the code, and why

The Slackware support stays implemented rather than being ripped out — it costs nothing, and one
piece of it is load-bearing on **both** flavours:

| Kept | Why |
|---|---|
| **`_installed()` verification** | **The most valuable thing the Slackware experiment produced.** A package manager can report success while installing nothing: slackpkg exits 0 after silently declining its own confirmation prompt, and the verb cheerfully built a 4 KiB "successful" bundle containing no packages. Verifying against the package database turns that from silent into loud. Costs nothing on Debian; keep it forever. |
| `yes` on stdin | Gets past slackpkg's `-current` confirmation, which `-batch=on` does not cover. |
| Dead-repo pruning | `slackonly.com` is NXDOMAIN and hardcoded in Slax 15.0.4, so `slackpkg update` fails on a stock image without it. |
| `mirror:` field | Lets someone pin a stable mirror if they take this on. |
| `-current` warning | Names the ABI risk at the point it matters. |

### If you want to take it on

The promising route is **not** slackpkg — it is `installpkg` against a **pinned local package set**,
which is what upstream's own `txz2sb` does. That gives exact reproducibility and sidesteps mirror
drift entirely, at the cost of requiring you to assemble the package set (and its dependencies)
yourself. The verb's `from:`, delta and exclusion machinery would all be reused unchanged.

### Unaffected

Everything else works identically on Slackware, because it is flavour-agnostic — **30 of 32 files
under `/slax/boot/` are byte-identical between the two flavours**, `vmlinuz` included. `unpack`,
`pack`, `probe`, fingerprints, and the `uefi-bootable` / `isohybrid` / `serial-console` recipes are
all verified on Slackware bases.
