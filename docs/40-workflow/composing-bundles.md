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

## "apt wanted to remove a package"

If a build stopped with that, this section is why, and what to do. It is written for whoever hits
it, which will not be the person who decided it.

### What happened

`apt-get install` decided it had to remove something to satisfy what your recipe asked for — a
`Conflicts:`, or a package superseded by one you named. `bundle.packages` passes `--no-remove`, so
apt aborted **before unpacking anything**. Your chroot is untouched and no bundle was written.

### Why it refuses, when apt would have coped

Because a bundle cannot delete, and the rule above has a consequence that is easy to miss: the
removal would happen in the *build chroot*, and the files live in the *bundle below*, which the
build never touches. They are still there at boot. What changes is only the database:

| you would run, on the built image | you would get |
|---|---|
| `dpkg -s X` | **not installed** — the fragment carries `deinstall ok config-files`, and the pack-time merge replaces the base's entry with it |
| `dpkg -L X` | the full file list, read from the lower bundle's `.list`, because `var/lib/dpkg/info/` is a directory and unions correctly |
| any file of X | **present, and it runs** |

Three consequences follow, in rising order of how much they should worry you:

1. **A hybrid nobody shipped.** Where X and the package that replaced it collide, the higher bundle
   wins — which is the outcome you wanted. X's *other* files survive. The image carries a mixture
   of two package versions that no maintainer ever tested together.
2. **Tools disagree about reality.** Anything that asks dpkg gets one answer and anything that looks
   at the filesystem gets another.
3. **X is never patched again.** `apt upgrade` on the live system skips a package it believes is
   uninstalled. Its code is present, reachable, and invisible to the updater — indefinitely.

The third is the reason this is a refusal rather than a warning.

### What to do, and when each one stops working

1. **Name different packages.** If the conflict is between two things that do the same job, pick the
   one the image does not already have.
2. **Pin a version that does not conflict.** `pkg=1.2.3-1` in the recipe's `packages:` list.
   Bookworm is frozen, so a version that resolves today will resolve tomorrow.
3. **Remove the bundle that holds the conflicting package**, with `remove-bundle` listed first in
   the profile — the documented answer, and the one the rule above points at.

   ```yaml
   recipes:
     - name: remove-bundle
       vars: {drop: "^05-chromium\\.sb$"}
     - your-recipe
   ```

**Option 3 does not always exist**, and this is the honest limit of the current design: you cannot
remove `01-core`, because it *is* the root filesystem. If the package apt wants to drop lives there,
none of the three options applies, and you have found the case that reopens this decision. See below.

### Why there is no flag to override it

Considered and rejected, so that the next person does not have to re-derive it:

| | why not |
|---|---|
| allow it silently, as before | the three consequences above, none of them visible to the person building the image |
| allow it, but warn | a line in a ten-minute build log, and a green CI run. The image still ships with a database that disagrees with its own files |
| allow it, and keep the package in the database | honest about the files, but the removal the recipe implicitly asked for did not happen, and the version recorded is wrong too, since some of that package's files are now shadowed |
| a per-recipe opt-out | reinstates exactly the problem, on the say-so of whoever was trying to get past an error message |

At the time this was written **nothing in the tree triggered it**: no shipped recipe asks apt to
remove anything, and `all-browsers` resolves a nine-package dependency closure without a single
removal. An opt-out designed against an imagined case would have been designed wrong.

### What would change the answer

**A real recipe that needs a package removed and cannot use option 3** — most likely because the
package is in `01-core`. If you are reading this because you hit exactly that, you are holding the
evidence this decision lacked. Please open an issue with the recipe, the package, and the output of
the refusal, rather than adding a flag: the right shape of the escape hatch depends on what your
case actually needs, and nobody has had one yet.

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
