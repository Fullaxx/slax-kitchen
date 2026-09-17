# The kernel

**Linux 6.1.38, built by Tomáš on 2023-08-11, with aufs patched in.** One kernel per word size,
shared by both flavours.

```
64-bit  Linux version 6.1.38 (root@slax) (gcc (Debian 12.2.0-14) 12.2.0,
        GNU ld (GNU Binutils for Debian) 2.40) #1 SMP PREEMPT_DYNAMIC Fri Aug 11 19:41:11 UTC 2023
        sha256 0c0bf1d56b774d7465d51583e4fcf2d5632099c4050d0f034c0d52982d2c6e61   12,017,856 B

32-bit  Linux version 6.1.38-smp (root@slax) (gcc (Debian 12.2.0-14) 12.2.0,
        GNU ld (GNU Binutils for Debian) 2.40) #1 SMP PREEMPT_DYNAMIC Fri Aug 11 14:10:24 UTC 2023
        sha256 3acebaa2220d70ff928fabdef3afa0473a3516e4847c1f37659bed5a0cb69667   10,841,216 B
```

Both were built the same day, 5½ hours apart, with the identical Debian gcc 12.2.0 / ld 2.40
toolchain — including the Slackware images, whose kernel is byte-identical to Debian's at the same
word size:

```sh
sha256sum vmlinuz-from-debian-iso vmlinuz-from-slackware-iso   # identical
```

That is worth internalising: **the kernel is not a flavour property.** A kernel change applies to two
of the four targets at once, and a `kernel.replace` recipe needs no flavour branch.

## `-smp` is a trap

The 32-bit build carries `LOCALVERSION=-smp`, so its release string is `6.1.38-smp` and its module
directory is `lib/modules/6.1.38-smp`. **Anything that hardcodes `6.1.38` breaks on 32-bit.** Read
the directory name from the tree instead:

```sh
LMK=$(basename "$(find "$root/lib/modules" -mindepth 1 -maxdepth 1 -type d | head -1)")
```

Upstream's own `lib/config` does this correctly via `KERNEL=$(uname -r)`, because it builds on the
machine that will run the kernel.

## Why it is custom

**aufs was never merged into mainline Linux.** Tomáš's union filesystem of choice therefore has to be
patched in and the kernel rebuilt, every release. That single fact explains most of what is unusual
about Slax:

- there is no stock distribution kernel that will do;
- the build needs `sfjro/aufs-standalone` at the matching branch;
- and replacing the kernel with a distribution one silently downgrades Slax to overlayfs, which
  disables `slax activate` and makes `union_append_bundles` a no-op.

Built by `vendor/linux-live/Slax/debian12/aufs-kernel-compile/compile` from `linux-source-6.1` plus
`sfjro/aufs-standalone` at `origin/aufs6.1`. The repository was `aufs5-standalone` when the script was
written, and GitHub redirects that name. The script checks out whatever the branch holds on the day,
so the image is the only record of what shipped: the `aufs.ko` in `01-core.sb` reports version
`6.1-20230724`, commit `3402e2ca3861`. The branch has moved on since, so the script run today builds a
different aufs. Details in [`15-upstream/aufs-kernel.md`](../15-upstream/aufs-kernel.md).

## `CONFIG_IA32_EMULATION=y` is mandatory

Every one of the eight static binaries in the initramfs is **32-bit i386, on all four images** —
including the 64-bit ones. An x86-64 kernel without `CONFIG_IA32_EMULATION` cannot execute any of
them, so:

> **A kernel replacement that drops `IA32_EMULATION` makes the entire initramfs unrunnable.**

The failure happens before any userspace exists to report it: a reboot loop with no message. It is
the highest-consequence constraint in the image, and it is the first thing a `kernel.replace` recipe
must assert.

The shipped kernel has it set. It embeds its own configuration (`CONFIG_IKCONFIG=y`, so a running
Slax has it at `/proc/config.gz`), and that is the record to trust, not upstream's committed
`aufs-kernel-compile/config-x86_64`. The committed file is not the configuration this kernel was built
with: 3,092 options differ (1,816 set differently, 684 only in the shipped configuration, 592 only in
the committed one), and it builds squashfs, isofs, ext4 and vfat as modules where the shipped kernel
has them built in. It does have `IA32_EMULATION` set too.

## What is built in, and what is a module

Absent from `lib/modules/` because they are compiled in:

```
squashfs   isofs   ext4   vfat   (and the usual core)
```

Present as modules, and load-bearing:

```
fs/aufs/aufs.ko             the union
fs/overlayfs/overlay.ko     the fallback
drivers/block/loop.ko       every bundle is a loop mount
drivers/block/zram/zram.ko  512 MB compressed swap
fs/ntfs3/ntfs3.ko           perch on a Windows partition
fs/fuse/*.ko                dynfilefs and httpfs2
```

`squashfs` and `isofs` being built in is why `/init` can mount the medium and the bundles before it
has loaded anything.

The initramfs carries 301 of the tree's modules (300 on 32-bit) — the selection and the full
breakdown are in [`initramfs-userland.md`](initramfs-userland.md).

## Packaging, on Debian

The Debian build installs the custom kernel as a real `.deb` and removes the stock one. Both states
are visible in `01-core.sb`'s dpkg database:

```
linux-image-6.1.38                install ok installed
linux-image-6.1.0-13-amd64        deinstall ok config-files
linux-image-amd64                 deinstall ok config-files
```

On 32-bit the same three lines read `linux-image-6.1.38-smp`, `linux-image-6.1.0-13-686-pae` and
`linux-image-686-pae`. Those three lines are the **only** difference between the 32- and 64-bit
Debian package name sets — 295 entries each, otherwise identical.

`grub-pc`, `grub-common`, `grub2-common` and `apparmor` are in the same removed-but-configured state,
which is the record of Slax choosing SYSLINUX over GRUB.

## Replacing it

Possible, and documented, but it is the one change that can break the system in ways nothing reports:

| you must keep | or |
|---|---|
| aufs | `slax activate` stops working; `union_append_bundles` no-ops |
| `CONFIG_IA32_EMULATION` (64-bit) | the initramfs cannot execute at all |
| squashfs + isofs built in, or in the initramfs | `find_data` cannot read the medium |
| the module directory name in sync | `modprobe_everything` finds nothing |

A replacement also means rebuilding the initramfs, because `initramfs_create` copies modules from
`/lib/modules/$KERNEL` — a new kernel with the old initramfs has 301 modules for a kernel that will
not load them.
