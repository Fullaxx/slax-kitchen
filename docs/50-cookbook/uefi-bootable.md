# `uefi-bootable` — make the ISO boot on UEFI firmware

**Status: boot-verified** — booted under OVMF, all the way to livekit mounting bundles.

```sh
kitchen apply memtest86plus serial-console uefi-bootable   # LAST in the list
kitchen pack
```

## The problem

**A stock Slax ISO cannot boot on UEFI at all.** Parsed from the official image:

```
El Torito catalog @ LBA 45
  validation      platform=x86-BIOS
  initial         platform=x86-BIOS bootable=True media=no-emulation sectors=4 lba=46
UEFI bootable False
```

One entry, for BIOS. No `0xEF` section header, no EFI System Partition.

`/slax/boot/EFI/Boot/bootx64.efi` **does** exist on the ISO, which misleads people — but it is a
copy of `syslinux.efi`, and syslinux's EFI build can only read FAT. On an ISO9660 filesystem it is
unreachable. `bootinst.sh` relocates that directory to the root of a FAT USB stick, which is why
UEFI works from a stick but never from the disc.

## What the recipe does

Adds a second El Torito entry pointing at a real FAT EFI System Partition, and puts **GRUB** in it
rather than syslinux — because GRUB can read iso9660. That means a ~6 MiB ESP holding only
`BOOTX64.EFI`, while the kernel, initramfs and bundles stay on the ISO filesystem where they already
are.

```
boot/grub/grub.cfg: mirrored 4 menu entries from isolinux.cfg
boot/efi.img: 6336 KiB FAT12 ESP containing EFI/BOOT/BOOTX64.EFI (6048 KiB GRUB)
pack hint: uefi=True
```

Afterwards:

```
  initial         platform=x86-BIOS bootable=True media=no-emulation sectors=4
  section-header  platform=EFI entries=1
  section         platform=EFI bootable=True media=no-emulation sectors=12672
UEFI bootable True
```

BIOS booting is untouched — the same ISO boots both ways.

**It needs no privileges.** The ESP is built with `mkfs.vfat -C` plus `mtools`, which write into an
image file without ever mounting it. That is what makes this work in an unprivileged container.

## `menu_timeout` — how long GRUB waits

```yaml
recipes:
  - name: uefi-bootable
    vars:
      menu_timeout: "30"        # default is "5"
```

Five seconds is fine for a person sitting in front of the machine. It is a poor fit for
anything driving the menu automatically, because the window only opens once GRUB is
running — and getting there is a property of the machine, not of the image.

Measured on 2026-09-16, the same ISO under TCG with no `/dev/kvm`, screendumping at
intervals:

| at | what is on screen |
|---|---|
| 2 s | nothing — OVMF is still in its DXE phase |
| 9 s | still blank |
| 14 s | `EFI stub: Loaded initrd…` — GRUB's menu has already come and gone |

So OVMF spends about nine seconds in firmware, and the five-second window had opened and
shut before 14. A keystroke lead tuned on a KVM host — where the same menu appears in
about one second — lands in dead air on a slower one. That is the worst shape a test can
have: green on the fast machine, red on the slow one, for reasons having nothing to do
with the thing under test.

[`profiles/boot-matrix.yaml`](../60-testing/tier-c.md) sets 30 for exactly this reason,
and `kitchen test --uefi` waits 10 seconds before touching the menu. An unattended boot
sits through the extra seconds it was going to sit through anyway.

## ⚠️ Apply it last

`boot.uefi` generates the GRUB menu by **parsing `isolinux.cfg`**, so it mirrors whatever entries
exist when it runs. Put it after anything that adds a menu entry:

```yaml
recipes:
  - memtest86plus
  - serial-console
  - isohybrid
  - uefi-bootable     # last
```

Order it earlier and the BIOS menu gets your entries while the UEFI menu does not. It is idempotent,
so re-running it after adding entries refreshes the menu.

## Verified

Booting the result under OVMF:

```
BdsDxe: starting Boot0001 "UEFI QEMU DVD-ROM" ...
GNU GRUB  version 2.12
...
efifb: mode is 1280x800x32
Live Kit init <http://www.linux-live.org/>
* Looking for slax data in /slax ... * Found on /dev/sr0
* Mounting bundles
```

### The GRUB in your ESP is your build host's GRUB

`grub-mkstandalone` builds `BOOTX64.EFI` from whatever GRUB is installed where you are building, and
that binary is the first code the firmware executes. So the [reference
container](../../containers/README.md)'s base changes what ships:

| built on | GRUB | `BOOTX64.EFI` | ESP |
|---|---|---|---|
| `ubuntu:24.04` (default) | 2.12 | 6,193,152 B | ~6.3 MiB |
| `debian:12` | **2.06** | 10,334,208 B | 10,368 KiB |

Both boot. The 2.12 log above is the default build; the Debian 12 build was checked the same way on
2026-09-15 and reached a login prompt:

```
GNU GRUB  version 2.06
...
[  OK  ] Started getty@tty1.service - Getty on tty1.
[  OK  ] Reached target getty.target - Login Prompts.
slax login:
```

The 4 MB difference is GRUB's, not ours — the module list
(`lib/apply.py` `GRUB_MODULES`) is identical in both. Structure assertions pass either way:
16 passed, 0 failed with `--expect-uefi`.

## Limitations

- **x86_64 only.** There is no `bootia32.efi`, matching upstream — a 32-bit UEFI machine is not
  covered, even on the 32-bit ISO.
- **No Secure Boot.** Slax's kernel is unsigned and there is no shim. With Secure Boot enabled the
  firmware will refuse GRUB. Disable it, or enroll your own keys.
- Combine with [`isohybrid`](isohybrid.md) to also get a GPT, which is what makes a `dd`'d stick
  bootable on UEFI.
