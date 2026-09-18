# Composing bundles

How to split customization across bundles so the pieces can be added, removed and rebuilt
independently. The worked example is browsers, because that is where every constraint shows up
at once.

## The rule everything else follows from

**A union filesystem composes trees, not files.**

Directories merge: if `01-core` and `07-extras` both contain `usr/bin/`, you see the contents of
both. Files do not: if both contain `etc/issue`, you see exactly one of them — the copy from the
higher-numbered bundle, and the other becomes unreachable.

So any single file holding *whole-system* state can only ever be correct in the topmost bundle that
ships one. There is precisely one such file on Debian:

| | | |
|---|---|---|
| Debian | `var/lib/dpkg/status` | **one file** — cannot compose |
| Debian | `var/lib/dpkg/info/` | directory, one file per package — composes |
| Slackware | `var/lib/pkgtools/packages/` | directory, one file per package — composes |

Measured across the stock Debian ISO, each bundle's `status`:

```
01-core 295 → 01-firmware 307 → 02-xorg 424 → 03-desktop 535 → 04-apps 575 → 05-chromium 600
```

Strictly cumulative, because upstream builds each bundle by chrooting into everything below it. The
top copy is complete, so the system is correct. Slackware's is *not* cumulative — 416 / 206 / 34 /
13 / 32 entries, pure deltas — and does not need to be, because a directory merges.

That chain works, and it costs upstream two things: bundles can only be removed from the top down,
and two of them can never be built independently. Whichever lands higher erases the other's
packages. **This is the constraint that decides how you split a customization.**

`kitchen` breaks the chain instead. Add-on bundles ship no `status` at all; each carries
`var/lib/slax-kitchen/dpkg-status.d/<bundle>` holding only the stanzas it added or changed, and
`kitchen pack` merges base plus fragments into a generated `98-dpkg-db.sb`. The base is the highest
real `var/lib/dpkg/status` in the stack, and **every** fragment merges into it wherever it sits —
a bundle numbered below a stock one is still counted, because `98-dpkg-db.sb` sorts above every
stock bundle and is what dpkg reads at boot.

The one exception is a saved session. `savechanges` squashes the writable layer, so `99-changes-N`
carries a status only if packages changed, and that copy is already the complete merged database —
newer than the fragments that fed it. It therefore supersedes them, and `pack` says so rather than
merging stale versions back over it.

A directory of
fragments composes the way Slackware's database already does, so add-on bundles become independent
and removable in any order.

There is one honest limit. Drop a `.sb` onto a booted stick by hand and no merge runs, so dpkg will
not list that bundle's packages — the software works, `dpkg -l` does not show it. Cosmetic, where
the failure it replaced lost three hundred real packages silently.

## Three properties worth designing for

| | |
|---|---|
| **Dependency-complete** | built against everything that will sit below it, so `apt` does not reinstall libraries the image already has |
| **State-composable** | ships no file that aggregates whole-system state |
| **Removable** | dropping it leaves a consistent system — which follows from the second |

## Removes come first

**Every `bundle.remove` must run before every `bundle.packages` and `bundle.script` in the
plan.** This is enforced: the run is refused before anything is modified.

The reason is the default above. A bundle built against the whole stack beneath it assumes that
stack is still there at boot — `apt` saw those libraries as installed and shipped no copies. Remove
one afterwards and the binaries have an unresolvable `NEEDED`. Nothing catches it: the file delta
is empty for the missing libraries, so the status fragment does not declare them either and the
merged `98-dpkg-db.sb` stays perfectly self-consistent. The image builds, passes every gate, and
fails when someone runs the program.

**So removal is its own recipe, and does nothing else.** `kitchen validate` refuses a recipe that
removes or renumbers a bundle and also builds one, because such a recipe decides where it may sit in
a plan — and that constraint then applies to every recipe listed beside it. `all-browsers` used to
drop `05-chromium` as its first step, which is why a profile had to list it before anything that
built a bundle, for a reason that had nothing to do with browsers.

[`remove-bundle`](../50-cookbook/remove-bundle.md) is that recipe: a `drop:` pattern, several
bundles in one entry if you need them, listed first in the profile. Everything else only adds, so
the rest of the list composes in any order:

```yaml
recipes:
  - name: remove-bundle
    vars: {drop: "^05-chromium\\.sb$"}
  - firmware-refresh
  - debian-browsers
```

The rule is plan-wide rather than per-recipe, because the case that motivated it spans two:
a profile listing `firefox-esr` and then a removal, where each recipe is fine alone.
[`firefox-esr`](../50-cookbook/firefox-esr.md) is the additive model — its comments record that it
is correct with or without `05-chromium` beneath it, and only the size of its bundle changes.

**The one exception is decided, not written down.** A removal whose pattern is anchored on a
literal number higher than every bundle the plan builds — `^98-dpkg-db\.sb$` after a plan that
builds `13-browsers`, say — cannot strand anything, because `from:` already drops everything
sorting at or above the bundle being built. `_only_above` in `lib/apply.py` works that out from
the step list and allows it. Anything it cannot decide — an unanchored or templated pattern — is
treated as disturbing, because a regex can match anything.

## `from:` is a dial, not a switch

`from:` says *what to assume is already present*. It defaults to every bundle that will load below
yours, and that default is right almost always.

- **Full stack** (the default) → the smaller bundle, because `apt` skips what is already there. It
  depends on those bundles staying on the ISO.
- **Shorter stack** → a self-contained bundle that survives its neighbours being removed.

**The dial is mostly about correctness and removability, not size.** Measured, building
`firefox-esr` two ways on debian-64bit:

| `05-chromium` beneath it | bundle | packages declared |
|---|---|---|
| present | 79,480 KiB | 2 |
| absent | 81,004 KiB | 5 |

1.9% — Firefox's own payload dominates, and the shared runtime it can borrow is small beside it.
Do not choose `from:` for the megabytes.

Choose it for the other two. Name a shorter stack casually and `apt` installs a second copy of
libraries that already exist lower down; because your bundle is higher, *your* copies win — a
version skew nobody asked for, invisible until something links against the wrong one. And a bundle
built against `05-chromium` quietly stops being self-sufficient if someone later removes it.

## One bundle = one unit of removal

The question "should each browser be its own bundle, or should there be a `browsers` bundle?" is
really "what might someone want to remove on its own?"

- Separate bundles if you would ever ship one without the other.
- One bundle if they always travel together — fewer files, one dependency resolution, smaller total.

Grouping by *category* is the wrong instinct. Group by removability.

## Worked example: browsers

### What ships

Stock Slax has exactly one browser, and it is old:

| | |
|---|---|
| `05-chromium` | 79 MiB on Debian, 115 MiB on Slackware |
| version | **117.0.5938.149**, released September 2023, on both flavours |
| what else is in there | `chromium`, `chromium-common`, `chromium-sandbox` **plus 22 libraries** |

Those 22 are the interesting part: `libnss3`, `libnspr4`, `libopus0`, `libvorbis0a`,
`libvorbisenc2`, `libflac12`, `libpulse0`, `libsndfile1`, `libwebpmux3`, `libwoff1`, `libmp3lame0`,
`libmpg123-0`, `libopenh264-7` and friends. **`05-chromium` is not just Chromium — it is the shared
browser runtime.** Anything else with a rendering engine wants most of that list.

So the frequently repeated advice that "nothing depends on `05-chromium`" is true of the stock
image and wrong the moment you add a second browser.

### Removing it

Already a first-class operation — [`remove-bundle`](../50-cookbook/remove-bundle.md) with a
pattern, which is the one recipe that removes anything. Removing from the top of the
stack is consistent by construction: `04-apps`' database correctly does not list Chromium.

Removing from the *middle* of upstream's chain is not. `noload=01-firmware` leaves `02-xorg`'s
database, which still lists the twelve firmware packages it no longer has.

### Replacing it

**Drop, then add.** Remove `05-chromium.sb` and build a new bundle from the remaining stack, so
`apt` pulls the shared runtime back in.

Overriding without removing is the tempting shortcut and it is worse: both copies occupy the ISO
(+79 MiB for nothing), and files the old version had that the new one does not are still there in
the union, because a bundle cannot delete — see below.

### Several at once

Chromium, Firefox ESR and `torbrowser-launcher` are in Debian proper. Brave, Chrome, Edge and
Vivaldi are distributed **only through vendor apt repositories**, which is what `apt.sources` in
[`bundle.packages`](../90-reference/verbs.md) exists for — each with its signing key pinned by
sha256.

Tor Browser is the exception worth knowing before you try: `torbrowser-launcher` is a *downloader*.
The actual browser arrives on first run, over the network, at runtime. It cannot be baked into a
bundle without pre-seeding the user profile, and an offline boot gets a launcher that cannot launch.

Sizes are real and they add up. Chromium alone is 79 MiB compressed and 223 MB installed; Firefox
ESR's `.deb` is 73 MiB before installation. A seven-browser image lands around 1.0–1.2 GB, against
a 416 MiB stock ISO.

### Ceilings nothing else documents

| | |
|---|---|
| a single `.sb` | < 4 GiB — ISO9660's per-file limit |
| the ISO itself | < 4 GiB to be copyable onto a FAT32 stick as a file |
| `toram` | needs RAM at least the size of the ISO |

## What a bundle cannot do

**Delete.** The delta a bundle records is *added or modified* files; a file removed in the build
chroot produces nothing at all. aufs does support deletion — a `.wh.<name>` entry in a higher branch
hides a lower file — but `kitchen` excludes whiteouts deliberately, and the overlayfs fallback
ignores them entirely. A removal that works on one kernel and silently does not on another is worse
than no feature.

To remove something, remove the bundle that contains it.

## Numbering

Load order is the numeric prefix and **higher wins**. `00`–`09` is the platform — upstream's
`01`–`06` plus the recipes here that adjust the OS. **`10`–`89` is yours.** `90`–`97` is headroom,
`98` is the generated package database and `99` is `savechanges`; the last two are refused to
recipes, for reasons that are not about tidiness —
[which numbers are whose](../10-anatomy/bundles-squashfs.md#which-numbers-are-whose).

Ties are not an error and they are not random: `sortmod` sorts on the number and falls back to an
alphabetical compare, so `01-core` loads before `01-firmware`, and `07-branding` before
`07-extras`. Stock Slax relies on this. It is still worth avoiding, because nobody predicts it.

Do **not** renumber survivors to close a gap after removing a bundle. Gaps are fine; renumbering
changes relative priority.
