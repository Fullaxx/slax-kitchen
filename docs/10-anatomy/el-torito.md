# El Torito, and the BIOS-only finding

The single most consequential fact about a stock Slax ISO is recorded in 64 bytes at LBA 45: **it
declares exactly one boot entry, for x86 BIOS.** There is no EFI section and no EFI System
Partition, so no UEFI machine will boot it from optical media or from a `dd`'d stick.

This is not a defect report — upstream's supported USB path is `bootinst`, which produces a FAT
filesystem that `syslinux.efi` *can* read. It is a statement about what the ISO file itself can do.

## The boot catalog

`/slax/boot/isolinux.boot` is a one-block file whose extent is the catalog; the Boot Record
descriptor at LBA 17 points at it.

```
entry @+00: 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
            00 00 00 00 00 00 00 00 00 00 00 00 aa 55 55 aa
entry @+32: 88 00 00 00 00 00 04 00 2e 00 00 00 00 00 00 00
            00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00
```

**Bytes 0–31 — validation entry**

| field | value | |
|---|---|---|
| header id | `01` | required |
| platform id | `00` | **x86 BIOS** (`ef` would be UEFI) |
| id string | empty | genisoimage writes nothing |
| checksum | `0x55aa` | chosen so the 16 little-endian words sum to `0x0000` |
| key | `0x55aa` | required |

**Bytes 32–63 — default (initial/default) entry**

| field | value | |
|---|---|---|
| boot indicator | `0x88` | bootable |
| media type | `0` | **no emulation** |
| load segment | `0x0000` | default, i.e. `0x07C0` |
| system type | `0x00` | |
| sector count | `4` | 4 × 512 B = 2 KiB loaded by the BIOS |
| load LBA | `46` | where `isolinux.bin` sits |

**Bytes 64 onward are zero.** A UEFI-bootable ISO needs a section header (header id `0x91`,
platform `0xEF`) and at least one more entry there. There is none, on any of the four images.

## What a UEFI firmware does with this

Nothing. UEFI does not consult the El Torito catalog for BIOS entries; it looks for a `0xEF`
platform entry pointing at a FAT image containing `/EFI/BOOT/BOOTX64.EFI`. Finding no such entry, it
falls through to the next boot device.

The `/slax/boot/EFI/Boot/` directory on the ISO looks like it should help and does not:

- it is at `/slax/boot/EFI/Boot/`, not the `/EFI/BOOT/` that firmware searches;
- it is inside ISO9660, and `syslinux.efi` can only read FAT;
- nothing in the boot catalog points at it.

It is there for `bootinst.sh`, which **moves** it to `<root>/EFI/Boot/` on a FAT USB stick:

```sh
mkdir -p "$BOOT/../../EFI"
mv "EFI/Boot" "$BOOT/../../EFI/"
```

On the ISO it is 832,272 bytes of dead weight. (Note that this is `mv`, not `cp` — running `bootinst.sh`
a second time from the same tree finds nothing to move.)

The `uefi-bootable` recipe fixes the ISO case by building a real ESP with `mkfs.vfat -C` plus
`mtools` and adding a second El Torito entry. See
[`docs/50-cookbook/uefi-bootable.md`](../50-cookbook/uefi-bootable.md).

## The boot-info-table

`isolinux.bin` is written with `-boot-info-table`, so `genisoimage` **patches 56 bytes into the file
after it is laid out**, at offsets 8–63:

```
offset 0  : fa ea 6c 7c 00 00 90 90     untouched loader bytes
offset 8  : pvd_lba   = 16
offset 12 : file_lba  = 46
offset 16 : file_len  = 40960
offset 20 : checksum  = 0xe5d3e1ef
offset 24 : 40 reserved bytes, all zero
```

The checksum is the 32-bit sum of every little-endian dword **from offset 64 to the end of the
file** — the first 64 bytes are excluded because they include the patch itself. Verified on the
shipped image:

```
recomputed sum of dwords from offset 64 = 0xe5d3e1ef  →  MATCH
```

`isolinux.bin` uses `file_lba` to locate itself on the disc without knowing a path, which is how it
then finds `ldlinux.c32` and the configuration file.

### Why this matters when repacking

The table is computed over the file's *content*, which never changes, and the file's *position*,
which does. So:

- **`isolinux.bin` on disk and `isolinux.bin` inside the ISO are different files.** A byte comparison
  of an extracted copy against `vendor/`'s copy will always differ in those 56 bytes. Both
  `kitchen diff` and `tests/structure/iso_assert.py` exclude the first 64 bytes for exactly this
  reason, and check the checksum instead of the bytes.
- **Never patch `isolinux.bin` after mastering.** Editing a byte past offset 64 invalidates the
  checksum, and ISOLINUX refuses to boot. If you need to change the embedded directory name (see
  [`bootloader-payloads.md`](bootloader-payloads.md)), do it to the source file and re-master.

`lib/isoparse.py` recomputes and reports this on every image:

```
boot-info-table pvd_lba=16 file_lba=46 file_len=40960 checksum=0xe5d3e1ef consistent=True
```

## No MBR, no GPT

The 32 KiB system area (LBA 0–15) is **entirely zero on all four images** — measured, not assumed:

```sh
head -c 32768 isos/slax-64bit-debian-12.2.0.iso | tr -d '\0' | wc -c   # → 0
```

So there is no MBR boot code at offset 0, no `0x55AA` signature at 510, no partition table, and no
protective GPT at LBA 1 or at the end of the image.

**`dd`-ing a stock Slax ISO to a USB stick produces a stick that boots nothing**, on BIOS or on
UEFI. The `isohybrid` recipe adds `isohdpfx.bin` as MBR boot code plus a GPT; combined with
`uefi-bootable` it produces an image that boots from optical media and from a `dd`'d stick, on both
firmware types.

## Identical across all four images

Every field above is byte-identical on 32- and 64-bit, Debian and Slackware:

```
El Torito catalog @ LBA 45
  validation      platform=x86-BIOS
  initial         platform=x86-BIOS bootable=True media=no-emulation sectors=4 lba=46
UEFI bootable False
boot-info-table pvd_lba=16 file_lba=46 file_len=40960 checksum=0xe5d3e1ef consistent=True
```

The checksum is the same on all four because `isolinux.bin` is the same file and lands at the same
LBA every time. One `uefi-bootable` implementation therefore covers all four targets with no
per-target branch.
