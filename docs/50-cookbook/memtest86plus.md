# `memtest86plus` — add a memory tester to the boot menu

**Status: runtime-verified** — on BIOS and UEFI, both by booting the built ISO and watching
Memtest86+ actually run.

```sh
kitchen unpack isos/slax-64bit-debian-12.2.0.iso
kitchen apply memtest86plus uefi-bootable    # uefi-bootable LAST -- see below
kitchen pack
```

Adds Memtest86+ **8.10** (2026-05-16) as a boot-menu entry on all four supported targets.

## One binary, both firmwares

Memtest86+ 8.10 ships each x86 build as a **single file that is simultaneously a Linux bzImage and a
PE32+ EFI application**. Verified directly:

```
MZ header (EFI stub): True   HdrS at 0x202   boot sig: 55aa
PE signature at 0x471: b'PE\0\0'  -> bootable as an EFI application
```

So one file covers both paths — syslinux boots it on BIOS, GRUB boots the *same* file with `linux`
on UEFI. Most guides, and older Memtest86+ releases, still assume separate `.bin` and `.efi`.

The recipe picks `mt86p_810_x86_64` or `mt86p_810_i586` via `when: arch==…`.

## ⚠️ `LINUX`, not `KERNEL`

The menu entry uses `linux:` deliberately. **With `kernel:`, syslinux 6.03 loads the image and then
shows a black screen forever — no error, no output, no timeout.** `KERNEL` auto-detects the image
type and picks the wrong handler for this image; `LINUX` forces the Linux boot protocol.

This cost real debugging time, so it is worth stating plainly: the failure is *silent*. Both
spellings were verified in QEMU, and the recipe carries a comment so nobody "tidies" it back.

## Source and pinning

Upstream publishes **no loose binaries** — the GitHub release for v8.10 has zero assets — so the only
source is `mt86plus_8.10.binaries.zip` from memtest.org. `boot.payload` gained an `extract:` field
for this:

```yaml
- verb: boot.payload
  src: "https://www.memtest.org/download/v8.10/mt86plus_8.10.binaries.zip"
  sha256: "7e6c5162cb84ab959aeb9d13c9cfd6976b0dec3b34936b73820b20c55eb26c29"
  extract: "mt86p_810_x86_64"
  dest: slax/boot/memtest.bin
```

The sha256 is checked against **the archive**, which is what upstream actually publishes, before
anything is taken out of it. The extracted binary's own hash was confirmed to match upstream's
(`11808288ea1ee332f7d89e683cef10fbcdf2d6585bb972688ed9fe0e3d30fc55`).

Archive type is detected from content, not filename — a download lands in a temp file named after its
destination (`memtest.bin.part`), so a name-based check would hand a zip to the tar reader.

## Apply `uefi-bootable` last

`boot.uefi` builds the GRUB menu by **parsing `isolinux.cfg`**, so it mirrors whatever entries exist
at the time it runs. List it after `memtest86plus` and the memory tester appears under UEFI too:

```
boot/grub/grub.cfg: mirrored 3 menu entries from isolinux.cfg
...
menuentry 'Memory test (Memtest86+ 8.10)' {
    linux /slax/boot/memtest.bin
}
```

Order it the other way and the BIOS menu gets the entry but the UEFI menu does not.

## Reaching it from the menu

Worth knowing when testing: the stock `isolinux.cfg` sets `MENU HIDDEN`, so on BIOS the menu is not
shown at all — press **Esc** during the 4-second window to reveal it. The two session entries are
`MENU DISABLED` and syslinux skips them during navigation, so Memtest86+ is **two** arrow-downs from
the top, not four.

`tests/boot/qemu_boot.py --keys` drives this: `'2s,esc,1s,down,down,0.5s,ret'` for BIOS,
`'7s,down,2s,down,1s,ret'` for UEFI. A bootloader menu is the one thing a serial log cannot capture,
since both isolinux and GRUB draw to the video console.

## Updating to a new release

Change `version`, update `zip_sha256`, and update the two `extract:` member names — they embed the
version (`mt86p_810_x86_64`). `kitchen apply` fails loudly on a hash mismatch rather than installing
something unexpected.
