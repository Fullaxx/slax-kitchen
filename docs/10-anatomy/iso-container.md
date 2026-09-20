# The ISO9660 container

All four images are the same shape: **ISO9660 Level 1 + Rock Ridge + Joliet**, no El Torito EFI
section, no MBR, no GPT. This page covers the filesystem; the boot catalog is
[`el-torito.md`](el-torito.md).

```sh
python3 lib/isoparse.py isos/slax-64bit-debian-12.2.0.iso
```

## Volume descriptors

Four descriptors at LBA 16–19, then the terminator. Read straight off the 64-bit Debian image:

| LBA | Descriptor | |
|---|---|---|
| 16 | Primary | system id `LINUX`, volume id `slax`, application id `slax` |
| 17 | Boot Record | boot system id `EL TORITO SPECIFICATION`, catalog at LBA 45 |
| 18 | Supplementary | escape sequence `%/E` → **Joliet**, UCS-2 level 3 |
| 19 | Terminator | |

The Primary Volume Descriptor in full:

```
system id        'LINUX'            volume id      'slax'
application id   'slax'             block size     2048
volume space     212,819 blocks     set size/seq   1 / 1
path table size  90 bytes           L path table   LBA 21
root directory   LBA 29, 2048 B     file struct    version 1
created / modified / effective      2023-10-09 20:48:43 +00:00
publisher · data preparer · volume set · copyright · abstract · biblio   all empty
```

Volume id, system id and application id come straight from `genisoimage -V slax -A slax`; `LINUX` is
genisoimage's own default. Everything else is left at its default, which is why the publisher and
preparer fields are blank — worth knowing if you intend to brand a build, because there is nothing
to overwrite, only fields to start filling in.

The Joliet SVD mirrors the PVD with its own path table (LBA 25) and root directory (LBA 37). Both
name trees describe the same extents; only the directory records differ.

## Level 1, and why nothing is truncated anyway

The PVD declares file structure version 1 and the names in the primary tree obey ISO9660 Level 1:
8.3, uppercase, with a `;1` version suffix. `ISOLINUX.BIN;1`, `INITRFS.IMG;1`, and the bundles —
which are 10 characters before the extension — become names like `01_CORE.SB;1`.

Nothing reads those names. Linux mounts the image with Rock Ridge, Windows with Joliet, and
`isolinux.bin` finds its own configuration through the boot-info-table rather than by path. The
Level 1 names exist only because `genisoimage` defaults to them and the build never passes
`-iso-level`.

## Rock Ridge

`-R` (not `-r`), so real ownership and real modes survive:

```
drwxr-xr-x  /slax/boot
-rwxr-xr-x  /slax/boot/bootinst.sh          0755 — must stay executable
-rwxr-xr-x  /slax/boot/extlinux.x64         0755 — must stay executable
-rw-r--r--  /slax/boot/vmlinuz              0644
er--r--r--  /slax/boot/isolinux.boot        0444 — the boot catalog (xorriso marks it 'e')
```

The `0755` bits on `bootinst.sh` and the two `extlinux` installers are the ones that matter. A user
who copies `/slax/` off the ISO onto a FAT stick loses them (FAT has no mode bits), which is why
`bootinst.sh` opens by explaining what to do if it was opened in a text editor instead of run, and
re-`chmod`s `extlinux.x$ARCH` itself before using it.

**Repacking with `-r` instead of `-R` silently resets every mode to 0555 and the owner to 0:0.** The
image still boots — nothing in the boot path checks modes — so the damage only shows up later, when
someone tries to run `bootinst.sh` from a mounted ISO. `kitchen pack` uses `-R`, and
`tests/structure/iso_assert.py` asserts the three executable bits.

## Extent layout

`-graft-points` emits the tree in the order the build script lists it, which puts the payload in an
unusual place:

| LBA range | blocks | |
|---|---|---|
| 0–15 | 16 | system area — **all zero**, see [`el-torito.md`](el-torito.md) |
| 16–19 | 4 | volume descriptors |
| 21–44 | 24 | path tables and directory records |
| 45 | 1 | El Torito boot catalog (`isolinux.boot`) |
| 46–65 | 20 | `isolinux.bin` |
| 66 | 1 | `readme.txt` |
| **67–201,446** | **201,380** | **the six bundles, back to back** |
| 201,447–212,818 | 11,372 | everything else in `/slax/boot/`, including `vmlinuz` and `initrfs.img` |

Two consequences:

- **The kernel and initramfs live at ~97% of the image.** On optical media the drive seeks to the
  outer edge to load them, then back to LBA 67 for `01-core.sb`. It costs a second or two on a real
  drive and nothing on anything else.
- **Bundles start at a fixed LBA 67 on every image**, i.e. byte offset `0x21800`. That is why
  `unsquashfs -o 137216 <iso>` reads `01-core.sb` in place without extracting anything, which is how
  `kitchen probe` inspects a bundle in about 20 ms — measured 2026-09-13, and the only copy of
  that number.

## Per-image differences

Paths are identical across all four images except that Slackware adds `06-devel.sb`:

```sh
diff <(xorriso -indev A.iso -find / -exec lsdl -- | awk '{print $NF}') \
     <(xorriso -indev B.iso -find / -exec lsdl -- | awk '{print $NF}')
```

| | 64-bit Debian | 64-bit Slackware | 32-bit Debian | 32-bit Slackware |
|---|---|---|---|---|
| size | 435,853,312 | 476,710,912 | 436,060,160 | 470,161,408 |
| blocks | 212,819 | 232,769 | 212,920 | 229,571 |
| bundles | 6 | 7 | 6 | 7 |
| `vmlinuz` | 12,017,856 | 12,017,856 | 10,841,216 | 10,841,216 |
| `initrfs.img` | 8,872,472 | 8,869,024 | 7,791,856 | 7,802,236 |

Inside `/slax/boot/`, only `vmlinuz` and `initrfs.img` ever differ — **and they differ by word size,
never by flavour**. The other 30 files are byte-identical on all four images, so a change to the
bootloader or the menus applies to every target at once.

The 32-bit Debian ISO is *larger* than the 64-bit one (by 206,848 bytes), because `02-xorg` and
`05-chromium` are both bigger there. Only the initramfs shrinks predictably with word size.
