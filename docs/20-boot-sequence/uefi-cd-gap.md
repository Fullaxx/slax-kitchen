# The UEFI gap

**No UEFI machine will boot a stock Slax ISO** — not from a DVD, not from a `dd`'d USB stick. This
page explains precisely why, because the reason is three separate problems that each look like the
whole answer.

It is the most consequential finding in this documentation set, and it is what the `uefi-bootable`
recipe exists to fix.

## The three reasons

### 1 · The boot catalog has no EFI entry

El Torito lets an image declare several boot entries, one per firmware platform. A UEFI firmware
looks for a section header with platform id `0xEF`. Slax's catalog is 64 bytes long and both entries
are BIOS:

```
entry @+00: 01 00 …                          validation, platform 0x00 = x86 BIOS
entry @+32: 88 00 00 00 00 00 04 00 2e …     default entry, no-emulation, LBA 46
entry @+64: (zero)
```

There is no section header and no second entry. Byte dump and verification in
[`10-anatomy/el-torito.md`](../10-anatomy/el-torito.md).

Corroborated upstream: the `mkisofs` invocation in `vendor/linux-live/build` has no
`-eltorito-alt-boot` and no `-e`, so no second entry is ever created.

### 2 · There is no EFI System Partition

An EFI entry points at a **FAT image**, because that is the only filesystem UEFI firmware is required
to implement. Slax's ISO has no such image anywhere — no `efi.img`, no embedded FAT, and no partition
table that could describe one.

`/slax/boot/EFI/Boot/` is a directory *inside ISO9660*, which firmware cannot read.

### 3 · `syslinux.efi` cannot read ISO9660 anyway

Even if the firmware somehow reached `bootx64.efi`, it would stop there. `syslinux.efi` implements
FAT and nothing else. It could not load `/slax/boot/vmlinuz` from an ISO9660 volume.

This third reason is the one that decides the fix. Any solution has to bring a loader that can read
ISO9660 — which SYSLINUX does not do on UEFI.

## Why the shipped EFI files exist at all

They are staged for `bootinst.sh`, which **moves** them onto a FAT filesystem where all three
problems vanish: FAT is readable by firmware, `/EFI/Boot/` is where firmware looks, and
`syslinux.efi` can read the filesystem it is now on.

```sh
mv "EFI/Boot" "$BOOT/../../EFI/"
```

So upstream's UEFI support is real — it is just conditional on `bootinst`, and therefore unavailable
to anyone who burns the ISO or `dd`s it. See [`uefi-usb-hdd.md`](uefi-usb-hdd.md).

## The fix

The `uefi-bootable` recipe addresses all three, and every step runs **unprivileged** — nothing is
ever mounted:

```sh
# 1. a GRUB that can read ISO9660 (syslinux.efi cannot)
grub-mkstandalone -O x86_64-efi -o BOOTX64.EFI \
  --modules="part_gpt part_msdos fat iso9660 normal linux configfile \
             search search_fs_file all_video echo test" \
  "boot/grub/grub.cfg=early.cfg"

# 2. an ESP as a plain FAT image — mkfs.vfat -C and mtools, never mounted
#    (size is computed from the GRUB payload plus FAT12 slack, floor 1440 KiB)
mkfs.vfat -C -F 12 -n SLAXEFI work/iso/boot/efi.img $KIB
mmd   -i work/iso/boot/efi.img ::/EFI ::/EFI/BOOT
mcopy -i work/iso/boot/efi.img BOOTX64.EFI ::/EFI/BOOT/BOOTX64.EFI

# 3. a second El Torito entry pointing at it
xorriso -as mkisofs -o out.iso -J -R -D -A slax -V slax -input-charset utf-8 \
  -b slax/boot/isolinux.bin -c slax/boot/isolinux.boot \
  -no-emul-boot -boot-load-size 4 -boot-info-table \
  -eltorito-alt-boot -e boot/efi.img -no-emul-boot \
  work/iso
```

The GRUB image is built with an embedded `early.cfg` that finds the ISO by a file that is certain to
be on it, then hands over to the real menu:

```
search --no-floppy --file --set=root /slax/boot/vmlinuz
configfile ($root)/boot/grub/grub.cfg
```

`kitchen` generates `/boot/grub/grub.cfg` **from `isolinux.cfg`** rather than hardcoding entries, so
anything another recipe added to the BIOS menu appears in the UEFI one automatically. That is the
reason `uefi-bootable` should be the last recipe in a profile.

`mkfs.vfat -C` **creates** a filesystem image as an ordinary file, and `mtools` writes into it
without any kernel involvement. That combination is what makes the whole recipe possible in an
unprivileged container — no loop device, no `CAP_SYS_ADMIN`, no root.

The resulting ISO has two El Torito entries:

```
validation      platform=x86-BIOS
initial         platform=x86-BIOS  bootable=True media=no-emulation sectors=4 lba=46
section-header  platform=UEFI      entries=1
section-entry   platform=UEFI      bootable=True media=no-emulation
UEFI bootable True
```

**BIOS booting is unaffected.** The original entry is untouched, so the same image boots on both
firmwares. That is asserted by `tests/structure/iso_assert.py` and verified by booting under both
SeaBIOS and OVMF.

Adding [`isohybrid`](../50-cookbook/isohybrid.md) on top gives an image that also boots when `dd`'d
to a stick, on both firmwares — four boot paths from one file.

Full recipe: [`docs/50-cookbook/uefi-bootable.md`](../50-cookbook/uefi-bootable.md).

## Why upstream has not done this

Speculation would be unhelpful, but two facts are relevant. Upstream's documented USB workflow is
`bootinst`, which already provides UEFI; and the project has shipped nothing since 2023-10-10. The
gap is real, the fix is small, and it is offered here as an addition rather than a criticism.
