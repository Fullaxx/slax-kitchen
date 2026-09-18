# `remove-bundle` — slim the ISO by dropping bundles

**Status: boot-verified** — built, probed and booted.

```sh
kitchen apply remove-bundle          # drops 05-chromium by default
```

`drop:` is a regex over bundle filenames, anchored by default: `^05-chromium\.sb$`.

**This is the only recipe that removes anything, and it does nothing else** — a rule
[`kitchen validate`](../90-reference/cli.md) enforces. A recipe that removed a bundle *and* built one
would decide where it could sit in a profile: a bundle is built against everything below it, so
removing one afterwards can leave an unresolvable `NEEDED` that no gate can see, and `check_plan_order`
refuses that order. List this recipe first and every additive recipe then composes in any order:

```yaml
recipes:
  - name: remove-bundle
    vars: {drop: "^(05-chromium|01-firmware)\\.sb$"}   # one entry, several bundles
  - firmware-refresh
  - debian-browsers
```

**Several bundles go in one pattern, not in two entries.** Each recipe is applied once, so a profile
naming this one twice is refused rather than quietly dropping the second entry and its `vars:`.

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
| `05-chromium` | 79 / 115 MiB | **Best first candidate**, and the default. The desktop launcher turns into an on-demand installer rather than breaking — see below. |
| `06-devel` | — / 81 MiB | Slackware only; gcc, headers, make. |
| `01-firmware` | 91 / 80 MiB | Only if you know the target hardware. It is wireless/NIC firmware — note it contains **no GPU firmware** at all, so removing it does not affect graphics. |
| `02-xorg`, `03-desktop` | 59+38 / 15+20 MiB | Leaves a console-only system. Legitimate, but pair with a `text` boot parameter so the X autostart unit does not fire and retry. |

**Never remove `01-core`** — it is the root filesystem.

## Dropping Chromium: the launcher stays, on purpose

The Fluxbox menu and xlunch keep their **Web Browser** entry, and that is correct rather than a
leftover. Slax routes it through `fbliveapp`, not directly at the binary:

```
[exec] (Web Browser) { fbstartupnotify && fbliveapp chromium --no-default-browser-check }
```

`fbliveapp` checks whether `/usr/bin/chromium` exists and, if it does not, offers to install it:

> *Chromium is a free and open source version of the famous Chrome browser, developed by Google.*
> *Chromium Web Browser is not yet installed. Do you like to download and install it now?*

So removing the bundle turns the launcher into an on-demand installer. That is upstream's design.

### ⚠️ Recovery differs by flavour

This matters if the target machine has no network, and the two builds are genuinely different:

| Flavour | What `fbliveapp` runs |
|---|---|
| **Debian** | `apt install --yes chromium chromium-sandbox` — needs working apt **and** a reachable Debian mirror |
| **Slackware** | `wget … slax.org/download-web-browser.php?b=slackware&v=…` then `slax activate` — needs slax.org reachable |

Two consequences worth planning for:

- On **Debian**, recovery depends on Debian's archive, and bookworm is oldstable. It works today; it
  is not guaranteed forever.
- On **Slackware**, recovery depends on `slax.org` still serving that endpoint for a release that has
  had no update since 2023.

### Air-gapped builds

If the machine will never have a network, an installer prompt that cannot succeed is worse than no
entry at all. Mask the launcher with a rootcopy file — no bundle rebuild needed, because rootcopy
lands in the writable layer and beats every bundle:

```yaml
- verb: rootcopy.files
  files:
    - dest: /usr/share/applications/5chromium.desktop
      mode: "0644"
      content: |
        [Desktop Entry]
        Type=Application
        Name=Web Browser
        NoDisplay=true
        Hidden=true
```

**`Hidden=true` is the line that does the work**, and it is easy to leave out. Slax's
`xlunch_genquick` greps an anchored `^(Name|Icon|Exec|Hidden|Terminal)=`, so it **never reads
`NoDisplay`** — that key is for the freedesktop consumers, and it is kept for them. `Hidden=true`
it does read, and acts on before it looks for an icon:

```sh
if [ "$Hidden" = "true" ]; then
   continue
fi
```

Without it, a stub like this suppresses the tile only by accident: it ships no `Icon=`, the empty
string fails the generator's final `[ -e "$Icon" ]`, and the entry is dropped for the wrong reason.
Add an `Icon=` line to such a stub — a reasonable-looking improvement — and the entry you were
hiding comes back. See [issue 15](../30-inventory/known-upstream-bugs.md), and
`tests/unit/test_desktop_entries.py`, which refuses this shape.

The Fluxbox menu entry lives inside `03-desktop.sb` at `/root/.fluxbox/menu`, so removing *that* one
needs either a rootcopy of the whole menu file or a rebuilt bundle.

### Getting it back later

Nothing here is one-way. The bundle is an ordinary file: keep a copy and drop it into
`/slax/modules/` on a writable stick, or `slax activate /path/to/05-chromium.sb` on a running
system to load it without rebooting.

## Building without firmware

Using an image that contains firmware implies acceptance of each firmware's license terms — see
[NOTICE.md](../../NOTICE.md). If you would rather not, build without it. In a profile:

```yaml
recipes:
  - name: remove-bundle
    vars:
      drop: "^01-firmware\\.sb$"
```

and do not apply [`firmware-refresh`](firmware-refresh.md). What that leaves out, and what it leaves
behind:

- **All of stock Slax's non-free firmware goes**: Wi-Fi, wired NIC and Bluetooth for atheros, iwlwifi,
  realtek, brcm80211, bnx2, cavium, libertas, ti-connectivity, zd1211, Intel ipw2x00, and the Broadcom
  b43 blobs. Many laptops lose Wi-Fi.
- **`firmware-linux-free` stays** — it lives in `01-core`, and it is free firmware.
- **Or replace it rather than dropping it.** Removing `01-firmware` *and* applying
  [`firmware-refresh`](firmware-refresh.md) gives two bundles with their license texts and no b43
  blobs: `09-firmware-debian.sb`, measured at **139.4 MiB** against 48.8 MiB when it layers on top
  of the stock bundle — because the reinstall then really reinstalls the firmware instead of only
  the documentation Slax deleted — and `09-firmware-linux.sb` at 5.4 MiB either way.
- **The package database still lists the removed packages as installed.** Each stock bundle carries a
  complete `var/lib/dpkg/status`, and the ones above `01-firmware` still record them. That is
  documented rather than changed. A recipe that installs one of those packages again afterwards has
  to set `apt: {reinstall: true}`, or apt will believe it is already there and do nothing.

## Why numbering still matters

Removing a bundle leaves a gap in the numeric sequence (`01,01,02,03,04` after dropping `05`) and
that is completely fine: `sortmod` sorts by the numeric prefix and does not care about gaps. Do not
renumber the survivors to "tidy" the sequence — you would change their relative priority.
