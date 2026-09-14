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

Built on debian-64bit, 2026:

| | |
|---|---|
| before | `05-chromium.sb`, chromium **117.0.5938.149**, 79 MiB |
| after | `10-chromium.sb`, chromium **152.0.7977.82-1~deb12u1**, 114 MiB |
| packages declared | 20 |

The bundle is larger because the 22 media and crypto libraries that came with the old Chromium
left with it, and the new bundle carries current versions of all of them.

## Drop, then add — not override

The recipe removes `05-chromium.sb` before building. That ordering is the point:

- **Overriding** would leave both copies on the ISO, paying 79 MiB for a Chromium nobody can
  reach — and files the 2023 build had that the current one does not would still be visible in the
  union afterwards, because a bundle can add and replace but **cannot delete**.
- **Removing first** also makes `from:` come out right on its own. With `05-chromium` gone, the
  default stack is `01-core … 04-apps`, so `apt` reinstalls the browser runtime into the new bundle
  and the result is self-contained.

See [composing bundles](../40-workflow/composing-bundles.md) for the reasoning, and
[`remove-chromium`](remove-chromium.md) if you want the browser gone rather than replaced.

## The desktop launcher

`fbliveapp` looks for `/usr/bin/chromium`, which the new bundle provides, so the existing launcher
keeps working. This has not been confirmed on a booted desktop — see the status line.

## Slackware

Not supported. Slax's Slackware Chromium is an AlienBOB `.txz`, and `slackpkg` points at a mirror
years ahead of the frozen base (upstream issues 12 and 13), so packages installed from it do not
run. The supported route there is a pinned `.txz` through
[`bundle-from-txz`](bundle-from-txz.md).
