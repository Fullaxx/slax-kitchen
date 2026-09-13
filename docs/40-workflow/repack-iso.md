# Rebuilding the ISO

> Phase 2. Assumes you have a work tree from [`kitchen unpack`](unpack.md).

## TL;DR

```sh
kitchen unpack isos/slax-64bit-debian-12.2.0.iso -o work
# ... edit work/iso/ ...
kitchen pack -s work/iso -o out/slax-custom.iso
```

## What upstream actually runs

Slax is mastered by `genisoimage`, not `xorriso`. Both `vendor/linux-live/build` and the
`genslaxiso` script shipped inside `01-core.sb` use the same invocation:

```sh
genisoimage -o - -quiet -v -J -R -D -A slax -V slax \
  -no-emul-boot -boot-info-table -boot-load-size 4 -input-charset utf-8 \
  -b slax/boot/isolinux.bin -c slax/boot/isolinux.boot \
  -graft-points $GRAFT .
```

Note `-R` (Rock Ridge, preserving real permissions) rather than `-r` (which would force
uid/gid 0 and normalise modes), and `-D` (no deep-directory relocation). This matches what is
actually in the ISO: `bootinst.sh` and `extlinux.x*` carry mode `0755`, everything else `0644`.

## Two backends, and why

`kitchen pack` supports both. It defaults to `genisoimage`, and switches to `xorriso` automatically
when you ask for `--uefi` or `--hybrid`.

| | `genisoimage` | `xorriso` |
|---|---|---|
| Matches upstream byte-for-byte | **yes** (see below) | no — see application id |
| Application id case | preserves `slax` | **uppercases to `SLAX`** (enforces ECMA-119 d-characters) |
| Pin timestamps for reproducible builds | no — cdrkit dropped cdrtools' `-*-date` flags | **yes**, `--modification-date=YYYYMMDDhhmmsscc` |
| El Torito EFI alt-boot (`-eltorito-alt-boot`) | yes | yes |
| isohybrid MBR + GPT in one pass | no (needs a separate `isohybrid` run) | **yes**, `-isohybrid-mbr` / `-isohybrid-gpt-basdat` |

The application-id difference is cosmetic — nothing in `livekitlib` reads it — but it does mean an
xorriso-built ISO will not fingerprint-match a stock one on that single field. `kitchen probe`
reports it as a known-benign difference rather than a mismatch.

## Measured fidelity

Round-tripping the stock 64-bit Debian ISO through `kitchen unpack` and `kitchen pack` with the
`genisoimage` backend:

```
size  original=435,853,312  rebuilt=435,853,312  delta=+0
sectors differing: 19/212,819 (0.0089%)
```

All 19 differing sectors are in the ISO9660 metadata region. Diffing the Primary Volume Descriptor
byte-for-byte shows the difference is **entirely three timestamp fields** — volume creation (offset
813), modification (830) and effective (864):

```
[816:822]  orig=b'310092'   new=b'609131'      # 2023-10-09 20:48:43  vs  2026-09-13 11:33:32
[823:827]  orig=b'4843'     new=b'3332'
```

Everything else — all 415 MB of payload, every bundle, the initramfs, the kernel — is byte-identical.

**The boot-info-table checksum also survives unchanged** (`0xe5d3e1ef`), which confirms
`isolinux.bin` came through intact. That checksum covers the file body, not its location, which is
why it matches even though the file landed at a different LBA in an earlier experiment.

## Gotcha: never checksum the in-ISO `isolinux.bin` against the source file

`-boot-info-table` **rewrites 56 bytes inside `isolinux.bin` in the output image** — it patches in
the PVD LBA, the file's own LBA, its length and a checksum. So the copy inside a built ISO will
never match `vendor/linux-live/bootfiles/isolinux.bin` on disk. Any fidelity check must exclude it,
or compare the patched fields separately. `lib/isoparse.py` parses the table and reports
`self_consistent`, which is the check that actually means something.

## Reproducible builds

`genisoimage` has no way to pin the volume timestamps, so two runs minutes apart differ in those 19
sectors. If you need bit-identical rebuilds, use the xorriso backend with an explicit date:

```sh
kitchen pack -s work/iso -o out.iso --backend xorriso --date 2023100920484300
```

and accept the uppercased application id, or post-normalise the PVD timestamps.

## UEFI and hybrid builds

See the `uefi-bootable` and `isohybrid` recipes — the stock ISO is BIOS-only and cannot be `dd`'d to
a USB stick, and those two recipes are what fix it. (Cookbook pages land with the recipes; until
then the mechanism is documented in `INITIAL_PLAN.DNC.md`.)
