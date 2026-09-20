# Generated manifests

Machine-readable inventories extracted from the four reference Slax ISOs. These are **generated
artifacts kept under version control on purpose** — they are what `kitchen probe` and the structure
tests compare against, and regenerating them requires the ISOs, which are deliberately *not* in the
repo. `kitchen sources` reads the three `.sha256` kinds to recognise every stock file in a built
image — and when a recipe has repacked `initrfs.img`, it reads that image's initramfs members and
says how many of them are still Slax's, rather than assuming.

| File | What it is | Rows |
|---|---|---|
| `<flavour>-<arch>-<ver>.packages.tsv` | Debian: `dpkg` inventory from `01-core.sb` — `name⇥version⇥arch⇥status` | 295 |
| | Slackware: package inventory across all 7 bundles — `bundle⇥package` | 423 |
| `bootfiles-<target>.sha256` | sha256 of every file under `/slax/boot/` | 31 |
| `initramfs-<target>.sha256` | sha256 of every regular file inside the unpacked `initrfs.img` | 329–331 |
| `isofiles-<target>.sha256` | sha256 of every file outside `/slax/boot/` (the bundles, `readme.txt`), plus `isolinux.bin@64` | 8 (Debian), 9 (Slackware) |

**`./slax/boot/isolinux.bin@64` is not a file.** It is the sha256 of `isolinux.bin` from byte 64 to
the end. Bytes 8–63 are the boot-info table, which every mastering tool rewrites with the file's new
position, so a repacked image never has the stock file's whole-file hash. Past byte 64 it is
unchanged, and that is how `kitchen sources` still recognises it. The value is the same on all four
targets.

All four targets are present: `{debian,slackware}` × `{32bit,64bit}`.

## Regenerating

```sh
ci/gen-manifests.sh isos/slax-*.iso
```

About sixteen seconds for all four images — measured 2026-09-17 at `9c5a45a`, the commit that added
the `isofiles` hashing which is most of it. It cost three seconds before that. **This paragraph is
the only copy of that number**: [`../README.md`](../README.md) restated the older figure and nobody
updated it when this got slower. Re-measure with `time ci/gen-manifests.sh isos/slax-*.iso`.
Read-only
and unprivileged throughout: `xorriso -osirrox` for the
ISO9660 tree, `unsquashfs -o <offset>` to read bundles **in place** without extracting them, and
`xz | cpio` for the initramfs. No loop devices and nothing mounted — the same constraints the dev
container imposes (see [`../../40-workflow/container-vs-host.md`](../../40-workflow/container-vs-host.md)).

**Run it as root.** The initramfs holds seven device nodes; a non-root `cpio` turns them into empty
regular files. They are excluded from the hash lists either way (`find -type f`), but a tree
extracted without them is not a tree you can repack.

Ordering is `LC_ALL=C sort` everywhere, so a regeneration produces a reviewable diff rather than a
reshuffle.

## Two results worth reading off these files directly

**The boot chain is shared across flavours.** Diffing the two `bootfiles-*.sha256` tables yields
exactly one differing line — `initrfs.img`. All 30 other files, `vmlinuz` included, are
byte-identical between the Debian and Slackware builds:

```sh
diff <(sort bootfiles-debian-64bit-12.2.0.sha256) \
     <(sort bootfiles-slackware-64bit-15.0.4.sha256)
```

**The initramfs userspace is arch-neutral.** Across all four ISOs, `bin/busybox`
(`9eb1c731…64de`, BusyBox v1.26.2, i386 static), the seven static helper binaries, all 245 applet
symlinks, `/init`, `/lib/livekitlib`, `/lib/config` and `/shutdown` are byte-identical. The only
thing that differs between the four `initrfs.img` files is `lib/modules/<version>/` — and note the
32-bit kernel is `6.1.38-smp`, not `6.1.38`.
