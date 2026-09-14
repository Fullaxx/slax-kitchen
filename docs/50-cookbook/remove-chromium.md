# `remove-chromium` — drop the browser bundle

**Status: boot-verified** — built, probed and booted.

```sh
kitchen apply remove-chromium
```

Zero configuration. For anything else, or several bundles at once, use
[`remove-bundle`](remove-bundle.md) — both use the same `bundle.remove` verb, this one is just a
named preset so it can be dropped straight into a profile.

## Measured

| | Debian 12.2.0 | Slackware 15.0.4 |
|---|---|---|
| bundle size | 79 MiB | 115 MiB |
| ISO before | 416 MiB | 455 MiB |
| ISO after | **336 MiB** | **340 MiB** |

Roughly a fifth of the image. It is the single largest optional bundle on both flavours.

## The desktop launcher stays, on purpose

The Fluxbox menu and xlunch keep their **Web Browser** entry, and that is correct rather than a
leftover. Slax routes it through `fbliveapp`, not directly at the binary:

```
[exec] (Web Browser) { fbstartupnotify && fbliveapp chromium --no-default-browser-check }
```

`fbliveapp` checks whether `/usr/bin/chromium` exists and, if it does not, offers to install it:

> *Chromium is a free and open source version of the famous Chrome browser, developed by Google.*
> *Chromium Web Browser is not yet installed. Do you like to download and install it now?*

So removing the bundle turns the launcher into an on-demand installer. That is upstream's design.

## ⚠️ Recovery differs by flavour

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

## Air-gapped builds

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
```

The Fluxbox menu entry lives inside `03-desktop.sb` at `/root/.fluxbox/menu`, so removing *that* one
needs either a rootcopy of the whole menu file or a rebuilt bundle.

## Getting it back later

Nothing here is one-way. The bundle is an ordinary file: keep a copy and drop it into
`/slax/modules/` on a writable stick, or `slax activate /path/to/05-chromium.sb` on a running
system to load it without rebooting.
