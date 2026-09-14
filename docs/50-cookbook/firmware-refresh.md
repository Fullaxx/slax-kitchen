# `firmware-refresh` — the GPU firmware Slax ships none of

**Status: verified** — applied on Debian 64-bit; all six packages confirmed installed, and the
resulting bundle inspected for the firmware directories that were missing. **Not boot-tested** on
hardware that needs the firmware.

```sh
kitchen apply firmware-refresh
```

**Debian only.** The Slackware route is at the bottom of this page.

## The gap is total, not partial

Every bundle of both flavours was searched. **There is no GPU firmware anywhere in a stock Slax** —
no `amdgpu`, no `i915`, no `radeon`, no `nvidia`, no `nouveau`. The 108 firmware subdirectories on
Debian (129 on Slackware) are all networking: `intel/` is iwlwifi, alongside `rtl_nic` and `ath10k`.

That is not cosmetic. A modern AMD card **does not initialise at all** without `amdgpu` firmware,
and recent Intel graphics lose GuC/HuC and display power management without the `i915` blobs. On an
affected machine the symptom is a black screen or a software framebuffer — which looks like a Slax
bug and is not one.

## What it adds, measured

| | stock | after |
|---|---|---|
| firmware subdirectories | 108 | **158** |
| `amdgpu` | 0 | **531** files |
| `nvidia` | 0 | **482** |
| `radeon` | 0 | **248** |
| `i915` | 0 | **120** |
| `ath11k` (Wi-Fi 6) | 0 | **71** |
| `mediatek` | 0 | **35** |

**Cost: the bundle is 89 MiB and the ISO goes 415 → 505 MiB, so +90 MiB.** Less than it sounds,
because firmware compresses well under xz.

If you know your target hardware, cut the list — `firmware-linux` alone fixes the GPU case and is
the bulk of the value.

## No apt source changes are needed

The stock `/etc/apt/sources.list` already carries `non-free non-free-firmware` on all three lines
(bookworm, `-security`, `-updates`), so everything here is reachable from the image as shipped. That
is unusual and worth knowing — it means `bundle.packages` can install any firmware package without
touching apt configuration.

`no_recommends: true` matters here: firmware packages Recommend other firmware packages, and
without it the closure is far larger than the list and much of it is for hardware that cannot
coexist in one machine.

## It supplements `01-firmware`, it does not replace it

Numbered `09`, so both bundles mount and for any path present in both, **the higher number wins**.
That is what you want: these packages are newer than the frozen 2023 base, so the refreshed copies
of `rtl_nic`, `ath10k` and the `intel/` wireless blobs are the ones that get used.

Removing `01-firmware` as well would save another ~91 MiB but drop the firmware this bundle does
*not* carry. See [`remove-bundle`](remove-bundle.md) if you want to try it — check your hardware
first.

## Slackware

Not supported here, for the reasons [`add-packages`](add-packages.md) documents: no
dependency-resolving package manager, and a stock mirror pointing years past this base.

The Slackware route is a pinned `kernel-firmware` package through
[`bundle-from-txz`](bundle-from-txz.md):

```yaml
PKGS="a/kernel-firmware-20211220_0c6a7b3-noarch-1.txz"
```

Note that Slackware ships firmware as **one large noarch package** rather than Debian's split
packages, so there is no way to take only the GPU blobs.
