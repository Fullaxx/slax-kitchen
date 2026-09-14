# `isohybrid` — make the ISO `dd`-able to a USB stick

**Status: matrix-verified** — MBR, GPT and an EFI System Partition entry confirmed in the built image.

```sh
kitchen apply isohybrid
kitchen pack
sudo dd if=out/slax-...-custom.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

## The problem

**A stock Slax ISO written to a stick boots nothing.** Its first 512 bytes are entirely zero:

```
isohybrid MBR : False
GPT           : False
partitions    : 0
```

No MBR signature, no partition table, no GPT. Firmware finds nothing to execute. This surprises
people because almost every other distro ships hybrid images.

Upstream's supported USB route is instead "copy `/slax/` onto FAT and run `bootinst`", which is
documented in [install-to-usb](../05-using-slax/install-to-usb.md) and remains a perfectly good
option — see the trade-off below.

## What the recipe does

Records the intent; the MBR is stamped at mastering time by `xorriso`, which is the only point at
which the layout is known. Afterwards:

```
isohybrid MBR : True
GPT           : True
MBR part      : type=0x00 bootable=True lba=64 sectors=864192
MBR part      : type=0xef bootable=False lba=340 sectors=12672
```

The second partition, type `0xEF`, is the EFI System Partition — it appears only when
[`uefi-bootable`](uefi-bootable.md) is also applied, and it is what makes a `dd`'d stick boot on UEFI
firmware rather than BIOS only.

`kitchen pack` switches to the xorriso backend automatically when this recipe has run, because
`genisoimage` cannot emit a hybrid image on its own.

## ⚠️ The trade-off: no persistence

A `dd`'d image is a verbatim ISO9660 filesystem, which is **read-only**. So:

- **no persistent changes** — there is nowhere to write them;
- you cannot edit `/slax/rootcopy/` on the stick afterwards;
- you cannot add a bundle to `/slax/modules/` without rebuilding.

If you want any of those, use `bootinst` onto a FAT32 or ext4 partition instead. `isohybrid` is for
the case where you want a single file you can write anywhere, quickly, and treat as disposable.

## Requirements

Needs `/usr/lib/ISOLINUX/isohdpfx.bin` — the SYSLINUX hybrid MBR template, from the `isolinux`
package. Preflight checks for it before doing any work, and names the package if it is missing.

## Combine with `uefi-bootable`

Together they give one ISO that boots four ways: BIOS from disc, UEFI from disc, BIOS from a `dd`'d
stick, and UEFI from a `dd`'d stick. That is the configuration `profiles/example.yaml` builds.
