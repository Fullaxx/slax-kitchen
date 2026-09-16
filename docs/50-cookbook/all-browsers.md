# `all-browsers` — six browsers in one bundle

**Status: schema-valid** — the YAML validates and every number below was read from a vendor index or
out of a `.deb`, but **nothing has been built**. No bundle exists, no ISO has been packed, and no
browser has been run. Raise this to `matrix-verified` after a real
`ci/recipe-matrix.sh debian-64bit-12.2.0`, and not before.

```sh
kitchen apply all-browsers
```

Zero configuration. Debian, 64-bit only.

## What it does

Removes the stock `05-chromium.sb` and builds one bundle carrying **Chromium, Firefox ESR, Brave,
Google Chrome, Microsoft Edge and Vivaldi**. It is a strict superset of
[`chromium-current`](chromium-current.md) and [`firefox-esr`](firefox-esr.md) — do not apply those
alongside it, and see [Drop, then add](#drop-then-add) for why the engine refuses if you try.

Want two browsers instead of six, or a 32-bit image? That is
[`debian-browsers`](debian-browsers.md), which is also the one that adds nothing to what your image
trusts.

## Measured

Read from each vendor's own `dists/stable` index on **2026-09-16**, amd64, taking the *highest*
version in each — which is what apt installs.

| package | version | `.deb` | installed |
|---|---|---|---|
| `microsoft-edge-stable` | 153.0.4234.32-1 | 183.8 MiB | **640.5 MiB** |
| `brave-browser` | 1.95.101 | 141.7 MiB | 462.5 MiB |
| `vivaldi-stable` | 8.2.4133.55-1 | 131.9 MiB | 454.6 MiB |
| `google-chrome-stable` | 153.0.8010.47-1 | 135.4 MiB | 433.9 MiB |
| `chromium` + `-common` + `-sandbox` | 152.0.7977.82-1~deb12u1 | 104.0 MiB | 338.9 MiB |
| `firefox-esr` | 140.16.0esr-1~deb12u1 | 73.3 MiB | 272.2 MiB |
| **total** | | **~770 MiB** | **~2,600 MiB** |

> **Read the index, not the first stanza in it.** Brave's `Packages` file carries **183** versions of
> `brave-browser` and Edge's carries **189**. The first stanza in each is from 2021 and is not what
> apt picks. The first draft of this page quoted Brave 1.51.118 and Edge 128.x for exactly that
> reason — years stale, and it looked like a measurement. Sort by version, take the last.

### The ISO

**Estimated, not measured.** Two compression data points exist in this repo for large Debian
payloads — LibreOffice and `chromium-current`, both about **3.4:1**. Applying that:

| | |
|---|---|
| bundle | **~0.77–0.89 GiB** *(unverified)* |
| ISO | 416 MiB stock − 79.1 (`05-chromium` removed) + the above = **~1.10–1.20 GiB** *(unverified)* |

[composing-bundles](../40-workflow/composing-bundles.md) independently predicts **1.0–1.2 GB for a
seven-browser image**, written before this recipe existed. Two calculations with no shared inputs
landing in the same band is worth something — but note it lands at the *top* of it, which is what you
would expect, since Edge alone is 640.5 MiB installed today against the 564 MiB that prediction
assumed. Browser binaries hold more already-compressed data than an office suite, so the real number
is more likely above this range than below it.

Both documented ceilings still hold, with roughly 3× headroom: under 4 GiB for a single `.sb`, and
under 4 GiB for the ISO to sit on a FAT32 stick as a file. **`toram` does not.** It copies the whole
image into RAM, so a 1.2 GiB ISO wants 1.2 GiB *before* the running system gets any — `toram` stops
being usable on a 2 GiB machine, and [`boot-cmdline`](boot-cmdline.md) bakes `toram` into every entry
by default. That is a real change in what this image is for.

## Why Tor Browser is not here

It was not forgotten, and the reason is packaging rather than preference.

- **No Tor Browser `.deb` exists anywhere.** `deb.torproject.org` ships `tor`, `tor-geoipdb`,
  `tor-dbgsym` and a keyring — the daemon, not the browser.
- `torbrowser-launcher` is in Debian (bookworm `contrib`, both arches) and would have been the
  easiest of the seven to add. It is a **downloader**: the browser is fetched on first run, over the
  network, into the live overlay — every boot, without persistence. Baking it in produces a launcher
  that cannot launch, and an offline boot from a stick is most of what a live system is for.

There is a real route, and it is a different recipe: `bundle.fromTarball` against a pinned
`tor-browser-linux-*.tar.xz`. Tor publishes **both** `x86_64` and `i686` builds (15.0.23 at the time
of writing), and the archive is a single `tor-browser/` root, so `strip: 1` would place it. Note that
Tor Browser is reported to refuse running as root, which matters because Slax boots as root —
**unverified here**, though every Chromium-derived browser refuses root too, which is why Slax
already ships a `guest` user to run one.

## Why 64-bit only

Not a limitation of `bundle.packages`. Read from each repository's own `Release`, 2026-09-16:

| repository | `Architectures:` |
|---|---|
| `dl.google.com/linux/chrome/deb` | `amd64 arm64` |
| `brave-browser-apt-release.s3.brave.com` | `amd64 arm64` |
| `packages.microsoft.com/repos/edge` | `amd64 arm64 armhf all` |
| `repo.vivaldi.com/stable/deb` | **`amd64`** |

**Four of the six have no i386 package at all**, so there is nothing to install and nothing a `when:`
guard could rescue. `chromium` and `firefox-esr` do — which is exactly what
[`debian-browsers`](debian-browsers.md) is. On a 32-bit target the matrix reports this recipe as a
skip rather than a failure, the same way [`bundle-from-tarball`](bundle-from-tarball.md) handles fzf
being amd64-only.

## ⚠ The image will trust four vendor apt repositories

Read this before you hand an image built with this recipe to anyone. It is not something the recipe
can switch off, and it is accepted deliberately rather than hidden.

`keep: false` — which this recipe sets on all four sources — keeps **kitchen's** source list and
pinned keyring out of the bundle. It cannot keep the *repositories* out, because all four vendor
packages install their own repository and key in their postinst, and that is ordinary dpkg output
which no exclusion pattern can reach. Measured by reading the `control.tar` of each `.deb`:

| package | writes |
|---|---|
| `google-chrome-stable` | `/etc/apt/sources.list.d/google-chrome.sources`, `/usr/share/keyrings/google-chrome.gpg` |
| `brave-browser` | `brave-browser.sources`, `/usr/share/keyrings/brave-browser.gpg` |
| `microsoft-edge-stable` | `microsoft-edge.sources`, `/usr/share/keyrings/microsoft-edge.gpg` |
| `vivaldi-stable` | `vivaldi.sources`, `/usr/share/keyrings/vivaldi-16BD9233.gpg` |

Brave is the sharpest of the four. `brave-browser` **Depends on `brave-keyring`**, a separate package
whose postinst ends in:

```sh
ln -sf /usr/share/keyrings/brave-browser-archive-keyring.gpg \
       /etc/apt/trusted.gpg.d/brave-browser-release.gpg
```

`/etc/apt/trusted.gpg.d/` is trusted for **every** source, not just Brave's — strictly weaker than
the `signed-by=` pinning kitchen uses. Its two guard clauses test for a
`sources.list.d/brave-browser-release.list` that kitchen never writes, and `brave-keyring` is
configured *before* `brave-browser` writes its own list, so the guards miss and the symlink lands.

So a booted Slax carrying this bundle will `apt-get update` against Google, Brave, Microsoft and
Vivaldi and holds their keys. If that is not acceptable for your image, use
[`debian-browsers`](debian-browsers.md), which has none of it. If you want these six browsers *and*
the repositories disabled, mask them from the writable layer rather than fighting dpkg — rootcopy
beats every bundle, and [`remove-chromium`](remove-chromium.md) documents the same trick for a
`.desktop` file:

```yaml
- verb: rootcopy.files
  files:
    - dest: /etc/apt/sources.list.d/google-chrome.sources
      mode: "0644"
      content: "# disabled\n"
```

### Why `keep: false` rather than `keep: true`

Since the repositories ship regardless, the choice here is about **redundant configuration, not the
trust surface** — which is identical either way.

`keep:` governs only the two files kitchen itself writes per source:
`etc/apt/sources.list.d/<name>.list`, and the sha256-pinned
`usr/share/keyrings/<name>-archive-keyring.{asc,gpg}`. `false` excludes them from the bundle after
they have done their job in the chroot; `true` ships them.

**`apt update && apt upgrade` works either way**, because the vendors configure themselves — verified
above. So `keep: true` would add a *second*, duplicate entry per vendor and make apt fetch eight
indexes instead of four. Hence `false`.

The one argument for `keep: true`, recorded and not taken: if a vendor postinst ever partly failed in
the chroot, kitchen's entry would remain as a fallback — and kitchen's is the better-formed of the
two, sha256-pinned and `signed-by=`-scoped rather than leaning on Brave's global symlink. It is a
one-word change per source if that failure is ever seen. See
[`bundle.packages`](../90-reference/verbs.md) for the general rule.

## The Recommends check, and why it is short

`bundle.packages` installs with `--no-install-recommends`, which is what cost LibreOffice a working
Base — see [`libreoffice`](libreoffice.md). Doing the same check here: **the four vendor packages
declare no `Recommends:` at all.** `fonts-liberation`, `libvulkan1`, `xdg-utils`, `wget` and
`ca-certificates` are hard `Depends:` on all four, so nothing is silently dropped. Every real gap is
on the Debian side, and three are named explicitly:

- **`chromium-sandbox`** — a `Recommends` of `chromium`, and its entire content is the setuid sandbox
  helper. Without it Chromium runs with no sandbox. `bundle.packages` preserves setuid correctly
  (`chmod` after `chown`, because the kernel clears the bit on `chown`), so it survives the pack.
- **`libavcodec59`** — a `Recommends` of `firefox-esr` and its only route to H.264 and AAC, 14.3 MiB.
  The five Chromium-family browsers ship their own codecs, so this buys playback for Firefox alone. A
  browser that silently cannot play video is exactly the failure this project refuses to ship. The
  `Recommends` is a 20-way alternation; `59` is the one bookworm actually has, and
  `libavcodec-extra59` adds encoders nobody needs in order to browse.
- **`libgl1-mesa-dri`** — a `Recommends` of `chromium-common`, 24.6 MiB. Without it every
  Chromium-family browser here falls back to llvmpipe. Whether `02-xorg` already carries it is
  **unverified**; if it does, apt sees it installed and the line costs nothing, which is why naming it
  is safe either way.

Deliberately left out: `libu2f-udev` (in none of the six `Depends` lines today) and
`pulseaudio`/`pipewire-pulse` (neither a `Depends` nor a `Recommends` of anything here).

## Dependencies, and what the bundle actually contains

The bundle's contents are **a measured filesystem delta, not the package list above**. `_manifest()`
snapshots every path as `(type, size, mtime_ns, mode)` before the install and again after, and
`added + modified` is what gets squashed.

So every transitive dependency apt pulls in that was not already on disk lands in the bundle, and
anything the stack below already provides — `libgtk-3-0` from `02-xorg`, say — is not duplicated,
because it does not change. One apt transaction solves **one** dependency closure for all six
browsers, so a library three of them share is installed once and appears once. That is the concrete
argument for one bundle rather than six.

<a id="drop-then-add"></a>

## Drop, then add — and why this recipe does the removal itself

`check_plan_order` requires every `bundle.remove` to precede every `bundle.packages`, across recipes
*and* across invocations — it seeds prior bundles from `.kitchen/journal.yaml`. Doing the removal
inside this recipe makes that ordering true by construction, exactly as
[`chromium-current`](chromium-current.md) does.

It also makes `from:` come out right on its own. With `05-chromium` gone the default stack is
`01-core … 04-apps`, so apt pulls the shared browser runtime back into this bundle and the result
stands on its own. `05-chromium` was never only Chromium: it carries `libnss3`, `libnspr4`,
`libopus0`, `libflac12`, `libwebpmux3` and eighteen more — see
[the bundle map](../30-inventory/bundle-map.md).

> **Do not list this recipe with `chromium-current`, `firefox-esr` or `debian-browsers`.** It
> supersedes the first two, and the last is its alternative. Both this recipe and `debian-browsers`
> remove `^05-chromium\.sb$`, so a plan containing both makes `_only_above` compare the literal `05`
> against an already-built `13`, and it is refused before anything is modified. That refusal is
> correct — you would have been shipping two overlapping browser bundles.

## What it does *not* do

- **It is not verified.** `schema-valid` is the highest rung this has reached. Nothing has been built,
  packed or booted, and every size above is arithmetic over indexes.
- **It does not pin browser versions.** `stable main` is a moving target for all four vendors, so two
  builds a week apart produce different browsers. Only the signing *keys* are pinned — and those are
  live URLs the vendors rewrite, so a rotation fails the build with a `want`/`got` mismatch rather
  than trusting a new key. That is the design working, and it needs a maintenance commit when it
  happens.
- **It does not keep the vendor repositories out of your image** — see the warning above.
- **It does not make the browsers runnable as root.** Chromium and every Chromium-derived browser
  here refuse to run as root, which is why both Slax flavours ship a `guest` user (uid 1000) purely to
  run the browser. Firefox is the exception. Nothing in this recipe changes who the desktop runs as.
- **It does not leave the base system's libraries untouched, despite nothing being "upgraded".**
  Neither this recipe nor any other here runs `apt-get upgrade` — the only commands are
  `apt-get update -qq` and `apt-get install -y -qq --no-install-recommends`. But `install` still
  upgrades a package when dependency resolution demands it, and any library it refreshes lands in the
  delta's `modified` set, ships inside this bundle, and **at boot the union serves that newer copy to
  the whole system**, because the higher bundle number wins. The stock bundles stay byte-identical on
  the ISO — `kitchen probe` still recognises them — while their contents are partly shadowed at
  runtime. Mostly benign, since bookworm-security holds ABI stable within the release, but the scale
  is **unmeasured**.
- **It does not add desktop entries beyond what the packages register.** Each installs a `.desktop`
  file and registers `x-www-browser` through `update-alternatives`, and Slax's "Web Browser" button
  routes through `fbliveapp chromium`, which still finds `/usr/bin/chromium` and works. Whether the
  other five appear in `xlunch` or `fbappselect` is **unverified**.
- **It does not give you Tor Browser** — see above.
- **It does not work on 32-bit or on Slackware**, and the `compat:` block says why.
- **It does not shrink.** This is by a wide margin the largest thing in this cookbook — roughly three
  times LibreOffice — and it turns a 416 MiB ISO into something over a gigabyte.

## Slackware

Not supported, and there is no unsupported route either. Slax's Slackware base is frozen while its
`slackpkg` points at a mirror years ahead of it, so packages installed from it may not run — and none
of these four vendors publishes a Slackware package in any case. The supported route for software
there is a pinned `.txz` through [`bundle-from-txz`](bundle-from-txz.md).
