# 10 · Anatomy of a Slax ISO

What is actually inside the four shipping images, measured from the bytes rather than inferred from
the build scripts. Everything here was produced by re-reading `isos/slax-*.iso` with `lib/isoparse.py`,
`xorriso`, `unsquashfs -o <offset>` and `cpio -it`; the commands are shown so any claim can be
re-derived.

## How this differs from `15-upstream/`

They describe the same system from opposite ends and are deliberately not interchangeable:

| | asks | breaks when |
|---|---|---|
| [`15-upstream/`](../15-upstream/) | *what does the build system say?* | upstream commits something new |
| **`10-anatomy/`** | *what is in the image on disk?* | a new ISO is released |

Where the two would overlap, this section links instead of repeating. The largest case is
`livekitlib`: [`livekit-init.md`](livekit-init.md) walks the boot path and what it produces, while
the function-by-function reference with line numbers lives at
[`15-upstream/livekitlib-reference.md`](../15-upstream/livekitlib-reference.md). There is exactly one
copy of each fact.

## Pages

| | |
|---|---|
| [`iso-container.md`](iso-container.md) | ISO9660 descriptors, Rock Ridge, Joliet, and where every extent lands |
| [`el-torito.md`](el-torito.md) | the 64-byte boot catalog, the boot-info-table, and the proof that the ISO is BIOS-only |
| [`directory-map.md`](directory-map.md) | every one of the 37 files under `/slax/`, and which ones are never used |
| [`bootloader-payloads.md`](bootloader-payloads.md) | the SYSLINUX 6.03 family — which files are loaders and which are installers |
| [`initramfs.md`](initramfs.md) | `initrfs.img`: cpio layout, 8 static binaries, 301 modules, exact xz parameters |
| [`livekit-init.md`](livekit-init.md) | `/init` start to finish, with the state each stage leaves behind |
| [`bundles-squashfs.md`](bundles-squashfs.md) | the `.sb` format, numeric precedence, and why the two flavours' bundles are shaped differently |
| [`union-and-persistence.md`](union-and-persistence.md) | aufs vs overlayfs, perch sessions, DynFileFS |
| [`runtime-layout.md`](runtime-layout.md) | `/run/initramfs/memory/*` — the map everything else hangs off |
| [`shutdown.md`](shutdown.md) | the pivot back into the initramfs, loop teardown, CD eject |

## Reading order

Start with [`runtime-layout.md`](runtime-layout.md) if you only read one page. Almost every
"where do I put my file?" question resolves to a path under `/run/initramfs/memory/`, and the other
pages make more sense once that map is in your head.

For customization, the ordering that matches how the ISO is assembled is
[`bundles-squashfs.md`](bundles-squashfs.md) → [`initramfs.md`](initramfs.md) →
[`bootloader-payloads.md`](bootloader-payloads.md) → [`iso-container.md`](iso-container.md).

## Scope

Four images, all four measured for every claim here:

```
slax-64bit-debian-12.2.0.iso      435,853,312 B   sha256 61d9fdcc…
slax-64bit-slackware-15.0.4.iso   476,710,912 B   sha256 f5c26dfc…
slax-32bit-debian-12.2.0.iso      436,060,160 B   sha256 03b85cd2…
slax-32bit-slackware-15.0.4.iso   470,161,408 B   sha256 c6891370…
```

Where a claim holds for only one flavour or one word size it is marked as such. That distinction is
load-bearing more often than it looks — see the flavour asymmetry in
[`bundles-squashfs.md`](bundles-squashfs.md), which is the single biggest difference between the two
builds.
