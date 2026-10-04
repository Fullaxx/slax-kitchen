# `firefox-esr` — add a second browser

**Status: matrix-verified** — built and structurally asserted on both Debian targets, in three
different bundle sets; the measurements below are real. Not yet booted to a desktop, so the browser
has not been *run*.

```sh
kitchen apply firefox-esr
```

Zero configuration. Debian only.

## Why it is easy, and what that says

No Slax image ships a second browser. `firefox-esr` is in Debian proper — no vendor repository, no
signing key — and it is genuinely built for **i386 as well as amd64**, which is rare enough among
browsers to matter on a distribution that still ships 32-bit images.

Measured 2026-10-04: **153.4.0esr-1~deb12u1**, the version `bookworm-security` carried that day, is
a 76.1 MiB `.deb` on amd64 and 79.6 MiB on i386. The [recipe
matrix](../60-testing/ci.md#recipe-sizes) built it into an 81.3 MiB bundle on `debian-64bit-12.2.0`
and 85.7 MiB on `debian-32bit-12.2.0`.

Contrast with Brave, Chrome, Edge and Vivaldi, which are distributed only through vendor apt
repositories and therefore need `apt.sources` — see [`bundle.packages`](../90-reference/verbs.md).
And with Tor Browser: `torbrowser-launcher` is a *downloader*, so the actual browser arrives on
first run over the network. It cannot be baked into a bundle, and an offline boot gets a launcher
that cannot launch.

## This recipe is where `from:` is easiest to see

The same YAML, three bundle sets, all correct. Measured on `debian-64bit-12.2.0`: rows 1 and 2 on
2026-09-14 (`d0f48e8`), with `140.15.0esr`, and row 3 on 2026-09-15 (`7ab6793`):

| beneath it | bundle | packages declared |
|---|---|---|
| stock stack incl. `05-chromium` | 79,480 KiB | 2 |
| `05-chromium` removed | 81,004 KiB | 5 |
| `chromium-current` applied first | 79,648 KiB | 3 |

**1.9% between the extremes.** Firefox's own payload dominates, so `from:` is not a size knob here.
What it decides is whether this bundle still stands up if someone removes the bundle underneath it.
Leaving `from:` unset means "whatever is actually below me", which is what you want.

In the third row the recipe stacked itself on `10-chromium.sb` automatically, because that is what
the default resolves to once it exists — no ordering declaration needed.

> **Row 3 read `5` until [#2](https://github.com/Fullaxx/slax-kitchen/issues/2) was fixed**, because
> a build chroot never merged the status fragments of the add-on bundles beneath it — so `apt` read
> `04-apps.sb`'s 575 packages and believed `10-chromium.sb` had installed nothing. The size did not
> change (79,648 KiB before and after, on `debian-64bit-12.2.0`, 2026-09-15): the files were always
> right, and it was the *declaration* that was wrong by two packages.
>
> **Three, not two.** Row 1 is not the target to match, and it is worth seeing why. Firefox now
> declares `firefox-esr`, `libevent-2.1-7` and `libvpx7`. Stock `05-chromium` carries
> `libevent-2.1-7` and not `libvpx7`, so with it beneath you, Firefox owes two. A *current*
> Chromium no longer pulls `libevent-2.1-7` at all — `10-chromium.sb` declares twenty packages and
> that is not among them — so Firefox owes three. Different bundle, different dependency closure;
> the number is supposed to move.

## Two browsers, one package database

This is the case that the [composing-bundles](../40-workflow/composing-bundles.md) work exists for.
Both bundles ship a status *fragment* rather than Debian's single `var/lib/dpkg/status`, and `pack`
merges them:

```
98-dpkg-db.sb: 575 from 04-apps.sb + 2 fragment(s) -> 598 packages
   10-chromium.sb:10-chromium
   11-firefox.sb:11-firefox
```

Both browsers are listed, nothing below is lost, and either bundle can be removed on its own. Under
the old behaviour whichever bundle landed higher would have replaced the whole database with its
own smaller copy.

## Slackware

Not supported, and not because it fails. Slax's Slackware base is frozen and its `slackpkg` points
at a mirror years ahead of it (upstream issues 12 and 13), so installing from it produces packages
that do not run. Use [`bundle-from-txz`](bundle-from-txz.md) with a pinned `.txz`.
