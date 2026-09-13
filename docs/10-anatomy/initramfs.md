# `initrfs.img`

8.5 MiB compressed, 43.5 MiB unpacked, 768 entries. An xz-compressed SVR4/newc cpio archive holding
busybox, seven helper binaries, 301 kernel modules and four shell scripts. Everything Slax does
before the real root exists happens here.

```sh
xz -dc initrfs.img | cpio -idv          # unpack (as root, for the device nodes)
xz --list initrfs.img                   # → 1 stream, CRC32
```

## Compression — CRC32 is mandatory

```
Strms  Blocks   Compressed Uncompressed  Ratio  Check
    1       2  8,664.5 KiB     43.5 MiB  0.194  CRC32
```

Upstream produces it with exactly:

```sh
find . -print | cpio -o -H newc | xz -T0 -f --extreme --check=crc32
```

**`--check=crc32` is not a preference.** The kernel's built-in xz decoder does not implement CRC64,
which is `xz`'s default. An initramfs compressed with default settings produces an early boot failure
with no useful message. Anything that rewrites this file must keep the flag.

`-T0` (all threads) and `--extreme` affect size and build time only.

### It is not byte-reproducible, and that is upstream's doing

`find . -print` has no `sort`, so entry order follows readdir order — and that is not merely
host-dependent, it is **not stable at all on some filesystems**. Measured over five runs on an
unchanged tree: identical every time on ext2/3/4, but **five different orders on overlayfs**, which
is what Docker uses by default. Two consecutive repacks there differ by ~31 KB.

The two 64-bit images show the same effect in the shipped artifacts: every file in both trees is
byte-identical except that Slackware adds `usr/share/terminfo/l/linux`, yet Debian's archive begins
`root`, `lib`, `lib/modules/…` and Slackware's begins `shutdown`, `init`, `bin`, … The Slackware
image is 3,448 bytes *smaller* despite having four more entries, purely because xz compressed a
different ordering.

Add `| LC_ALL=C sort` after the `find` and the output becomes byte-identical across runs. It changes
nothing at boot — cpio order is irrelevant to extraction — and it is worth doing unconditionally,
because an unsorted repack cannot be diffed even against itself.
→ [reproducibility](../40-workflow/reproducibility.md)

## Layout

```
/init                     2,317 B   the boot script
/shutdown                 2,504 B   run by systemd / rc.6 on the way down
/lib/config               1,778 B   LIVEKITNAME, BEXT, LMK, NETWORK
/lib/livekitlib          28,608 B   49 functions
/bin/                               8 real binaries + 245 applet symlinks + bin/init
/lib/modules/6.1.38/                301 .ko
/etc/passwd                  25 B   root::0:0::/root:/bin/sh   (empty password field)
/etc/modprobe.d/local-loop.conf     options loop max_loop=32
/etc/{fstab,mtab}             0 B   empty placeholders
/dev/                               7 device nodes, see below
/sbin -> bin                        symlink
/lib64 /mnt /proc /root /run /sys /tmp /usr /var/log     empty directories
```

`/init`, `/lib/livekitlib` and `/shutdown` are **byte-identical to their copies in
`vendor/linux-live/initramfs/`**; `/lib/config` differs by the two lines the Slax build patches in.
That is the proof that the public repository is the real build system — see
[`15-upstream/README.md`](../15-upstream/README.md).

### The seven device nodes

```
crw-r--r--  dev/console   5,1
crw-r--r--  dev/null      1,3
brw-r--r--  dev/ram0      1,0
crw-r--r--  dev/tty1..4   4,1 4,2 4,3 4,4
```

They are real nodes in the cpio, so **unpacking as a non-root user loses them** — `cpio` silently
creates empty regular files instead. Repacking that tree yields an initramfs that cannot open its own
console. Anything that round-trips this file must do so as root, or preserve the nodes through
`cpio`'s `--owner`/pseudo-file mechanisms.

## The eight static binaries

All eight are **32-bit i386, on every one of the four images**, including the 64-bit builds. One blob
set serves all targets.

| | size | |
|---|---|---|
| `busybox` | 739,784 | **v1.26.2, 2017-12-14.** The only one not UPX-packed |
| `blkid` | 103,624 | util-linux. Shadows busybox's applet of the same name |
| `eject` | 68,848 | shadows busybox's applet |
| `ncurses-menu` | 77,964 | the session and device pickers |
| `xfs_growfs` | 238,128 | grows a perch container when `perchsize=` increases |
| `mkfs.xfs.custom` | 19,444 | formats a new perch container |
| `@mount.dynfilefs` | 77,856 | FUSE sparse container for FAT/NTFS persistence |
| `@mount.httpfs2` | 123,500 | `from=http://…iso` |

### Why i386 matters

An x86-64 kernel can only execute these if it was built with `CONFIG_IA32_EMULATION=y`. Slax's is —
confirmed in upstream's `aufs-kernel-compile/config-x86_64`. **A `kernel.replace` that drops
`IA32_EMULATION` makes the entire initramfs unrunnable**, and the failure comes before any userspace
that could report it. This is the highest-consequence constraint in the whole image.

### The shadowing, and why it is deliberate

`blkid` and `eject` are real files occupying names busybox also provides as applets.
`initramfs_create` generates its symlinks inside a `[ ! -e ]` guard, so a name already taken by a real
file is skipped. That ordering is load-bearing: `livekitlib` parses `blkid -o full`, an option
busybox's `blkid` does not have. Regenerate the symlinks before installing the standalone binaries
and device detection breaks at boot with no diagnostic.

`bin/init` is a symlink to `../init`, which is what stops busybox's own `init` applet from shadowing
the boot script.

The accounting closes exactly, which is what makes it a useful invariant to assert:

```
busybox --list                      248 applets
symlinks in /bin pointing to it     245
applets with no symlink             3   blkid, eject  (real files shadow them)
                                        init          (bin/init -> ../init)
/bin total                          254 = 8 real files + 246 symlinks
```

Any busybox replacement has to reproduce it. A rebuild that regenerates symlinks from `--list`
without the `[ ! -e ]` guard produces 248 symlinks, clobbers `blkid` and `eject`, and breaks device
detection at boot — which is why the planned `initramfs-busybox` recipe gates on this invariant
rather than on the binary alone.

Details of the generation loop, including the fact that it scrapes busybox's human-readable usage
text rather than using `busybox --list`, are in
[`15-upstream/initramfs-create.md`](../15-upstream/initramfs-create.md).

## Kernel modules

301 on the 64-bit images, 300 on 32-bit, under `lib/modules/$(uname -r)/`:

| | 64-bit | 32-bit |
|---|---|---|
| module directory | `lib/modules/6.1.38` | `lib/modules/6.1.38-smp` |
| modules | 301 | 300 |

**Anything that hardcodes `6.1.38` breaks on 32-bit.** The 32-bit kernel carries `LOCALVERSION=-smp`,
so the path differs. Read it from the tree instead.

Breakdown on 64-bit Debian:

| subtree | count | |
|---|---|---|
| `drivers/net` | 163 | `ethernet/` and `phy/` only — **zero wireless drivers** |
| `fs/nls` | 48 | codepages, for FAT volume names |
| `drivers/usb` | 37 | storage, host, common, core |
| `drivers/nvme` | 10 | |
| `net/ipv6` | 4 | |
| `lib` | 4 | crc helpers |
| `drivers/{infiniband,hid}` | 4 each | |
| `fs/fuse` | 3 | dynfilefs and httpfs2 need it |
| everything else | 1–2 each | |

The four that make Slax work:

```
fs/aufs/aufs.ko            the union
fs/overlayfs/overlay.ko    the fallback union
drivers/block/loop.ko      every bundle is a loop mount
drivers/block/zram/zram.ko 512 MB compressed swap
```

`fs/ntfs3/ntfs3.ko` is there so a Windows partition can hold perch data. **squashfs, isofs, ext4 and
vfat are absent because they are built into the kernel**, not because they are unsupported.

The module set is chosen by directory, not by name — `initramfs_create` copies whole subtrees with
dependency closure. The 64/32 difference is 21 modules, all in `drivers/net`: the 64-bit build has 11 the 32-bit lacks
(Cavium `thunder_*`/`nicpf`/`liquidio`, Hyper-V `pci-hyperv-intf`, Microsoft `mana`) and the 32-bit
build has 10 the 64-bit lacks, all ISA-era NICs (`3c509`, `3c515`, `ne`, `wd`, `smc9194`,
`smc-ultra`, `lance`, `8390p`, `pch_gbe`, `ptp_pch`).

## Differences across the four images

| | |
|---|---|
| Debian ↔ Slackware | Slackware adds `usr/share/terminfo/l/linux` (4 entries). Nothing else |
| 64-bit ↔ 32-bit | the module directory name, and 21 network drivers |

Everything else — `/init`, `livekitlib`, `config`, `shutdown`, all eight binaries, all 246 applet
symlinks, `/etc/passwd`, the device nodes — is byte-identical on all four. **One rebuilt initramfs
userland serves every target**, which is the finding that makes an `initramfs-busybox` recipe
tractable: build one i386 static binary, use it four times.
