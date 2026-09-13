# Generated manifests

Machine-readable inventories extracted from the four reference Slax ISOs. These are **generated
artifacts kept under version control on purpose** — they are what `kitchen probe` and the structure
tests compare against, and regenerating them requires the ISOs, which are deliberately *not* in the
repo.

| File | What it is | Rows |
|---|---|---|
| `debian-64bit-12.2.0.packages.tsv` | `dpkg` inventory from `01-core.sb` — `name⇥version⇥arch⇥status` | 295 |
| `slackware-64bit-15.0.4.packages.tsv` | Slackware package inventory across all 7 bundles — `bundle⇥package` | 423 |
| `bootfiles-<target>.sha256` | sha256 of every file under `/slax/boot/` | 31 |
| `initramfs-<target>.sha256` | sha256 of every file inside the unpacked `initrfs.img` | 337–338 |

## How they were produced

Read-only, without mounting anything: `7z` to read the ISO9660 and squashfs images, `xz` + `7z` to
unpack the newc cpio initramfs, then `sha256sum`. No loop devices, no `unsquashfs` privileges — the
same constraints the dev container imposes (see `docs/40-workflow/container-vs-host.md`).

## Two results worth reading off these files directly

**The boot chain is shared across flavours.** Diffing the two `bootfiles-*.sha256` tables yields
exactly one differing line — `initrfs.img`. All 30 other files, `vmlinuz` included, are
byte-identical between the Debian and Slackware builds:

```sh
diff <(sort bootfiles-debian-64bit-12.2.0.sha256) \
     <(sort bootfiles-slackware-64bit-15.0.4.sha256)
```

**The initramfs userspace is arch-neutral.** Across all four ISOs, `bin/busybox`
(`9eb1c731…64de`, BusyBox v1.26.2, i386 static), the seven static helper binaries, all 246 applet
symlinks, `/init`, `/lib/livekitlib`, `/lib/config` and `/shutdown` are byte-identical. The only
thing that differs between the four `initrfs.img` files is `lib/modules/<version>/` — and note the
32-bit kernel is `6.1.38-smp`, not `6.1.38`.

> Regeneration will be automated by `lib/fingerprint.py` (task T-014); until then these were
> produced by ad-hoc read-only inspection and the hashes above are the authority.
