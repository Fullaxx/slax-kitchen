# Editing the bootloader

The cheapest customization in the whole toolkit. The two menu files are **byte-identical across all
four targets**, so one edit applies everywhere, and nothing needs recompiling.

```
work/iso/slax/boot/isolinux.cfg    the CD menu       1,202 B
work/iso/slax/boot/syslinux.cfg    the USB/HDD menu  1,537 B
```

UEFI reads the second one too — `EFI/Boot/syslinux.cfg` is one line, `INCLUDE
/slax/boot/syslinux.cfg` — so there is no third file to keep in step.

## Two files, one real difference

Both carry identical chrome (vesamenu, `TIMEOUT 40` = 4 seconds, `bootlogo.png`, `F1 help.txt`). The
difference is persistence:

| | entries |
|---|---|
| `isolinux.cfg` | `default`, `toram`, and two `MENU DISABLED` placeholders |
| `syslinux.cfg` | `default` (`perchdir=resume`), `perch` (`=new`), `asksession` (`=ask`), `toram` |

**The CD menu sets no `perchdir=`, which is why booting from CD has no persistence.** The disabled
entries are there to keep both menus the same height. Enabling them does not work — optical media
cannot be written to.

## Adding an entry

```yaml
- verb: boot.menu
  targets: [isolinux.cfg, syslinux.cfg]
  add:
    label: mytools
    menu_label: "Slax with my tools"
    kernel: /slax/boot/vmlinuz
    append: "vga=normal initrd=/slax/boot/initrfs.img rw printk.time=0 automount noload=05-chromium"
```

**Prefer adding an entry over changing the default.** It leaves a way back if the change turns out to
be wrong, which matters when the only feedback channel is a boot that either works or does not.

To change an existing one instead:

```yaml
- verb: boot.cmdline
  targets: [isolinux.cfg, syslinux.cfg]
  append: ["noload=05-chromium"]
```

`boot.cmdline` edits the `APPEND` lines of entries that already exist; `boot.menu` creates new ones.

## ⚠ `LINUX`, not `KERNEL`

The single most expensive mistake available here.

```
LABEL memtest
  MENU LABEL Memory test
  LINUX /slax/boot/memtest.bin      ← correct
  KERNEL /slax/boot/memtest.bin     ← silent black screen, forever
```

`KERNEL` makes SYSLINUX **guess the payload type from the file extension**. For a raw `.bin` it
guesses wrong, and the failure mode is a black screen with no error, no message and no timeout — the
menu is gone and nothing replaces it. `LINUX` forces the bzImage path, which is what Memtest86+
actually is.

This cost real debugging time. The `memtest86plus` recipe uses `LINUX`; see
[the cookbook page](../50-cookbook/memtest86plus.md).

## Branding

| file | |
|---|---|
| `bootlogo.png` | menu background. 640×480 is what the shipped one is |
| `zblack.png` | background behind the `F1` help screen |
| `help.txt` | the `F1` text. Worth rewriting — it documents 6 parameters and one that does not exist |

`help.txt` lists `nosound`, which has **no consumer anywhere** — not in the initramfs, not in
`livekitlib`, not in any bundle on either flavour. Meanwhile `load=`, `cache=`, `ip=` and `text` are
all implemented and undocumented. If you are shipping to other people, fixing that file is a kindness.
The full authoritative list is [boot-parameters](../20-boot-sequence/boot-parameters.md).

## Adding a boot payload

```yaml
- verb: boot.payload
  src: "https://.../mt86plus_7.20_64.bin"
  sha256: "…"
  dest: slax/boot/memtest.bin
```

`boot.payload` downloads, verifies the hash, and handles archives — it detects the archive type from
**content magic**, not the filename, because a temp file named `memtest.bin.part` once sent a zip to
the tar reader.

## What you cannot edit in place

### `isolinux.bin` — never patch it after mastering

Two independent reasons:

1. **`-boot-info-table` patches 56 bytes into the file during mastering** — the PVD LBA, the file's
   own LBA, its length, and a checksum over everything from offset 64 onward. Editing any byte past
   64 invalidates that checksum and ISOLINUX refuses to boot.
2. The directory name `slax` is compiled in at offset `0x746e`, inside a **packed string table**, not
   as a plain null-terminated string:

   ```
   ot/iso \x88\x0c\xdb\x01 sys \xd8\x01\x01 slax \x8c\x02\x06\x00 / \x00 %02x \x00
   ```

   A search-and-replace will not survive a length change.

So renaming `LIVEKITNAME` means regenerating `isolinux.bin` **before** the ISO is built —
upstream ships `tools/isolinux.bin.update` for exactly this, and `lib/config` says so outright.

USB and PXE are unaffected: `extlinux` and `pxelinux.0` take their paths from the config file rather
than a compiled-in constant. **A rename that only ever boots from USB needs no loader patch at all.**

### Do not mix SYSLINUX generations

Every loader and `.c32` on the ISO is **6.03**. A module from 4.x will not load under a 6.03 core;
the symptom is `Failed to load COM32 file` or a bare prompt.

This is a live hazard on Slackware, whose `01-core.sb` contains `syslinux-4.07-x86_64-4` while the
boot payload is 6.03. Running the in-image `/usr/bin/syslinux` or `/sbin/extlinux` against a Slax
stick installs a mismatched generation. See
[known upstream issues](../30-inventory/known-upstream-bugs.md).

If you add a COM32 module — `hdt.c32`, `chain.c32`, `memdisk` — take it from a 6.03 tree, and take
`ldlinux.c32` with it if you are replacing the set.

## Verify

```sh
kitchen pack
kitchen test out/*.iso --structure          # asserts the entries parse
kitchen test out/*.iso --bios --uefi        # actually boots them
```

`tests/boot/qemu_boot.py --keys` can drive the menu, which is how a new entry gets verified rather
than just inspected:

```sh
--keys '2s,esc,1s,down,down,0.5s,ret'       # BIOS: Esc reveals the menu, then pick the 3rd entry
--keys '7s,down,2s,down,1s,ret'             # UEFI: GRUB needs longer before it accepts input
```

A spec is **comma-separated QEMU qcodes** — `down`, `ret`, `esc`, `home`, `a`–`z`, `0`–`9` — with
`Ns` to wait before the next key. There is **no repetition syntax**: write the key out as many times
as you mean it. Note `ret`, not `enter`; `enter` is not a qcode.

Both halves of that are checked. A spec this harness cannot read is refused before anything boots —
and before the tree is sent, if you have a [boot host](../60-testing/boot-host.md) — and a name QEMU
does not know is refused by QEMU, whose answer is reported rather than discarded. It used to be
discarded, so a mistyped key was simply never pressed: the run spent its whole `--seconds` ceiling
and then reported that the *sequence* had not selected an entry, which points at timing rather than
at the typo.

A screenshot comes back via QMP `screendump` — pass `format=png` or you silently get a PPM.
