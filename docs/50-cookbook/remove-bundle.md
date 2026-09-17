# `remove-bundle` — slim the ISO by dropping bundles

**Status: boot-verified** — built, probed and booted.

```sh
kitchen apply remove-bundle          # drops 05-chromium by default
```

`drop:` is a regex over bundle filenames.

## Measured

Removing `05-chromium` from 64-bit Debian:

| | Before | After |
|---|---|---|
| `slax/modules/` | 394 MiB | 315 MiB |
| ISO | 416 MiB | **336 MiB** |

Booted afterwards: livekit mounts five bundles instead of six and reaches
`Live Kit done, starting slax`. `kitchen probe` reports the change as one line —
`bundles.05-chromium.sb  expected 'present', got 'REMOVED'`.

## What is safe to drop

| Bundle | Size (Debian / Slackware) | Notes |
|---|---|---|
| `05-chromium` | 79 / 115 MiB | **Best first candidate** — see [remove-chromium](remove-chromium.md) for the dedicated preset. The desktop launcher turns into an on-demand installer rather than breaking, though **how it recovers differs by flavour**: Debian runs `apt install chromium`, Slackware downloads the bundle from slax.org and `slax activate`s it. Either way it needs a network. |
| `06-devel` | — / 81 MiB | Slackware only; gcc, headers, make. |
| `01-firmware` | 91 / 80 MiB | Only if you know the target hardware. It is wireless/NIC firmware — note it contains **no GPU firmware** at all, so removing it does not affect graphics. |
| `02-xorg`, `03-desktop` | 59+38 / 15+20 MiB | Leaves a console-only system. Legitimate, but pair with a `text` boot parameter so the X autostart unit does not fire and retry. |

**Never remove `01-core`** — it is the root filesystem.

## Building without firmware

Using an image that contains firmware implies acceptance of each firmware's license terms — see
[NOTICE.md](../../NOTICE.md). If you would rather not, build without it. In a profile:

```yaml
recipes:
  - name: remove-bundle
    vars:
      drop: "01-firmware"
```

and do not apply [`firmware-refresh`](firmware-refresh.md). What that leaves out, and what it leaves
behind:

- **All of stock Slax's non-free firmware goes**: Wi-Fi, wired NIC and Bluetooth for atheros, iwlwifi,
  realtek, brcm80211, bnx2, cavium, libertas, ti-connectivity, zd1211, Intel ipw2x00, and the Broadcom
  b43 blobs. Many laptops lose Wi-Fi.
- **`firmware-linux-free` stays** — it lives in `01-core`, and it is free firmware.
- **The package database still lists the removed packages as installed.** Each stock bundle carries a
  complete `var/lib/dpkg/status`, and the ones above `01-firmware` still record them. That is
  documented rather than changed. A recipe that installs one of those packages again afterwards has
  to set `apt: {reinstall: true}`, or apt will believe it is already there and do nothing.

## Why numbering still matters

Removing a bundle leaves a gap in the numeric sequence (`01,01,02,03,04` after dropping `05`) and
that is completely fine: `sortmod` sorts by the numeric prefix and does not care about gaps. Do not
renumber the survivors to "tidy" the sequence — you would change their relative priority.
