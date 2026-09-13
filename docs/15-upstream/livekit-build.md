# `build` — the Linux Live Kit master script

`vendor/linux-live/build`, 102 lines. Run as root on a **real installed system**, which it squashes
into a live image.

## Sequence

| Lines | What |
|---|---|
| 4–12 | set `PATH`, `cd` to its own directory, source `config` and `livekitlib` |
| 15 | `allow_only_root` — "only root can read all files from your system" |
| 18–38 | check `xz`, check `mksquashfs` supports `-Xdict-size`, find `mkisofs` **or** `genisoimage` |
| 41–46 | build the initramfs, **unless `$SKIPINITRFS` is set** |
| 49–57 | create `$LIVEKITDATA/$LIVEKITNAME/{boot,changes,modules}` and move the initramfs in |
| 60–63 | copy `bootfiles/`, rewriting `/boot/` → `/slax/boot/` in `syslinux.cfg` and `bootinst.bat`; copy `$VMLINUZ` |
| 66–69 | UEFI: copy `syslinux.efi` as `bootx64.efi` plus the `.c32` set, and write an `EFI/Boot/syslinux.cfg` |
| 72–80 | build `01-core.sb` from `$MKMOD`, **unless `$SKIPCOREMOD` is set** |
| 86 | generate `readme.txt` from `bootinfo.txt`, converting to CRLF |
| 88–96 | **write shell scripts that build the ISO and ZIP** — it does not build them |

## `01-core.sb` is your running system

```sh
COREFS=""
for i in $MKMOD; do
   if [ -d /$i ]; then COREFS="$COREFS /$i"; fi
done
mksquashfs $COREFS .../01-core.$BEXT -comp xz -b 1024K -Xbcj x86 -always-use-fragments -keep-as-directory
```

`$MKMOD` is `bin etc home lib lib64 libx32 opt root sbin srv usr var`. Every one of those that
exists at `/` goes straight into the bundle. That is the whole mechanism — no chroot, no package
list, no manifest. `-keep-as-directory` preserves the top-level directory names.

## It does not build an ISO

```sh
echo cd $LIVEKITDATA '&&' $MKISOFS -o "$TARGET/$LIVEKITNAME-$ARCH.iso" -v -J -R -D \
  -A "$LIVEKITNAME" -V "$LIVEKITNAME" \
  -no-emul-boot -boot-info-table -boot-load-size 4 \
  -b "$LIVEKITNAME"/boot/isolinux.bin -c "$LIVEKITNAME"/boot/isolinux.boot . \
  > $TARGET/gen_"$LIVEKITNAME"_iso.sh
```

It **echoes** the command into `/tmp/gen_slax_iso.sh` and tells you to run it. That generated line is
the authority for how a Slax ISO is mastered, and `kitchen pack` reproduces it — note `-R` rather
than `-r`, `-D`, and the absence of any UEFI El Torito argument, which is precisely why
[the stock ISO cannot boot on UEFI](../30-inventory/known-upstream-bugs.md).

## The two skip flags

`SKIPINITRFS` and `SKIPCOREMOD` are never set by `build` itself — both flavours set them before
sourcing it:

- **Debian** sets `SKIPINITRFS=true`, runs `build`, then runs `initramfs_create` separately. The
  comment says why: the initramfs must be built *after* the root filesystem is final, so the aufs
  modules it copies are the right ones.
- **Slackware** sets **both**, because its `01-core.sb` comes from `modbuild`, not from `/`.

## UEFI, such as it is

Lines 66–69 copy `syslinux.efi` to `bootx64.efi` and write a one-line `EFI/Boot/syslinux.cfg`. This
is real, but only reachable from a **FAT** filesystem — syslinux's EFI build cannot read iso9660,
and `bootinst.sh` relocates the directory to the root of a FAT stick at install time. On the ISO it
is unreachable data. See [uefi-bootable](../50-cookbook/uefi-bootable.md).
