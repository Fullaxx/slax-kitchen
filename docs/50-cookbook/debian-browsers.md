# `debian-browsers` — a current Chromium and Firefox, on both arches

**Status: boot-verified** — built on both Debian targets, packed, and booted to `slax login:` with
all three livekit markers under TCG. CI's recipe matrix builds it on `debian-64bit-12.2.0` and
`debian-32bit-12.2.0`. The browsers themselves have **not been run**; that would be
`runtime-verified`.

```sh
kitchen apply debian-browsers
```

Zero configuration. Debian, **32-bit and 64-bit**.

## What it does

Removes the stock `05-chromium.sb` and builds one bundle carrying a current **Chromium** and
**Firefox ESR**. It is the union of [`chromium-current`](chromium-current.md) and
[`firefox-esr`](firefox-esr.md) in a single bundle — do not apply those alongside it.

## It exists for two reasons, and only one of them is architecture

**1. It is the 32-bit answer.** [`all-browsers`](all-browsers.md) carries six browsers and is
64-bit-only, because four of its six publish no i386 package at all. These two do — measured in
`bookworm-security` on 2026-09-16, both architectures carrying the identical current versions.

**2. It adds nothing to what your image trusts.** There is no `apt:` block here at all. Both packages
come from Debian proper, so there is no vendor repository, no signing key to pin, and no `keep:`
decision to make. `all-browsers` necessarily ships four vendor repositories and their keys — the
packages install those themselves and `keep: false` cannot prevent it, which that page documents at
length. **Someone on 64-bit who wants two current browsers without four third-party trust roots
wants this recipe, not that one.**

## Measured

From `bookworm-security` on **2026-09-16**. The stock image ships Chromium 117.0.5938.149 from
September 2023, so this is a large jump in a browser's worth of security fixes.

| package | version | i386 `.deb` / installed | amd64 `.deb` / installed |
|---|---|---|---|
| `chromium` | 152.0.7977.82-1~deb12u1 | 77.3 / 262.7 MiB | 75.2 / 275.2 MiB |
| `chromium-common` | 152.0.7977.82-1~deb12u1 | 28.9 / 60.2 MiB | 28.7 / 63.3 MiB |
| `chromium-sandbox` | 152.0.7977.82-1~deb12u1 | 0.1 / 0.4 MiB | 0.1 / 0.4 MiB |
| `firefox-esr` | 140.16.0esr-1~deb12u1 | 77.4 / 290.9 MiB | 73.3 / 272.2 MiB |
| **total** | | **183.8 / 614.2 MiB** | **177.3 / 611.2 MiB** |

Plus `libavcodec59` at 14.3 MiB and `libgl1-mesa-dri` at 26.2 MiB (i386) / 24.6 MiB (amd64)
installed, both named deliberately — see below.

### Built — measured 2026-09-16

| | 64-bit | 32-bit |
|---|---|---|
| `05-chromium.sb` removed | −79 MiB | −81 MiB |
| `14-browsers.sb` | **223.3 MiB**, 736 files | **226.8 MiB**, 714 files |
| declared in the dpkg fragment | 53 packages | 52 packages |
| delta | 861 added, 141 modified | 838 added, 140 modified |
| merged database | 622 packages | 622 packages |
| **ISO** | **560.0 MiB** | **561.1 MiB** |
| boot | 3/3 livekit markers | 3/3 livekit markers |

The estimate written before the build was ~517 MiB — 416 − 79 + ~180 MiB of bundle at the ~3.4:1
ratio this repo had measured twice before. **The real figure is 560 MiB, 8% higher**, because the
bundle came out at 223 MiB rather than 180: browser binaries hold more already-compressed data than
the office suite that ratio was drawn from. The estimate is recorded rather than quietly replaced,
because the direction of the error is the useful part.

Two things nobody predicted, both real: the 32-bit bundle is *larger* than the 64-bit one despite
i386 binaries usually being smaller, and it declares one package fewer.

Unlike [`all-browsers`](all-browsers.md) at 1227 MiB, `toram` stays practical here.

> **A gotcha, if you go looking for these versions yourself.** They are in **`bookworm-security`**,
> not `bookworm` — main still carries chromium 150.0.7871.100 and firefox-esr 140.12.0esr. The
> security archive publishes its index as **`.xz` under `main/`**, despite its `Release` advertising
> `Components: updates/main`. Fetching `main/…/Packages.gz` returns an **empty body with HTTP 200**,
> which reads exactly like "no package for this architecture" — and is how this recipe was briefly,
> and wrongly, believed to be 64-bit-only. Stock `/etc/apt/sources.list` already carries
> `bookworm-security`, so no source changes are needed; the same point
> [`chromium-current`](chromium-current.md) makes.

## The three named Recommends

`bundle.packages` installs with `--no-install-recommends`, which is what cost LibreOffice a working
Base — see [`libreoffice`](libreoffice.md). Three are named back explicitly:

- **`chromium-sandbox`** — a `Recommends` of `chromium`, and its entire content is the setuid sandbox
  helper. Without it Chromium runs with no sandbox. `bundle.packages` preserves setuid correctly
  (`chmod` after `chown`, because the kernel clears the bit on `chown`), so it survives the pack.
- **`libavcodec59`** — a `Recommends` of `firefox-esr` and its only route to H.264 and AAC. Chromium
  carries its own codecs, so this is for Firefox alone. The `Recommends` is a 20-way alternation;
  `59` is the one bookworm actually has.
- **`libgl1-mesa-dri`** — a `Recommends` of `chromium-common`. Without it Chromium falls back to
  llvmpipe. Whether `02-xorg` already carries it is **unverified**; if it does, apt no-ops and naming
  it costs nothing.

`chromium-common` is named explicitly rather than left to the solver, so the declared set matches
what this page documents. apt would pull it in regardless.

## Drop, then add

`check_plan_order` requires every `bundle.remove` to precede every `bundle.packages`, across recipes
*and* across invocations — it seeds prior bundles from `.kitchen/journal.yaml`. Doing the removal
inside this recipe makes that ordering true by construction, exactly as
[`chromium-current`](chromium-current.md) does.

It also makes `from:` come out right on its own. With `05-chromium` gone the default stack is
`01-core … 04-apps`, so apt pulls the shared browser runtime back into this bundle and the result
stands on its own. `05-chromium` was never only Chromium: it carries `libnss3`, `libnspr4`,
`libopus0`, `libflac12`, `libwebpmux3` and eighteen more — see
[the bundle map](../30-inventory/bundle-map.md).

> **This recipe and [`all-browsers`](all-browsers.md) are alternatives, not companions.** Both remove
> `^05-chromium\.sb$`, so a plan containing both makes `_only_above` compare the literal `05` against
> an already-built `13` or `14`, and it is refused before anything is modified.

## Dependencies, and what the bundle actually contains

The bundle's contents are **a measured filesystem delta, not the package list**. `_manifest()`
snapshots every path as `(type, size, mtime_ns, mode)` before the install and again after, and
`added + modified` is what gets squashed. Every transitive dependency apt pulls in that was not
already on disk lands in the bundle; anything the stack below already provides does not, because it
does not change.

## What it does *not* do

- **It has not been run.** `boot-verified` means the image reached `slax login:` with all three
  livekit markers, on both arches. Nobody has opened either browser. That is `runtime-verified`, and
  it needs a desktop boot.
- **It does not pin versions.** `bookworm-security` moves, which is the point of using it — two builds
  a month apart produce different browsers.
- **It does not leave the base system's libraries untouched, despite nothing being "upgraded".** No
  recipe here runs `apt-get upgrade`; the only commands are `apt-get update -qq` and
  `apt-get install -y -qq --no-install-recommends`. But `install` still upgrades a package when
  dependency resolution demands it, and any library it refreshes lands in the delta's `modified` set,
  ships inside this bundle, and **at boot the union serves that newer copy to the whole system**,
  because the higher bundle number wins. The stock bundles stay byte-identical on the ISO —
  `kitchen probe` still recognises them — while their contents are partly shadowed at runtime. Mostly
  benign, since bookworm-security holds ABI stable within the release. **Measured on the 64-bit
  build: 77 regular files**, out of 666 in the bundle, exist in a stock bundle too and are now served
  from this one. They are dominated by mesa's DRI drivers — `iris_dri.so`, `radeonsi_dri.so`,
  `swrast_dri.so` and the rest — because `libgl1-mesa-dri` was refreshed. The build line reports
  `141 modified`, but that counts directories too, whose mtimes change whenever anything lands
  inside them; 77 is the file count.
- **It does not make Chromium runnable as root.** Chromium refuses, which is why both Slax flavours
  ship a `guest` user (uid 1000) purely to run the browser. Firefox is the exception. Nothing here
  changes who the desktop runs as.
- **It does not give you Brave, Chrome, Edge or Vivaldi** — that is [`all-browsers`](all-browsers.md),
  and it costs four vendor repositories in your image plus roughly another 600 MiB of ISO.
- **It does not work on Slackware.** Slax's Slackware base is frozen while its `slackpkg` points at a
  mirror years ahead of it, so packages installed from it may not run. The supported route there is a
  pinned `.txz` through [`bundle-from-txz`](bundle-from-txz.md).
