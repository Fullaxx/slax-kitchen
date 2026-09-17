# `firmware-refresh` — firmware so the ISO works on more hardware

**Status: boot-verified** — applied to `debian-64bit-12.2.0` and `debian-32bit-12.2.0`, both bundles
inspected, and the 64-bit ISO booted under QEMU (TCG, direct kernel) to `slax login:` with
`09-firmware-debian.sb` and `09-firmware-linux.sb` mounted. **Not tested on hardware that needs the
firmware** — that is the test that matters, and nobody here has run it.

```sh
kitchen apply firmware-refresh
```

**Debian only.** The Slackware route is at the bottom of this page.

**In a profile, list it after any recipe that removes a bundle.** `check_plan_order` refuses a
`bundle.remove` that runs after a bundle has been built, before anything is modified.
[`profiles/browsers-firmware.yaml`](../../profiles/browsers-firmware.yaml) combines it with
[`all-browsers`](all-browsers.md) in that order: `all-browsers` drops `05-chromium` first.

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

## The stock firmware it builds on

Slax 12.2.0's firmware is one bundle, `01-firmware.sb`: 91 MiB, holding 713 firmware files (211 MiB
unpacked) and the package records for what it installed. Twelve packages:

- **Ten Debian firmware packages**, at 20230210-5: `firmware-atheros`, `-bnx2`, `-brcm80211`,
  `-cavium`, `-ipw2x00`, `-iwlwifi`, `-libertas`, `-realtek`, `-ti-connectivity` and `-zd1211`. Wi-Fi
  and Bluetooth, and a few wired network cards (`bnx2` and `cavium` are Ethernet).
- **`firmware-b43-installer` and `b43-fwcutter`.** The installer ships no firmware. During Slax's
  build it downloaded Broadcom's `wl` driver from `www.lwfinger.com`, cut 155 firmware files for the
  `b43` driver out of its `wl_apsta.o`, and listed them in `b43/firmware-b43-installer.catalog`.
  Those files are in no Debian package, and no license text came with them.

There is no GPU firmware and no audio DSP firmware. Of the 1,959 firmware files the kernel's modules
name, the stock image has 295.

Slax's build deletes `/usr/share/doc` from every bundle, so none of the twelve packages carries its
`copyright` file; `usr/lib/firmware/ipw2x00.LICENSE` is the one license text left. `01-core.sb`
holds a little firmware too: `firmware-linux-free`'s files, and a `regulatory.db` with its signature
that no package owns.

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

Two new bundles. `01-firmware.sb` is not modified: it stays byte-identical to the one Slax shipped.

| bundle | from | size |
|---|---|---|
| `09-firmware-debian.sb` | 16 bookworm packages, installed as Debian ships them, and 10 stock packages reinstalled | 48.8 MiB, 2,118 files |
| `09-firmware-linux.sb` | 53 linux-firmware files and 8 links, their 11 license files and `WHENCE` | 5.4 MiB, 73 entries |

**Cost: the ISO goes from 416 MiB to 469 MiB.** Identical on both word sizes, because firmware
packages are architecture-independent.

**`09-firmware-debian.sb` is built with apt.** The verb stacks every bundle below it into a chroot,
runs

```sh
apt-get update -qq
apt-get install -y -qq --no-install-recommends --reinstall <the 26 packages>
```

and keeps only what the install added or changed on disk. Sixteen of the packages are new to the
image. The other ten are Slax's own, already installed at the version bookworm still carries, so a
plain install would skip them. `--reinstall` unpacks them again: their firmware comes back identical
and stays out of the bundle, and what lands is what Slax's build had deleted — their `copyright`
files. Measured contents: 1,971 firmware entries (307 MiB unpacked), 57 files under
`/usr/share/doc`, 58 dpkg records, and 32 others — alternatives links, man pages, AppStream,
bug-report and lintian metadata, `atmel_fwl` and its config, and the package-status fragment
`kitchen pack` merges into `98-dpkg-db.sb`.

**`09-firmware-linux.sb` is not apt.** A script fetches only files no Debian package ships, each
pinned by sha256, and refuses any path that already exists below it — see
[the rules the lists follow](#the-rules-the-lists-follow).

What the Debian bundle brings that stock Slax lacks entirely: **GPU firmware** — `amdgpu` (530
files), `i915`, `radeon`, `nvidia` for nouveau — Intel and SOF **audio DSP** firmware, `mediatek`,
Bluetooth, and a current **`regulatory.db`**. That is the only thing it replaces, and it is two paths:
stock `01-core` carries `regulatory.db` and its signature `regulatory.db.p7s` as plain files that no
package owns, and bookworm's `wireless-regdb` 2026.05.30 installs its own newer copies and turns both
paths into links to them, through `/etc/alternatives`.

What the linux-firmware bundle adds, because no Debian package has it: newer `amdgpu` blobs for
Radeon RX 7000-series cards (`gc_11_0_*`, `psp_13_0_*`), MediaTek MT7916 Wi-Fi, Realtek Bluetooth
configs, Intel QAT, Keyspan and Emagic USB devices, Mellanox Spectrum and Microchip PHY firmware.

## How the three bundles stack

An image built with this recipe has three firmware bundles, and all three are mounted:
`01-firmware.sb`, `09-firmware-debian.sb` and `09-firmware-linux.sb`.

**The number decides the layer.** At boot, livekit sorts bundles by number (`sortmod`) and adds each
to the aufs union at branch 1, directly under the writable layer, so every bundle added later sits
above the ones before it. When two bundles hold the same path, the higher-numbered one is what the
running system sees; the overlayfs fallback builds the same order. `09` is the top of the platform
range — above Slax's own `01`–`05`, below `10`–`89`, where a project's own bundles go
([which numbers are whose](../10-anatomy/bundles-squashfs.md#which-numbers-are-whose)). A profile can
rename either bundle with the `bundle` and `linux_bundle` vars.

Within `09`, the names set the order: `09-firmware-debian` sorts before `09-firmware-linux`, so the
linux-firmware step runs with everything Debian installed already below it, which its refusal to
shadow a file depends on.

**What overlaps, measured.** Every path in each new bundle, compared with every bundle below it, on a
fresh build for `debian-64bit-12.2.0` on 2026-09-17:

| bundle | paths that also exist in a lower bundle |
|---|---|
| `09-firmware-debian.sb`, 2,118 entries | 13: eleven dpkg records, byte-identical to the copies below, and `regulatory.db` with `regulatory.db.p7s`, which replace `01-core`'s |
| `09-firmware-linux.sb`, 73 entries | none |

**No firmware file in either new bundle hides one in `01-firmware.sb`.** Both bundles also carry
entries for directories that exist below; aufs merges a directory across layers, so those hide
nothing.

## Why it adds bundles instead of replacing `01-firmware`

The alternative is `remove-bundle` on `01-firmware`, then one bundle built from all 26 Debian
packages. It was considered, and this recipe layers instead:

- **The b43 firmware cannot be rebuilt.** `firmware-b43-installer` downloads from `www.lwfinger.com`,
  which no longer resolves, so a bundle built with apt would carry no b43 firmware. The only copy is
  the one in `01-firmware.sb`, and b43 firmware stays in the image: Broadcom BCM43xx Wi-Fi cards on
  the `b43` driver need it.
- **Slax's bundle stays byte-identical.** `kitchen probe` still recognises it, and the recipe can be
  added to a profile or taken out without touching a file Slax shipped — override, do not edit
  ([CONTRIBUTING](../../CONTRIBUTING.md#6-writing-a-recipe-the-parts-that-surprise-people)).
- **Removal has conditions.** `bundle.remove` must run before every `bundle.packages` in the plan,
  and the removed bundle's package records have to be replaced.
- **A replacement would not be smaller.** It carries the 91 MiB of stock firmware again, where this
  carries only the difference. The download is the same either way, because a reinstall fetches the
  ten stock packages too.

One bundle would be simpler to reason about — no reinstall, no replaced `regulatory.db` — but it
would lose the b43 files, or have to copy them out of the stock bundle, which is what layering
already achieves without copying anything.

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
too, as Slax shipped it: 155 files and the installer's catalog, with no license text. This recipe
does not add one, and `kitchen sources` and the release notes say so. If this project ever produces
the b43 files itself instead of keeping Slax's, the rule for everything it adds applies to them: the
license text travels with the firmware.

## What it does not do

- **It does not replace `01-firmware`.** All three firmware bundles are mounted; this recipe adds
  two. [Why](#why-it-adds-bundles-instead-of-replacing-01-firmware).
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
