# What Slax is

A live operating system that runs from CD, USB or disk without installing anything, built on **Linux
Live Kit** by Tomáš Matějíček. Two flavours share one boot chain:

| | base | init | size (64-bit) |
|---|---|---|---|
| **Slax 12.2.0** | Debian 12 bookworm | systemd 252 | 416 MiB |
| **Slax 15.0.4** | Slackware 15.0+ | sysvinit + elogind | 455 MiB |

Both exist in 32- and 64-bit, so there are **four targets**, and this toolkit supports all four.

## The one idea

**The root filesystem is a union of numbered squashfs images.**

```
/slax/modules/01-core.sb        the base OS
              01-firmware.sb    wireless firmware
              02-xorg.sb        X
              03-desktop.sb     Fluxbox, panel, launcher
              04-apps.sb        file manager, calculator, …
              05-chromium.sb    the browser
              06-devel.sb       Slackware only — gcc
```

At boot an initramfs loop-mounts each one read-only and stacks them with **aufs**, with a writable
layer on top. Nothing is installed and nothing is copied; the running system is those files, read
straight out of the images.

**Load order is the numeric prefix, and higher wins.** That one rule explains most of how Slax is
customized:

- drop in `07-mytools.sb` and it overrides everything below it;
- `savechanges` writes `99-changes-N.sb`, so a saved session outranks the shipped system;
- `noload=05-chromium` skips a bundle for one boot;
- and deleting a bundle is a legitimate way to shrink the image.

Details in [`10-anatomy/bundles-squashfs.md`](../10-anatomy/bundles-squashfs.md).

## Where your changes go

By default: RAM. Turn them off and they are gone.

Ask for persistence — "perch", for **per**sistent **ch**anges — and the writable layer lives on the
medium instead. On a Linux filesystem it is a plain bind mount with no size limit; on FAT32 or NTFS
it is a growable container file with XFS inside.

**Booting from CD never has persistence**, because the CD menu never sets `perchdir=` and optical
media cannot be written to anyway.
[`10-anatomy/union-and-persistence.md`](../10-anatomy/union-and-persistence.md).

## What makes it customizable

Three properties, in increasing order of how much work they save you:

1. **`01-core.sb` is a complete root filesystem** with a working package manager inside. You can
   unpack it, chroot in, install something, and pack the difference as a new bundle. No debootstrap,
   no base image, and a guaranteed ABI match with the shipped kernel.
2. **The whole build system is public** at `github.com/Tomas-M/linux-live` — Linux Live Kit *and* the
   official Slax build scripts. Verified: the `/init` and `/lib/livekitlib` inside a real
   `initrfs.img` are byte-identical to the repository's copies.
3. **`rootcopy/` costs nothing.** Files placed in `/slax/rootcopy/` are copied into the writable
   layer at boot, before anything starts. No squashfs, no rebuild, no precedence to reason about.

## What is unusual about it

| | |
|---|---|
| custom kernel | aufs was never merged into mainline, so 6.1.38 is built with it patched in |
| 32-bit initramfs | every static binary in the initramfs is i386, **even on 64-bit** — so the kernel needs `CONFIG_IA32_EMULATION` |
| SYSLINUX only | no GRUB anywhere, no Secure Boot |
| no GPU firmware | `01-firmware` is wireless and NIC only, on both flavours |
| busybox from 2017 | v1.26.2, byte-identical on all four images |

## Where it stands

**Nothing has shipped since 2023-10-10.** 12.2.0 and 15.0.4 are the newest releases, and the only
commit upstream since is from 2024-11-14 and never shipped. That is stable rather than abandoned —
it means the four fingerprints in `compat/` stay valid, and a weekly CI job watches for the day that
changes.

## Next

| | |
|---|---|
| use it | [`05-using-slax/quick-start.md`](../05-using-slax/quick-start.md) |
| understand it | [`10-anatomy/runtime-layout.md`](../10-anatomy/runtime-layout.md) |
| customize it | [`40-workflow/unpack.md`](../40-workflow/unpack.md) |
| the vocabulary | [`glossary.md`](glossary.md) |
