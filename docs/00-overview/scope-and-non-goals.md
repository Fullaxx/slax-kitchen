# Scope and non-goals

## What this is

A toolkit for modifying an existing Slax ISO and getting a bootable ISO back.

```
kitchen unpack <iso>  →  kitchen apply <recipe…>  →  kitchen pack  →  kitchen test
```

Everything is declarative: a recipe is YAML validated against a schema, a profile is a named list of
recipes, and the same scripts run in the git hooks and in CI so they cannot drift apart.

## What this is not

**Not a fork of Slax, and not a redistribution of it.** Upstream is vendored unmodified at
`vendor/linux-live` as a pinned submodule, and never relicensed. The ISOs are not in this repository
and are not mirrored to it — `kitchen fetch` downloads them from the official mirror and verifies
the hash. If you find this useful, support Slax upstream; there is a donate link on slax.org.

**Not a Slax build system.** It does not compile a kernel, run debootstrap, or build bundles from
scratch. It starts from a released ISO and changes it. The real build system is upstream's and is
documented in [`15-upstream/`](../15-upstream/).

**Not a general live-CD tool.** Everything here assumes Linux Live Kit's layout: numbered squashfs
bundles, an aufs union, `/init` from `livekitlib`. Much of it would work on any Linux Live Kit
derivative; none of it is designed for Ubuntu's casper or Fedora's dracut-live.

**Not a Secure Boot solution.** The kernel is unsigned and there is no shim. `uefi-bootable` makes
the ISO boot on UEFI with Secure Boot **off**. Signing the chain is a different project.

## Supported

Four targets, each fingerprinted in `compat/`:

```
debian-64bit-12.2.0     debian-32bit-12.2.0
slackware-64bit-15.0.4  slackware-32bit-15.0.4
```

A recipe declares which it works on, and applying it elsewhere fails loudly rather than producing a
broken image.

Older Slax releases are not supported, and are unlikely to work: 9.x and 11.x predate the current
bundle layout. `kitchen probe` will tell you what you have and report `unknown` rather than guessing.

## Deliberate limits

| | why |
|---|---|
| **Slackware package installation is deferred** | Slackware has no official dependency-resolving package manager, and the shipped `slackpkg` configuration points at a dead host and a moved mirror. Installing from pinned `.txz` files does work and is the supported route. → [add-packages](../50-cookbook/add-packages.md) |
| **The initramfs `busybox` is not replaced by default** | it is the one binary every other boot component depends on. The recipe exists but is opt-in and gated on a five-stage test harness. |
| **`kernel.replace` warns loudly** | a stock distribution kernel has no aufs, which silently downgrades the union and breaks `slax activate` with no error at any point. |
| **Reproducible builds are not promised** | upstream's own `initramfs_pack` uses `find . -print` with no `sort`, so even identical content compresses differently. We document how to make it reproducible; we do not claim the stock images are. |

## Privilege

Roughly 85% of the toolkit runs unprivileged, including both flagship recipes — `mkfs.vfat -C` plus
`mtools` build an EFI System Partition without ever mounting anything.

Two things need more:

| | needs |
|---|---|
| `bundle.packages` | a real `chroot` with `/proc` mounted — root, or a container with `CAP_SYS_ADMIN` |
| the full boot-test matrix | `/dev/kvm`; TCG works but is 10–20× slower |

`kitchen doctor` reports what the current machine can do, and `kitchen apply` refuses to start a
recipe whose tools are missing rather than failing partway through.
→ [container vs host](../40-workflow/container-vs-host.md)

## Licensing

MIT for this repository's own code, docs and recipes. Slax and Linux Live Kit are **GPLv2** and stay
that way under `vendor/`. Everything inside a built ISO carries its own licence — Debian and
Slackware packages, non-free firmware, Chromium. [`NOTICE.md`](../../NOTICE.md) sets out the boundary
and what redistributing an image obliges you to do.

## Honest status

See [`status.md`](status.md). Short version: the core loop and fourteen recipes are boot-verified, the
Phase 1 documentation is complete, and roughly two thirds of the planned recipes are not written yet.
