# `firmware-refresh` — firmware so the ISO works on more hardware

**Status: boot-verified** — applied to `debian-64bit-12.2.0` and `debian-32bit-12.2.0`, both bundles
inspected, and the 64-bit ISO booted under QEMU (TCG, direct kernel) to `slax login:` with
`09-firmware-debian.sb` and `09-firmware-linux.sb` mounted. **Not tested on hardware that needs the
firmware** — that is the test that matters, and nobody here has run it.

```sh
kitchen apply firmware-refresh
```

**Debian only.** The Slackware route is at the bottom of this page.

> **CI builds this weekly, not on every push.** 65 files come from linux-firmware's mirrors, pinned by
> sha256, so an outage or a moved tag fails the build *by design* — which on every push would turn
> someone else's server into a red master. See [`ci/slow-recipes.txt`](../../ci/slow-recipes.txt).

## What this is for

An ISO that works on more machines. A driver without its firmware is a device that does not work — a
black screen on a modern AMD card, silent speakers on a laptop that uses Sound Open Firmware, Wi-Fi
that never associates — and it looks like a Slax bug when it is not one. The firmware bundles are the
means, not the end; two other things bear on the same goal:

- **Booting at all** on a given machine: [`uefi-bootable`](uefi-bootable.md) and
  [`isohybrid`](isohybrid.md).
- **The kernel's own driver set.** Firmware only helps a device the kernel has a driver for, which is
  why the lists below are measured from the kernel's modules rather than guessed.

## What the kernel can ask for, and who ships it

Measured on stock `debian-64bit-12.2.0` on 2026-09-17, with
[`tools/firmware-coverage.py`](../../tools/firmware-coverage.py):

| | firmware names |
|---|---|
| declared by the 6.1.38 modules (`MODULE_FIRMWARE`) | **1,959** (+6 wildcard patterns, not expanded) |
| already in the stock image | 295 |
| shipped by a bookworm package | 1,534 — 1,258 of them not in the stock image |
| shipped by **no** Debian package | 425 |
| → in linux-firmware `20260916`, with a license file named in `WHENCE` | **59** — added |
| → in linux-firmware, with only "Allegedly GPL … but no source visible" | 12 — left out |
| → in neither | 354 — mostly versions never published, and firmware nobody may redistribute |

Declared names are a floor, not a ceiling: some drivers build firmware names at runtime. That is why
three packages are included as known runtime families rather than by name — SOF and Intel SST audio,
and `wireless-regdb`.

## What it adds

| bundle | from | size |
|---|---|---|
| `09-firmware-debian.sb` | 16 bookworm packages, installed as Debian ships them, and 10 stock packages reinstalled | 48.8 MiB, 2,118 files |
| `09-firmware-linux.sb` | 53 linux-firmware files and 8 links, their 11 license files and `WHENCE` | 5.4 MiB, 73 entries |

**Cost: the ISO goes from 416 MiB to 469 MiB.** Identical on both word sizes, because firmware
packages are architecture-independent.

What the Debian bundle brings that stock Slax lacks entirely: **GPU firmware** — `amdgpu` (530
files), `i915`, `radeon`, `nvidia` for nouveau — Intel and SOF **audio DSP** firmware, `mediatek`,
Bluetooth, and a current **`regulatory.db`**. The last is the only file it replaces: stock `01-core`
carries a `regulatory.db` that no package owns, and bookworm's `wireless-regdb` 2026.05.30 supersedes
it.

What the linux-firmware bundle adds, because no Debian package has it: newer `amdgpu` blobs for
Radeon RX 7000-series cards (`gc_11_0_*`, `psp_13_0_*`), MediaTek MT7916 Wi-Fi, Realtek Bluetooth
configs, Intel QAT, Keyspan and Emagic USB devices, Mellanox Spectrum and Microchip PHY firmware.

## The rules the lists follow

**Debian first, accepted as Debian ships it.** A package is installed when it ships at least one
requested file the image lacks, or belongs to a runtime family. Every other firmware package in
bookworm is left out for a stated reason:

| left out | why |
|---|---|
| `firmware-qcom-soc`, `firmware-qcom-media`, `raspi-firmware` | ARM and Raspberry Pi firmware; nothing an x86 kernel loads |
| `firmware-nvidia-gsp`, `-tesla-gsp`, `-tesla-535-gsp` | for NVIDIA's proprietary driver, which Slax does not ship |
| `amd64-microcode`, `intel-microcode` | CPU microcode must load early, from the initramfs; a bundle arrives too late |
| `firmware-ivtv` | prompts for license acceptance through debconf, which stops an unattended install |
| `firmware-ipw2x00` | the same prompt; stock already ships it, with `ipw2x00.LICENSE` beside it |
| `firmware-b43-installer` | a downloader whose host, `www.lwfinger.com`, no longer resolves; stock already carries the firmware it extracted |
| `dahdi-firmware-nonfree`, `firmware-microbit-*`, `firmware-tomu` | telephony drivers not in the kernel, and boards flashed from the host |

**Only what no Debian package ships comes from linux-firmware** — never a replacement for a Debian
file — and only when its `WHENCE` entry names a license file. The license file travels with the copy,
under `/usr/lib/firmware/LICENSES/` where the tag keeps it, with `WHENCE` beside it saying which entry
each license covers. The step refuses any path that already exists below its bundle, so Debian's copy,
when there is one, is the only copy. The bundle names are chosen for that: `09-firmware-debian` sorts
before `09-firmware-linux`, so the linux-firmware step builds on top of everything Debian installed.

**Fetched per file, pinned per file.** The release tarball is 662 MB for these 65 files. Mirrors are
tried in order — GitLab, linux-firmware's own home, then `git.kernel.org`, which answered 503 to a
burst of requests while the list was measured — and the sha256 in the recipe is the authority. A file
no mirror serves, or one that fails its hash, fails the build; both refusals were run once and read:

```
firmware-refresh: amdgpu/navi10_ta.bin already exists below this bundle; refusing to shadow it
firmware-refresh: LICENSES/LICENCE.Abilis has sha256 8116433f…, pinned 0000000000…
```

To regenerate both lists after the base or the tag moves, unpack a **stock** image and run:

```sh
tools/firmware-coverage.py work/iso --tag <linux-firmware tag> --recipe
```

## License texts

Using an image that contains firmware implies acceptance of each firmware's license terms — see
[NOTICE.md](../../NOTICE.md). This recipe makes sure those terms travel with the files:

- **Every Debian firmware package in the image ships its own `/usr/share/doc/<package>/copyright`** —
  the 16 it installs, and the 10 stock ones, whose copies Slax's build had deleted.
- **Every linux-firmware file ships beside the license file its `WHENCE` entry names.**

The stock packages come back through a **reinstall**, which is what `apt: {reinstall: true}` is for.
The stock image already has them at the version bookworm still carries (20230210-5; bookworm-updates
has nothing newer), so a plain install does nothing. That was measured on the previous version of
this recipe, which listed `firmware-realtek`, `-atheros`, `-iwlwifi` and `-brcm80211` and got none of
them: its bundle declared five packages and carried no `ath11k` at all, though its page said otherwise.
A reinstall ships only what differs, because unchanged files stay out of the bundle's delta. Measured
on its own: ten packages, **64 KiB, 32 files** — copyright files, changelogs and dpkg's file lists —
and each copyright byte-identical to the md5 dpkg had recorded for it in the stock image.

Stock `01-firmware.sb` is left in place, so the Broadcom b43 firmware Slax's build extracted stays
too. No license text was ever published with those blobs.

## What it does not do

- **It does not replace `01-firmware`.** Both are mounted; this recipe adds.
- **It does not cover everything the kernel names.** 354 declared names exist in neither Debian nor
  linux-firmware, and the 6 wildcard patterns (Broadcom `brcmfmac` board files) are not expanded.
- **It does not install `firmware-ivtv`**, or reinstall `firmware-ipw2x00`: both stop at a debconf
  license prompt.
- **It is not proof that any device works.** Boot-verified means the image with these bundles boots;
  whether an AMD RX 7800 initialises is a question only that card can answer.
- **The bundles are not reproducible across time.** `wireless-regdb` and any future bookworm firmware
  update land when the recipe runs; the linux-firmware files are pinned.

## Building without firmware

If you would rather not accept the firmware terms, see
[`remove-bundle`](remove-bundle.md#building-without-firmware).

## Slackware

Not supported here, for the reasons [`add-packages`](add-packages.md) documents: no
dependency-resolving package manager, and a stock mirror pointing years past this base.

The Slackware route is a pinned `kernel-firmware` package through
[`bundle-from-txz`](bundle-from-txz.md):

```sh
PKGS="a/kernel-firmware-20211220_0c6a7b3-noarch-1.txz"
```

Note that Slackware ships firmware as **one large noarch package** rather than Debian's split
packages, so there is no way to take only the GPU blobs.
