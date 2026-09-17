# `initramfs_create` — assembling `initrfs.img`

`vendor/linux-live/initramfs/initramfs_create`, 164 lines. Builds the initramfs from the **running
system's** kernel modules plus nine committed static binaries.

## Sequence

| Lines | What |
|---|---|
| 13–42 | `copy_including_deps` — recursive copier following both `ldd` output *and* `modules.dep` |
| 44–46 | create the skeleton; `ln -s bin sbin` |
| 48–57 | copy the nine static binaries, `chmod a+x` |
| 58–59 | write `/etc/modprobe.d/local-loop.conf` with `options loop max_loop=32` |
| 61–68 | **generate applet symlinks by scraping busybox's usage text**, then `rm bin/init` |
| 70–76 | `mknod` for `console`, `null`, `ram0`, `tty1`–`tty4` |
| 81–133 | copy kernel modules by subsystem |
| 135 | copy `/usr/share/terminfo/l/linux` |
| 138–139 | **decompress** any `.ko.gz` / `.ko.xz` |
| 142–146 | trim `modules.order`, run `depmod -b` |
| 148–157 | minimal `/etc/passwd`, empty `mtab`/`fstab`, then `init`, `shutdown`, `livekitlib`, `config` |
| 160 | pack |

## The pack command

```sh
find . -print | cpio -o -H newc 2>/dev/null | xz -T0 -f --extreme --check=crc32 >$INITRAMFS.img
```

`--check=crc32` is **mandatory** — the kernel's built-in xz decompressor does not support CRC64,
which is xz's default. An initramfs built without it will not boot.

## ⚠️ Applet symlinks are scraped from help text

```sh
$INITRAMFS/bin/busybox | grep , | grep -v Copyright | tr "," " " | while read LINE; do
   for TOOL in $LINE; do
      if [ ! -e $INITRAMFS/bin/$TOOL ]; then ln -s busybox $INITRAMFS/bin/$TOOL; fi
   done
done
rm -f $INITRAMFS/{s,}bin/init
```

This parses busybox's **human-readable usage output**, which is not a stable interface. `busybox
--list` exists and has since long before the shipped 1.26.2. Any work replacing busybox should fix
this first — see [known-upstream-bugs](../30-inventory/known-upstream-bugs.md).

Two behaviours in that loop are load-bearing and must be preserved:

- The `[ ! -e ]` guard means the **standalone `blkid` and `eject` copied at lines 56 and 51 win** over
  busybox's applets of the same name. `livekitlib` parses `blkid -o full`, which busybox's applet
  does not support — so this ordering is not cosmetic.
- `rm -f bin/init` removes the busybox `init` applet so it cannot shadow `/init`, the script.

## Which kernel modules go in

By subsystem, from the build machine's `/lib/modules/$KERNEL`:

`fs/{aufs,overlayfs,ext2,ext3,ext4,fat,nls,fuse,isofs,ntfs,ntfs3,reiserfs,squashfs}`, anything
matching `crc32c`, `zsmalloc`, `zram`, `loop`, `usb/{storage,host,common,core}`, `usbhid`, `hid`,
`cdrom`, `scsi/{sr_mod,sd_mod,scsi_mod,sg}`, `ata`, `nvme`, `mmc`, `updates/`, and — only when
`NETWORK=true`, which Slax sets — `drivers/net/ethernet`.

`copy_including_deps` resolves dependencies transitively through `modules.dep`, so the actual list is
larger: **301 `.ko`** in the shipped 64-bit image.

Note squashfs, isofs, ext4 and vfat are *also* built into Slax's kernel, so the modules here are
belt-and-braces.

## The nine static binaries

`initramfs/static/` holds committed blobs:

```
busybox  blkid  eject  mc  ncurses-menu  mkfs.xfs.custom  xfs_growfs
mount.dynfilefs  mount.httpfs2
```

Slax 12.2.0's initramfs has eight of them. `mc` was added to this directory, and to the copy list,
in `02c040b` on 2023-10-30 — three weeks after the release.

`mount.dynfilefs` and `mount.httpfs2` are installed with an `@` prefix — `/bin/@mount.dynfilefs` —
and `livekitlib` invokes them by that name directly, so busybox's `mount` never has to exec a
`/sbin/mount.TYPE` helper.

The whole README for these is two lines:

> To rebuild these static binaries, use buildroot <buildroot.org>
> Most of the binaries provided here are also packed with upx

Verified: all eight helpers are UPX-packed; **busybox alone is not**. All nine are **32-bit i386**,
even in the 64-bit ISO — one blob set serves both builds, which is why the x86_64 kernel needs
`CONFIG_IA32_EMULATION`. No build configuration is published for any of them.

## Post-release commits

Two changes since Slax 12.2.0 shipped on 2023-10-09: `02c040b` (2023-10-30) copies the new `mc`
binary into the initramfs, and `9825e93` (2024-11-14) adds the `.ko.xz` decompression at line 139
for RHEL-based build hosts. **Neither has appeared in a release.**
