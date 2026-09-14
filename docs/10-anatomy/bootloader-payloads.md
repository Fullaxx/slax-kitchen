# Bootloader payloads

Slax uses **SYSLINUX 6.03 and nothing else.** No GRUB, no shim, no Secure Boot. Nine of the files in
`/slax/boot/` are part of that family, and they split into three groups that are easy to confuse:
loaders that the firmware runs, COM32 modules that a loader then runs, and **installers that run on
your own machine.**

```sh
strings -a /slax/boot/isolinux.bin | grep -oE 'ISOLINUX [0-9.]+'   # → ISOLINUX 6.03
```

## Loaders

The firmware loads exactly one of these, depending on how the medium is booted.

| file | medium | firmware | |
|---|---|---|---|
| `isolinux.bin` | optical | BIOS | 40,960 B. Loaded via El Torito from LBA 46 |
| `ldlinux.sys` | FAT partition | BIOS | **not on the ISO** — `extlinux --install` writes it |
| `EFI/Boot/syslinux.efi` | FAT partition | UEFI | 200,992 B, PE32+ x86-64 |
| `pxelinux.0` | network | BIOS PXE | 46,909 B |

`bootx64.efi` is a **byte-identical copy** of `syslinux.efi` under the name firmware looks for:

```sh
cmp /slax/boot/EFI/Boot/bootx64.efi /slax/boot/EFI/Boot/syslinux.efi   # identical
```

There is no `bootia32.efi` on any of the four images, so 32-bit UEFI is unsupported everywhere.

`mbr.bin` belongs in this group conceptually but is not a loader — it is 440 bytes of MBR boot code
that `bootinst.sh` `dd`s onto LBA 0 of the disk, whose only job is to chain to the active partition's
`ldlinux.sys`. `file` identifies it as `SYSLINUX MBR (version 3.52 or newer)`.

## COM32 modules

Once a loader is running it loads these from the same directory. They are architecture- and
firmware-specific, which is why the EFI copies are separate files of different sizes.

| BIOS | UEFI | |
|---|---|---|
| `ldlinux.c32` (116,552) | `ldlinux.e64` (139,616) | the core; every other module depends on it |
| `libcom32.c32` (181,944) | `EFI/Boot/libcom32.c32` (201,336) | runtime |
| `libutil.c32` (23,628) | `EFI/Boot/libutil.c32` (24,464) | utilities |
| `vesamenu.c32` (26,684) | `EFI/Boot/vesamenu.c32` (32,776) | graphical menu — what `UI` names |
| — | `EFI/Boot/menu.c32` (32,064) | text menu; shipped only on the EFI side |

## Where these binaries actually came from

Not from upstream SYSLINUX. `ldlinux.c32` carries `GCC: (Debian 6.3.0-18) 6.3.0 20170516` and
`isolinux.bin` a `-6.03+dfsg/core/` build path: these are **Debian stretch** binaries, package
`syslinux-common_6.03+dfsg-14.1+deb9u1`, built in **2017** and carried forward unchanged ever since
— the same vintage as the 2017 busybox in
[issue 11](../30-inventory/known-upstream-bugs.md).

Fetching that package from `archive.debian.org` and comparing against the ISO:

| module | vs the ISO |
|---|---|
| `ldlinux.c32` | byte-identical |
| `libcom32.c32` | byte-identical |
| `libutil.c32` | byte-identical |
| `vesamenu.c32` | byte-identical |

**This matters because "6.03" is not enough to identify a compatible module.** Upstream's own
prebuilt 6.03 binaries, from `syslinux-6.03.tar.xz` on kernel.org, are a *different build* of the
same version — and they do not work here. Measured in QEMU, twice:

```
# upstream 6.03 hdt.c32 alongside the ISO's Debian-built libraries
Undef symbol FAIL: __syslinux_debug_enabled
Failed to load COM32 file /slax/boot/hdt.c32

# and replacing the whole c32 core with upstream's, keeping the ISO's isolinux.bin
Failed to load ldlinux.c32
Boot failed: press a key to retry...
```

The second failure is the tighter constraint: `isolinux.bin` will not load a `ldlinux.c32` it did
not ship with, so the core set cannot be swapped without also replacing `isolinux.bin` — which
embeds `slax` at offset `0x746e` and would need re-patching.

**So: if you add a COM32 module, take it from
`syslinux-common_6.03+dfsg-14.1+deb9u1_all.deb`**, not from upstream, and not from your host's
`syslinux-common` (this container's is 6.04-git, and every shared module differs).

**Mixing generations breaks the boot silently.** A `.c32` from SYSLINUX 4.x will not load under a
6.03 core; the symptom is `Failed to load COM32 file` or a bare boot prompt. This is a live hazard on
Slackware, whose package database records `syslinux-4.07-x86_64-4` in `01-core.sb` while the boot
payload is 6.03 — running the in-image `/usr/bin/syslinux` or `/sbin/extlinux` against a Slax stick
installs a mismatched loader generation. See
[`known-upstream-bugs.md`](../30-inventory/known-upstream-bugs.md).

## Installers

These never run at boot. They run on a booted OS and write a bootloader onto a target filesystem.

| file | runs on | |
|---|---|---|
| `extlinux.x32` / `extlinux.x64` | Linux | ELF binaries, mode `0755`. `--install <dir>` writes `ldlinux.sys` into the FAT filesystem containing `<dir>` |
| `syslinux.com` | DOS | |
| `syslinux.exe` | Windows | PE32 i386 |
| `bootinst.sh` | Linux | the wrapper users are told to run |
| `bootinst.bat` | Windows | an sh/batch polyglot |

`bootinst.sh` is worth reading once, because three of its behaviours surprise people:

```sh
ARCH=$(uname -m); if [ "$ARCH" = "x86_64" ]; then ARCH=64; else ARCH=32; fi
EXTLINUX=extlinux.x$ARCH
```

1. **It picks the installer by the architecture of the machine you are running it on**, not by the
   architecture of the Slax you are installing. Installing 32-bit Slax from a 64-bit desktop uses
   `extlinux.x64`, which is correct — the installer only has to write to a FAT filesystem.
2. **It works around `noexec` and `showexec`** by remounting, then `chmod`, then finally copying to
   `extlinux.exe`. Those fallbacks exist because the usual case is a FAT stick mounted by a desktop.
3. **It moves `EFI/Boot` rather than copying it.** Running it twice from the same tree leaves the
   second run with nothing to move.

## Where the configuration comes from

`isolinux.bin` locates itself through the boot-info-table (see [`el-torito.md`](el-torito.md)), then
reads `isolinux.cfg` from its own directory. `syslinux.efi` reads `EFI/Boot/syslinux.cfg`, whose
entire content is one line:

```
INCLUDE /slax/boot/syslinux.cfg
```

So the USB and UEFI paths share one menu file, and the CD path has its own. That is why the CD menu
lacks persistence entries: it is a different file, and it never sets `perchdir=`.

## The embedded directory name

`isolinux.bin` contains the string `slax` at offset `0x746e`, in a table alongside `/boot/iso` and
`sys`:

```sh
python3 -c "d=open('isolinux.bin','rb').read(); print(hex(d.find(b'slax')))"   # → 0x746e
```

The surrounding bytes are a packed string table, not a plain null-terminated string:

```
ot/iso \x88\x0c\xdb\x01 sys \xd8\x01\x01 slax \x8c\x02\x06\x00 / \x00 %02x \x00
```

so it is not safely patchable by a naive search-and-replace. That is why upstream ships
`tools/isolinux.bin.update`, and why `lib/config` states outright that changing `LIVEKITNAME`
requires re-running it. Whatever produces the new binary, the patch must happen **before** mastering:
any edit past offset 64 invalidates the boot-info-table checksum.

USB and PXE booting are unaffected: `extlinux` and `pxelinux.0` take their paths from the
configuration file rather than from a compiled-in constant. A rename that only ever boots from USB
needs no loader patch at all, which is exactly what the comment in `lib/config` says.

## What this means for customization

- Anything that only edits `isolinux.cfg` / `syslinux.cfg` — extra menu entries, changed defaults,
  a serial console — is safe, reversible, and applies to all four images at once, because those two
  files are byte-identical across every target.
- Anything that adds a **loader** (GRUB for UEFI, `memtest.bin` as a boot payload) has to respect the
  generation rule above: bring your own complete set, do not mix with the shipped 6.03 modules.
- The `uefi-bootable` recipe deliberately adds **GRUB 2** rather than reusing `syslinux.efi`, because
  `syslinux.efi` cannot read ISO9660 — only FAT. GRUB with the `iso9660` module can, which is what
  makes a UEFI-bootable ISO possible at all.
