# Every file on the ISO

Two entries at the root, and 37 files in 6 directories under `/slax/`. This page says what each one is for and, where
it applies, that it is never used.

```sh
xorriso -indev isos/slax-64bit-debian-12.2.0.iso -find / -exec lsdl --
```

## Root

| | | |
|---|---|---|
| `/readme.txt` | 823 B | instructions for making a USB stick bootable under Windows. Explicitly disposable — its own last line says so |
| `/slax/` | | everything else |

The `slax` name is not cosmetic. It is `LIVEKITNAME` from `lib/config`, it is compiled into
`isolinux.bin`, and `find_data` searches for `*.sb` relative to it. Renaming the directory requires
patching the loader — see [`bootloader-payloads.md`](bootloader-payloads.md).

## `/slax/` — three directories

| | |
|---|---|
| `boot/` | kernel, initramfs, bootloaders, menus — 31 files plus `EFI/Boot/` |
| `modules/` | the squashfs bundles — 6 or 7 files |
| `changes/` | **empty on the ISO**; where perch sessions are written on writable media |

`changes/` exists so that a plain `cp -a /slax/` to a USB stick produces a tree persistence can
immediately use. On read-only media it stays empty and is never written.

There is **no `rootcopy/`** on any shipped ISO, even though `/init` calls `copy_rootcopy_content`
unconditionally. The function no-ops when the directory is absent, so creating `/slax/rootcopy/` is
purely additive — which is what makes the `rootcopy-overlay` recipe the cheapest customization
available. See [`docs/50-cookbook/rootcopy-overlay.md`](../50-cookbook/rootcopy-overlay.md).

## `/slax/boot/` — what is actually used

Marked **live** if something reads it during a normal boot of this medium.

### Kernel and init

| file | size (64-bit Debian) | |
|---|---|---|
| `vmlinuz` | 12,017,856 | **live.** 6.1.38 with aufs patched in. Byte-identical between Debian and Slackware at the same word size |
| `initrfs.img` | 8,872,472 | **live.** xz'd newc cpio. See [`initramfs.md`](initramfs.md) |

### Menus and branding

| file | size | |
|---|---|---|
| `isolinux.cfg` | 1,202 | **live on CD.** Two entries: `default` and `toram`; the two session entries are `MENU DISABLED` |
| `syslinux.cfg` | 1,537 | **live on USB/HDD.** Four entries — resume, new session, ask, toram |
| `help.txt` | 1,384 | **live.** Shown on `F1`. Documents 6 boot parameters out of roughly 14 |
| `bootlogo.png` | 23,368 | **live.** Menu background |
| `zblack.png` | 6,447 | **live.** Background behind the `F1` help screen |

The two `.cfg` files differ in exactly one respect that matters: **`isolinux.cfg` never sets
`perchdir=`, so booting from CD has no persistence at all.** The disabled entries are there to keep
the menu the same height on both media.

### The BIOS CD loader

| file | size | |
|---|---|---|
| `isolinux.bin` | 40,960 | **live on CD.** ISOLINUX 6.03, loaded by the BIOS via El Torito |
| `isolinux.boot` | 2,048 | **live on CD.** The El Torito catalog itself, not a program |
| `ldlinux.c32` | 116,552 | **live.** The COM32 core, loaded by `isolinux.bin` |
| `libcom32.c32` | 181,944 | **live.** COM32 runtime |
| `libutil.c32` | 23,628 | **live.** COM32 utilities |
| `vesamenu.c32` | 26,684 | **live.** The graphical menu named by `UI` in both `.cfg` files |

### Installers — programs that run on your machine, not loaders

This is the group most often misread. None of these is loaded at boot; each one *writes* a
bootloader onto a disk.

| file | size | runs on |
|---|---|---|
| `bootinst.sh` | 3,413 | Linux. Mode `0755` on the ISO. Picks `extlinux.x32`/`x64` by `uname -m`, installs, writes the MBR, sets the boot flag, then **moves** `EFI/Boot` up to the filesystem root |
| `bootinst.bat` | 2,679 | Windows. An sh/batch polyglot; escalates via `runadmin.vbs`, guards against the same physical disk via `samedisk.vbs` |
| `extlinux.x32` | 208,480 | Linux, 32-bit. ELF, mode `0755` |
| `extlinux.x64` | 209,424 | Linux, 64-bit. ELF, mode `0755` |
| `syslinux.com` | 126,511 | DOS |
| `syslinux.exe` | 243,712 | Windows. PE32 |
| `runadmin.vbs` | 126 | UAC elevation helper for `bootinst.bat` |
| `samedisk.vbs` | 2,076 | refuses to install onto the disk Windows booted from |
| `mbr.bin` | 440 | **data, not a program.** The 440-byte SYSLINUX MBR that `bootinst.sh` `dd`s to LBA 0 |

### Never used on this medium

| file | size | |
|---|---|---|
| `pxelinux.0` | 46,909 | PXELINUX 6.03. Only reachable through the `pxe` helper inside the running system |
| `EFI/Boot/bootx64.efi` | 200,992 | **byte-identical to `syslinux.efi`** |
| `EFI/Boot/syslinux.efi` | 200,992 | the UEFI loader |
| `EFI/Boot/ldlinux.e64` | 139,616 | its COM32 core |
| `EFI/Boot/libcom32.c32` | 201,336 | |
| `EFI/Boot/libutil.c32` | 24,464 | |
| `EFI/Boot/menu.c32` | 32,064 | the text menu — note the BIOS side ships `vesamenu` but not `menu` |
| `EFI/Boot/vesamenu.c32` | 32,776 | |
| `EFI/Boot/syslinux.cfg` | 32 | one line: `INCLUDE /slax/boot/syslinux.cfg` |

All nine EFI files — 832,272 bytes — are inert while the image is an ISO, for the three reasons in
[`el-torito.md`](el-torito.md). They become live only after `bootinst.sh` moves them to `/EFI/Boot/`
on a FAT partition.

There is **no `bootia32.efi` on any of the four images**, including the two 32-bit ones. A 32-bit
UEFI machine cannot boot Slax by any supported route.

## `/slax/modules/`

64-bit images; the 32-bit sizes are in
[`docs/30-inventory/manifests/`](../30-inventory/manifests/).

| | Debian 12.2.0 | Slackware 15.0.4 |
|---|---|---|
| `01-core.sb` | 128,249,856 | 124,399,616 |
| `01-firmware.sb` | 95,547,392 | 83,775,488 |
| `02-xorg.sb` | 61,775,872 | 15,171,584 |
| `03-desktop.sb` | 39,559,168 | 20,312,064 |
| `04-apps.sb` | 4,390,912 | 4,603,904 |
| `05-chromium.sb` | 82,903,040 | 120,172,544 |
| `06-devel.sb` | — | 84,852,736 |

Details, and the numbering rule that governs precedence, in
[`bundles-squashfs.md`](bundles-squashfs.md).

## Total file count

37 files under `/slax/` plus `/readme.txt` on a Debian image; 38 on Slackware, which adds
`06-devel.sb`. The path set is otherwise **identical on all four images** — verified by diffing the
`xorriso -find` output pairwise.
