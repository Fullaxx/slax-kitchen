# `tools/` — three helpers nobody mentions

`vendor/linux-live/tools/`. Small, undocumented outside a comment in `config`, and one of them is
load-bearing.

## `isolinux.bin.update`

**The one that matters.** `isolinux.bin` has the boot directory name compiled into it, so renaming
`LIVEKITNAME` from `slax` to anything else produces an ISO that boots from USB but **not from CD**
until this is run. Upstream's warning lives in `config`:

> If you change it, you must run `./tools/isolinux.bin.update` script in order to update
> `isolinux.bin` for CD booting.

It rebuilds the binary from the syslinux source with the new path patched in. Any `rename-livekit`
work has to invoke it or accept a CD-unbootable image.

`build` separately rewrites the name through `sed` in `syslinux.cfg`, `bootinst.bat` and the EFI
config — so a rename touches four distinct mechanisms, only one of which is a text substitution.

## `bootlogo.update`

Regenerates `bootlogo.png`, the vesamenu background. Useful for branding; nothing subtle about it.

## `mkfs.xfs.custom.c`

147 KB of C — the source for one of the nine static binaries in `initramfs/static/`, and the **only
one whose source is in the repository at all**. `DOC/sources.txt` points elsewhere, and only for
busybox and ntfs-3g:

> Source code for precompiled binaries (initramfs/static/*) can be found at
> http://ftp.slax.org/Slax-7.x-development/sources/Slax-7.0-sources/busybox-and-ntfs3g/

So seven of the nine binaries ship with no in-tree source and no stated provenance. That is worth
knowing if you redistribute an image: see [NOTICE.md](../../NOTICE.md).

Why a custom `mkfs.xfs` at all: `persistent_changes` formats the DynFileFS container with it, and it
needs to work inside a 45 MB initramfs with no libraries — hence a purpose-built static binary
rather than the real `xfsprogs`.
