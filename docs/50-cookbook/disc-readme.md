# `disc-readme` — a note on the disc, for whoever mounts it

**Status: boot-verified** — applied, packed and structure-asserted on all four targets, and booted
on each with all three livekit markers.

```sh
kitchen apply disc-readme
kitchen pack
```

Puts `ABOUT.txt` at the root of the disc, the first thing someone sees when they open the image in a
file manager or mount it instead of booting it. It plays no part in the boot, which works from the
`slax` directory beside it. It is the shipped recipe that exercises
[`iso.files`](../90-reference/verbs.md#isofiles-)
([#85](https://github.com/Fullaxx/slax-kitchen/issues/85)).

## Beside Slax's own `readme.txt`

Every stock image already has `/readme.txt` at its root: Slax's 823-byte note on making a USB drive
boot, byte-identical on all four. This recipe leaves it alone and adds a second file, because the
obvious name, `README.txt`, collides with it. Measured 2026-10-21, packing a 64-bit Debian tree that
held both names:

| | `genisoimage`, the default | `xorriso`, with `uefi-bootable` or `isohybrid` |
|---|---|---|
| plain ISO 9660 | Slax's stays `README.TXT;1`, and the new one becomes `READM000.TXT;1`; nothing says so, since pack runs it `-quiet` | `README.txt;1` and `readme.txt;1` |
| Joliet and Rock Ridge | `README.txt` and `readme.txt` | the same |

Either way, anything that ignores case finds two files with one name.

To replace Slax's note instead, set `file: readme.txt`: `iso.files` writes over a file that is
already there, so the disc keeps one note, with this text.

## What lands on the disc

Measured 2026-10-21 on 64-bit Debian, with `isoinfo`:

| | `genisoimage` | `xorriso` |
|---|---|---|
| plain ISO 9660 | `ABOUT.TXT;1` | `ABOUT.txt;1` |
| Joliet | `ABOUT.txt` | `ABOUT.txt` |
| Rock Ridge | `ABOUT.txt`, `-rw-r--r--`, owned 0:0 | the same |

The default text is 513 bytes in 12 lines, with LF line endings; Slax's `readme.txt` has CRLF. It
costs the image one 2,048-byte sector: 435,853,312 → 435,855,360 bytes with `genisoimage`, and
435,845,120 → 435,847,168 with `xorriso`.

## The default text

```
This disc is a Slax live system, built with slax-kitchen.

To run it from a CD or DVD, start the machine from the disc in legacy (BIOS)
mode. It starts in UEFI mode only if it was built with UEFI support.

To run it from a USB drive:
  1) Copy the whole /slax/ directory to the drive.
  2) On the drive, open the /slax/boot/ directory.
  3) Run bootinst.bat there on Windows, or bootinst.sh as root on Linux.
     It sets the drive up to boot.

readme.txt, beside this file, is Slax's own note on the same steps.
```

The steps are Slax's own: both `bootinst` scripts are in `/slax/boot` on all four stock images, and
`bootinst.sh` refuses to run except as root. The text holds whatever else an image has: the
structure check asserts the BIOS El Torito entry on every recipe's image, and only
[`uefi-bootable`](uefi-bootable.md) adds the EFI entry UEFI needs, since the stock image's
catalogue has only the BIOS one. Writing the image to a drive with `dd` works only with
[`isohybrid`](isohybrid.md), so it is not mentioned.

## Variables

| var | default | |
|---|---|---|
| `file` | `ABOUT.txt` | the file's name at the root of the disc |
| `readme` | the text above | what it says |

A project says what its own image is in its profile:

```yaml
recipes:
  - name: disc-readme
    vars:
      file: MYPRODUCT.txt
      readme: |
        MyProduct 1.0, a Slax live system for ...
```

## Booting is unaffected

livekit finds its data by looking for the `slax` directory on each device (`find_data_try` in the
stock initramfs's `livekitlib`) and works from there, so a file beside it plays no part. On
2026-10-21, `kitchen test --kernel` on the boot host (KVM) booted the `disc-readme` image of each
target, and each reached all three livekit markers:

| target | first seen, since qemu started |
|---|---|
| `debian-64bit-12.2.0` | `Looking for slax data` 3.7 s, `Mounting bundles` 3.7 s, `Live Kit done, starting slax` 4.2 s |
| `debian-32bit-12.2.0` | 3.7 s, 3.7 s, 4.2 s |
| `slackware-64bit-15.0.4` | 3.7 s, 3.7 s, 3.7 s |
| `slackware-32bit-15.0.4` | 3.7 s, 3.7 s, 4.2 s |

Each image was 2,048 bytes larger than its stock ISO, one sector on every target.

## How the matrix checks it

The recipe matrix holds every recipe to what it journalled
([#85](https://github.com/Fullaxx/slax-kitchen/issues/85)). This one journals `/ABOUT.txt`, so the
packed image must hold `/ABOUT.txt`. Shown failing on 2026-10-21: with the file deleted between
apply and pack, the matrix reported `FAIL disc-readme  structure assertions failed` and
`FAIL /ABOUT.txt present`. See [CI](../60-testing/ci.md#the-recipe-matrix-is-the-important-one).
A profile that lists it is held to the same by `kitchen build`'s structure test
([#96](https://github.com/Fullaxx/slax-kitchen/issues/96)).
