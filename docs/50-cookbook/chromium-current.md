# `chromium-current` — replace the 2023 browser

**Status: matrix-verified** — built and structurally asserted on both Debian targets; the
measurements below are from a real build. Not yet booted to a desktop, so the browser has not been
*run*.

```sh
kitchen apply chromium-current
```

Zero configuration. Debian only.

## Why

Both Slax flavours ship **chromium 117.0.5938.149**, released September 2023. A live system is
mostly used for browsing, so this is the most exposed thing in the image and it is years of
security fixes behind.

Nothing exotic is needed to fix it. The stock `/etc/apt/sources.list` already carries
`bookworm-security`, which is where the current build lives — so this is an ordinary
`bundle.packages` step with no repository configuration at all.

## Measured

Built on `debian-64bit-12.2.0` on 2026-10-04, with the removal listed first. The version is the
one `bookworm-security` carried that day:

| | |
|---|---|
| before | `05-chromium.sb`, chromium **117.0.5938.149**, 79 MiB |
| after | `10-chromium.sb`, chromium **154.0.8037.92-1~deb12u1**, 116 MiB |
| packages declared | 20 |

The bundle is larger because the 22 media and crypto libraries that came with the old Chromium
left with it, and the new bundle carries current versions of all of them.

Applied on its own, the [recipe matrix](../60-testing/ci.md#recipe-sizes) measured the ISO at
**528 MiB** on 2026-10-04: the 2023 bundle stays, and this bundle is smaller, 113 MiB with four
packages declared, because apt finds its libraries already installed.

## Pair it with a removal, listed first

This recipe adds a bundle at `10`, which outranks `05`, so the browser you get is the current one
either way. What the removal buys is the space and the leftovers:

```yaml
recipes:
  - name: remove-bundle
    vars: {drop: "^05-chromium\\.sb$"}
  - chromium-current
```

- **Without it**, both copies stay on the ISO — 79 MiB for a Chromium nobody can reach — and files
  the 2023 build had that the current one does not are still visible in the union, because a bundle
  can add and replace but **cannot delete**.
- **With it**, `from:` also comes out right: the default stack is `01-core … 04-apps`, so `apt`
  reinstalls the browser runtime into the new bundle and the result is self-contained. The
  measurements above are from a build done that way; without the removal apt finds those libraries
  installed and the bundle is smaller, at the price of assuming `05` stays.

Removal is a recipe of its own and goes first — see [composing
bundles](../40-workflow/composing-bundles.md) and [`remove-bundle`](remove-bundle.md).

## The desktop launcher

`fbliveapp` looks for `/usr/bin/chromium`, which the new bundle provides, so the existing launcher
keeps working. This has not been confirmed on a booted desktop — see the status line.

## Slackware

Not supported. Slax's Slackware Chromium is an AlienBOB `.txz`, and `slackpkg` points at a mirror
years ahead of the frozen base (upstream issues 12 and 13), so packages installed from it do not
run. The supported route there is a pinned `.txz` through
[`bundle-from-txz`](bundle-from-txz.md).
