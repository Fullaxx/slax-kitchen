# Installing to a USB stick

Three routes. **The first is upstream's supported one and the one you probably want.**

## 1. Copy and run `bootinst` — recommended

Gives you persistence, UEFI boot and the ability to edit files on the stick afterwards.

1. Format the stick **FAT32** (or leave an existing FAT32 partition alone).
2. Copy the whole `slax/` directory from the ISO to the root of the stick.
3. Run the installer **as root**, from the stick:

```sh
cd /media/YOURSTICK/slax/boot
sudo ./bootinst.sh
```

On Windows, double-click `bootinst.bat` instead — it asks for admin rights via UAC and refuses to
run if the target is on the same physical disk as Windows.

### What it actually does

Worth knowing, because it explains the quirks:

- runs `extlinux --install` on the partition;
- `dd`s `mbr.bin` over the first 440 bytes of the **disk**, and sets the partition bootable;
- **moves** `slax/boot/EFI/Boot/` up to `/EFI/Boot/` at the root of the stick.

That last step is why `/slax/boot/EFI/` looks empty afterwards, and why UEFI works from a stick but
not from the CD — the EFI loader is `syslinux.efi`, which can only read FAT.

If `bootinst.sh` complains it cannot execute `extlinux`, the stick is mounted `noexec` or
`showexec`; the script tries to remount and then falls back to copying `extlinux.exe`.

## 2. `dd` the ISO — only after `isohybrid`

A **stock Slax ISO cannot be `dd`'d**. Bytes 0–511 are all zero: no MBR, no partition table, no GPT.
Written to a stick it boots nothing, on any firmware.

Apply the [`isohybrid`](../50-cookbook/isohybrid.md) recipe and it works:

```sh
kitchen apply isohybrid uefi-bootable
kitchen pack
sudo dd if=out/slax-...-custom.iso of=/dev/sdX bs=4M status=progress oflag=sync
```

The trade-off: a `dd`'d image is a read-only ISO9660 filesystem, so **you get no persistence** and
cannot edit `/slax/rootcopy/` on the stick. Use route 1 if you want either.

## 3. A tool like Ventoy or Rufus

Works, with the same read-only caveat as `dd`. Nothing Slax-specific.

---

## Which partition does it boot from?

The initramfs does not care which device it started from — it **scans every block device** for a
`/slax/` directory containing `.sb` files, for up to 45 seconds. That is why a stick works after a
simple copy, and why you can keep Slax on a data partition.

Force it with `from=`:

```
from=/dev/sdb1/slax
```

## Persistence

From a stick, on by default. The stock USB boot menu offers *Resume previous session*, *Start a new
session* and *Choose session during startup*. See [persistence-perch](persistence-perch.md) — the
behaviour differs sharply between FAT32 and a Linux filesystem.
