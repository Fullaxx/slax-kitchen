# UEFI + USB stick or hard disk

Works, but only after `bootinst` has run. The files are on the ISO; they are in the wrong place and
on the wrong filesystem until something moves them.

## What UEFI needs

1. a **FAT** partition (the ESP, or on removable media any FAT partition will do),
2. containing `/EFI/BOOT/BOOTX64.EFI`,
3. reachable by the firmware's own FAT driver.

A stock Slax ISO satisfies none of these. It ships `/slax/boot/EFI/Boot/bootx64.efi` — right file,
wrong path, inside ISO9660. See [`uefi-cd-gap.md`](uefi-cd-gap.md).

## What `bootinst.sh` does about it

One line, at the very end:

```sh
mkdir -p "$BOOT/../../EFI"
mv "EFI/Boot" "$BOOT/../../EFI/"
```

`$BOOT` is `/slax/boot`, so this moves `slax/boot/EFI/Boot` to `<root>/EFI/Boot`. Afterwards the
stick looks like:

```
/EFI/Boot/bootx64.efi      ← firmware loads this
/EFI/Boot/syslinux.efi     ← byte-identical copy
/EFI/Boot/ldlinux.e64
/EFI/Boot/libcom32.c32  libutil.c32  menu.c32  vesamenu.c32
/EFI/Boot/syslinux.cfg     ← one line: INCLUDE /slax/boot/syslinux.cfg
/slax/boot/…               ← kernel, initramfs, menus
/slax/modules/…
```

FAT is case-insensitive, so `EFI/Boot/bootx64.efi` satisfies firmware looking for
`EFI/BOOT/BOOTX64.EFI`.

## The chain

```
UEFI firmware
 └─ mounts the FAT partition
     └─ \EFI\BOOT\BOOTX64.EFI          (= syslinux.efi, PE32+ x86-64, 200,992 B)
         ├─ loads ldlinux.e64
         ├─ reads \EFI\Boot\syslinux.cfg  →  INCLUDE /slax/boot/syslinux.cfg
         └─ vesamenu.c32 → vmlinuz + initrfs.img, via EFI file protocols
```

`bootx64.efi` and `syslinux.efi` are byte-identical:

```sh
cmp EFI/Boot/bootx64.efi EFI/Boot/syslinux.efi     # identical
```

One is the name firmware looks for; the other is the name SYSLINUX ships. Both are kept so the
directory works whether the firmware uses the fallback path or an NVRAM boot entry.

## One menu file, two firmwares

The one-line `INCLUDE` means BIOS and UEFI boots of the same stick read the **same four entries**
from `/slax/boot/syslinux.cfg`. Anything that edits that file — a serial console entry, a changed
default, extra `noload=` — applies to both at once.

That is also why there is no separate UEFI menu to maintain, and why the `boot.menu` verb targets
`isolinux.cfg` and `syslinux.cfg` rather than three files.

## Limits

**No Secure Boot.** There is no shim, no signed loader, and the kernel is unsigned. Secure Boot must
be disabled in firmware. Signing the chain means shipping a shim signed by Microsoft's UEFI CA plus
a signed GRUB and a signed kernel, and enrolling a MOK — possible, but it is an entirely different
project from customizing an ISO.

**No 32-bit UEFI.** There is no `bootia32.efi` on any of the four images, including the two 32-bit
ones. A 32-bit-UEFI machine — some older Atom tablets and a few netbooks — cannot boot Slax by any
supported route. Adding one means building `syslinux.efi` for `i386-efi` or shipping an i386 GRUB.

**The EFI payload is 32-bit-hostile in a second way**: the 32-bit ISOs ship the same x86-64
`BOOTX64.EFI`. It is dead weight there twice over.

**`syslinux.efi` cannot read ISO9660.** It is FAT-only. This is not a configuration problem — it is
why the `uefi-bootable` recipe uses GRUB 2 for the ISO case rather than reusing the shipped loader.
See [`uefi-cd-gap.md`](uefi-cd-gap.md).

## Troubleshooting

| symptom | cause |
|---|---|
| firmware skips the stick entirely | `bootinst` never ran, so there is no `/EFI/BOOT/` |
| *Security violation* / *not signed* | Secure Boot is on |
| menu appears, kernel does not load | `/slax/boot/` missing or incomplete — the `INCLUDE` resolved but its targets did not |
| boots on BIOS but not UEFI on the same stick | the partition is not FAT, or is not the first one the firmware examines |
| 32-bit tablet sees nothing | no `bootia32.efi`; unsupported |

To confirm the stick is shaped correctly:

```sh
ls /path/to/stick/EFI/Boot/bootx64.efi      # must exist at the root, not under slax/
findmnt -no FSTYPE /path/to/stick           # must be vfat
```
