# Editing the initramfs

`initrfs.img` is an xz-compressed newc cpio archive. Opening it, changing something and closing it
again is three commands — but two of them have a detail that will cost you a boot if you miss it.

**Run everything here as root.** The archive contains seven device nodes, and a non-root `cpio`
silently turns them into empty regular files.

## Do not use the shipped helpers offline

Slax ships `initramfs_unpack` and `initramfs_pack` in `01-core.sb`. They work — on a booted Slax.
They are a **pair that only works as a pair**, and they need `mount`:

```sh
initramfs_unpack /slax/boot/initrfs.img   # replaces the file with a tmpfs mount holding the tree
…edit…
initramfs_pack   /slax/boot/initrfs.img   # repacks and unmounts it
```

`initramfs_unpack` does `mount -t tmpfs tmpfs "$IMG"`, so it needs `CAP_SYS_ADMIN`. In an
unprivileged container, or on any machine where you would rather not mount things, use the manual
path below. It does exactly the same work.

## Unpack

```sh
mkdir tree && cd tree
xz -dc ../initrfs.img | cpio -id --quiet
```

Verify you got the device nodes — this is the check that catches a non-root extraction:

```sh
$ find . | wc -l ; find . \( -type b -o -type c \) | wc -l
768
7
```

768 entries and **7 device nodes** on the 64-bit Debian image. If the second number is 0, you
extracted as a non-root user and the tree is not repackable.

## Repack

Upstream's exact command, from `initramfs_create` and the shipped `initramfs_pack`:

```sh
( cd tree && find . -print | cpio -o -H newc ) \
  | xz -T0 -f --extreme --check=crc32 > initrfs.img
```

### `--check=crc32` is not optional

xz defaults to **CRC64**, and the kernel's built-in xz decoder does not implement it. An initramfs
compressed with default settings fails at boot with no useful message — the kernel cannot even report
what went wrong, because the thing that would report it is inside the archive.

Confirm before you ship it:

```sh
$ xz --list initrfs.img
Strms  Blocks   Compressed Uncompressed  Ratio  Check
    1       2  8,700.5 KiB     43.5 MiB  0.195  CRC32
```

`Check` must read `CRC32`. `-T0` and `--extreme` only affect build time and size.

### Add `| LC_ALL=C sort` if you want reproducible output

Upstream's command has no `sort`, so entry order follows `find`'s readdir order — which is **not
stable**. Measured on the reference container:

| filesystem | `find . -print` across 5 runs on an unchanged tree |
|---|---|
| ext2/3/4 | identical every time (stable, but arbitrary — hash order, not alphabetical) |
| **overlayfs** | **5 runs, 5 different orders** |

So on a Docker overlay filesystem two consecutive repacks of the same tree produce different
archives — same file set, different bytes, different size:

```
unsorted, run A    8,869,652 B
unsorted, run B    8,900,924 B
sorted,   run 1    8,869,652 B   sha256 4ad7c7f9…
sorted,   run 2    8,869,652 B   sha256 4ad7c7f9…   ← byte-identical
```

Sorting costs nothing at boot — cpio order is irrelevant to extraction — and it turns the repack into
something you can diff. See [reproducibility](reproducibility.md).

## Put it back

```sh
cp initrfs.img work/iso/slax/boot/initrfs.img
kitchen pack
```

Nothing else references it by hash, so no other file needs updating.

## What you might change, and what it costs

| | |
|---|---|
| **add a kernel module** | drop the `.ko` into `lib/modules/<version>/kernel/…`. `modprobe_everything` finds it by walking the tree, so no `depmod` and no manifest to update |
| **add a binary** | must be **static** — there is no dynamic loader here. i386 is safest; see below |
| **patch `livekitlib` or `/init`** | plain shell, edit in place. Keep a copy of the original for `kitchen diff` |
| **change `/lib/config`** | `LIVEKITNAME` also requires patching `isolinux.bin` — [edit-bootloader](edit-bootloader.md) |
| **replace busybox** | the heavy one. [initramfs-userland](../30-inventory/initramfs-userland.md) lists the eight constraints |

### The module directory name is version-specific

```
64-bit   lib/modules/6.1.38
32-bit   lib/modules/6.1.38-smp
```

**Anything hardcoding `6.1.38` breaks on 32-bit.** Read it from the tree:

```sh
LMK=$(basename "$(find tree/lib/modules -mindepth 1 -maxdepth 1 -type d | head -1)")
```

### Static binaries must be i386

Every one of the eight shipped binaries is **32-bit i386, even on the 64-bit images** — one blob set
serves all four targets. That works because the kernel is built with `CONFIG_IA32_EMULATION=y`.

You *can* add an x86-64 static binary to a 64-bit image and it will run. But it will not run on the
32-bit image, so if you want one binary for all four targets, build i386.

### Do not regenerate the applet symlinks naively

`/bin` holds 8 real files and 246 symlinks — 245 to busybox plus `bin/init → ../init`. busybox
reports **248** applets; the three without symlinks are `blkid` and `eject`, which are **real
standalone binaries shadowing busybox's applets**, and `init`.

That shadowing is load-bearing: `livekitlib` parses `blkid -o full`, an option busybox's `blkid` does
not have. Regenerating symlinks from `busybox --list` without the `[ ! -e ]` guard produces 248
symlinks, clobbers both binaries, and breaks device detection at boot with no diagnostic.

## Verify before you ship

```sh
xz --list initrfs.img | grep CRC32                       # compression check
xz -dc initrfs.img | cpio -it | wc -l                    # entry count ≈ 768
kitchen pack && kitchen test out/*.iso --bios            # does it still boot
```

The boot test is the one that matters. A broken initramfs fails before any userspace exists to report
it, so the symptom is a reboot loop or a bare kernel panic — `tests/boot/qemu_boot.py` reads the
serial log and tells you which livekit stage it reached, which is far more informative than watching
it fail.

Expect to see, in order:

```
Looking for slax data
Mounting bundles
Setting up empty union using aufs
Live Kit done, starting slax
```

`Setting up union using overlayfs` instead of the third line means `aufs.ko` did not load.
