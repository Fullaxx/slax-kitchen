# The initramfs userland

Eight binaries, 245 applet symlinks, four shell scripts and 301 kernel modules. This is the complete
software inventory of early boot; the file format and layout are in
[`10-anatomy/initramfs.md`](../10-anatomy/initramfs.md).

**The entire userland is identical on all four images.** Only `lib/modules/<version>/` differs, and
on Slackware a single extra terminfo file. One rebuilt userland serves every target.

## busybox

```
BusyBox v1.26.2 (2017-12-14 11:24:25 EST) multi-call binary.
sha256 9eb1c731…64de    739,784 B    i386, static, not UPX-packed
```

**Nine years old at the time of writing**, and byte-identical across all four ISOs. It reports 248
applets, of which 245 have symlinks in `/bin`:

```
busybox --list                      248
symlinks in /bin pointing to it     245
applets with no symlink             3   blkid, eject  (shadowed by real files)
                                        init          (bin/init -> ../init)
```

That accounting closes exactly, which makes it a useful invariant to assert against any replacement.

Known reachable issues in 1.26.2 include **CVE-2017-16544** (terminal-escape injection via `ash` tab
completion — and `/init` calls `debug_shell` six times) and **CVE-2022-48174** (`ash` stack
overflow). It also predates `CONFIG_TIME64`, so `date -r` on a post-2038 mtime already misbehaves
today.

The 55 applets the boot scripts actually use, extracted from `/init`, `livekitlib` and `/shutdown`,
include several that a bare `defconfig` does not enable: `switch_root`, `pivot_root`, `mdev`,
`modprobe`, `rmmod`, `losetup`, `mkswap`, `swapon`, `mountpoint`, `tac`, `seq`, `xargs`, `cpio`,
`tftp`, `wget`, `udhcpc`, `ifconfig`, `route`, `fdisk`, `mknod`, `chroot`.

## The seven helpers

All UPX-packed, all i386 static, all byte-identical across the four images. Upstream's
`initramfs/static/README` says only *"To rebuild these static binaries, use buildroot"* — **no build
configuration is published for any of them**, and only one has in-tree source.

| | size | | source |
|---|---|---|---|
| `blkid` | 103,624 | util-linux. Shadows busybox's applet — `livekitlib` parses `blkid -o full`, which busybox's does not support | none in tree |
| `eject` | 68,848 | shadows busybox's applet; used by `/shutdown` | none in tree |
| `ncurses-menu` | 77,964 | the `from=ask` device picker and the `perchdir=ask` session picker | [Tomas-M/ncurses-menu](https://github.com/Tomas-M/ncurses-menu) |
| `xfs_growfs` | 238,128 | grows a perch container when `perchsize=` is raised | none in tree |
| `mkfs.xfs.custom` | 19,444 | formats a new perch container | **`tools/mkfs.xfs.custom.c`** |
| `@mount.dynfilefs` | 77,856 | FUSE sparse container for FAT/NTFS persistence | [Tomas-M/dynfilefs](https://github.com/Tomas-M/dynfilefs) |
| `@mount.httpfs2` | 123,500 | `from=http://…iso` | [Tomas-M/httpfs2-enhanced](https://github.com/Tomas-M/httpfs2-enhanced) |

The `@` prefix on the last two is not decorative — it is how `mount` finds a helper for a type, and
also why they sort to the top of a directory listing.

**`blkid`, `eject` and `xfs_growfs` are prebuilt blobs with neither source nor build config in the
repository.** Reproducing them means rebuilding from the upstream projects with buildroot and
accepting that the result will not be byte-identical. Only `mkfs.xfs.custom` has source in tree.

### The shadowing is load-bearing

`blkid` and `eject` are real files occupying names busybox also provides. `initramfs_create`
generates applet symlinks inside a `[ ! -e ]` guard, so a name already taken is skipped — and that
ordering is what keeps the better binary. Regenerate the symlinks after installing the standalone
binaries and `find_data` breaks at boot, silently, because busybox's `blkid` has no `-o full`.

## The four scripts

| | size | |
|---|---|---|
| `/init` | 2,317 | eighteen function calls. [`10-anatomy/livekit-init.md`](../10-anatomy/livekit-init.md) |
| `/lib/livekitlib` | 28,608 | 49 functions. [`15-upstream/livekitlib-reference.md`](../15-upstream/livekitlib-reference.md) |
| `/lib/config` | 1,778 | `LIVEKITNAME=slax`, `NETWORK=true`, `BEXT=sb`, `MKMOD`, `LMK` |
| `/shutdown` | 2,504 | [`10-anatomy/shutdown.md`](../10-anatomy/shutdown.md) |

`/init`, `livekitlib` and `/shutdown` are **byte-identical to their copies in
`vendor/linux-live/initramfs/`**. `config` differs by exactly the two lines the Slax build patches
in. That is the proof that the public repository is the real build system.

## Kernel modules

| | 64-bit | 32-bit |
|---|---|---|
| directory | `lib/modules/6.1.38` | `lib/modules/6.1.38-smp` |
| count | 301 | 300 |

```
drivers/net             163    ethernet/ and phy/ only — zero wireless
fs/nls                   48    codepages, for FAT volume labels
drivers/usb              37    storage, host, common, core
drivers/nvme             10
net/ipv6                  4
lib                       4    crc helpers
drivers/infiniband        4
drivers/hid               4    usbhid
fs/fuse                   3    dynfilefs and httpfs2
everything else         1–2    aufs, overlay, loop, zram, ntfs3, …
```

The set is chosen by **directory**, not by name: `initramfs_create` copies whole subtrees with
dependency closure. That is why `drivers/net` is so large and why wireless is absent — only
`drivers/net/ethernet` and `drivers/net/phy` are copied.

The 64/32 difference is 21 modules, all network:

| only 64-bit | only 32-bit |
|---|---|
| `thunder_bgx`, `thunder_xcv`, `nicpf`, `nicvf`, `cavium_ptp`, `liquidio`, `liquidio_vf`, `octeon_ep`, `mana`, `pci-hyperv-intf`, `dca` | `3c509`, `3c515`, `8390p`, `lance`, `ne`, `pch_gbe`, `ptp_pch`, `smc9194`, `smc-ultra`, `wd` |

Server and hypervisor NICs on one side, ISA-era cards on the other.

## Other files

```
/etc/passwd                      root::0:0::/root:/bin/sh    ← empty password field
/etc/modprobe.d/local-loop.conf  options loop max_loop=32
/etc/fstab  /etc/mtab            empty placeholders
/dev/{console,null,ram0,tty1..4} seven real device nodes
/sbin -> bin
usr/share/terminfo/l/linux       Slackware only
```

The empty password in `/etc/passwd` matters only inside the initramfs, where there is no login — the
booted system's credentials come from a bundle. `max_loop=32` caps the number of bundles plus perch
containers that can be mounted at once, which is generous but not unlimited.

## Replacing busybox

Tractable, because one i386 static binary serves all four targets — and because **the shipped 1.26.2
blob executes directly on an x86-64 host**, so parity tests are milliseconds, not VM boots.

The constraints, in order of how badly they bite:

1. **Static, i386, no interpreter.** There is no dynamic loader in the initramfs.
2. **musl, not glibc.** glibc's `getpwnam`/`getaddrinfo` `dlopen()` `libnss_*.so.2` at runtime; that
   file does not exist here, so name and DNS lookups fail *silently*.
3. **Preserve the shadowing.** See above.
4. **`CONFIG_LONG_OPTS=y`** — `livekitlib` uses `date --date`, which needs it.
5. **`CONFIG_EGREP` / `CONFIG_FGREP`** — renamed since 1.26.2 (was `FEATURE_GREP_EGREP_ALIAS`), so
   porting an old config silently drops them. `livekitlib` uses `egrep` five times and `fgrep` once.
6. **`CONFIG_TC=n`** — `default y`, but a hard build failure against kernel ≥ 6.8 headers.
7. **`CONFIG_LFS` and `CONFIG_TIME64`** — perch containers exceed 2 GB, and the current blob's
   32-bit `time_t` already misbehaves.
8. **`CONFIG_FEATURE_UTMP`/`WTMP=n`** — musl has no `utmpx`; link failure otherwise.

The output-parsing sites in `livekitlib` were audited against 1.38.0 and are mostly safe; the
`losetup -a | cut -d : -f 1` format is unchanged since 1.26.2. The one genuine pre-existing hazard is
`df "$X" | tail -n 1 | cut -d " " -f 1`, which busybox wraps for device names over 20 characters —
already broken today for `/dev/mapper/*`, and worth hardening with `df -P` regardless of any version
bump.
