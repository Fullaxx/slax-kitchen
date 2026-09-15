# Bundles — the `.sb` format

A bundle is a squashfs image holding a fragment of a root filesystem. Slax boots by stacking them in
numeric order. Everything about customizing Slax comes back to two facts: **they are all built with
the same four flags**, and **higher numbers win.**

## The format — one command, four call sites

Every bundle on every one of the four images has identical superblock parameters:

```
v4.0  xz  block=1048576  flags=0x04e0  xz_dict=1048576  bcj=x86
```

```sh
python3 lib/isoparse.py isos/slax-64bit-debian-12.2.0.iso   # reads them in place
```

Flags `0x04e0` decode as `ALWAYS_FRAGMENTS | DUPLICATES | EXPORTABLE | COMPRESSOR_OPTIONS`, which is
what you get from:

```sh
mksquashfs SRC DST -comp xz -b 1024K -Xbcj x86 -always-use-fragments
```

That line appears in `livekitlib`'s `create_bundle`, and in `dir2sb`, `savechanges` and both
flavours' module builders — four call sites, one format, no per-architecture branch. **Bundle tooling
needs no arch handling at all**, which is why `kitchen`'s bundle verbs are the same code for all four
targets.

`-b 1024K` is the flag that matters most for size; `-Xbcj x86` gains a few percent on executables and
is harmless on data. Upstream's own `DOC/bundle.txt` documents the flag as `-bs 1024k`, which does not
exist — see [`15-upstream/doc-verbatim.md`](../15-upstream/doc-verbatim.md).

## Precedence — higher wins

Ordering comes from `sortmod`, which sorts on the **basename's numeric prefix**, not the path:

```sh
sortmod() { cat - | sed -r "s,(.*/(.*)),\\2:\\1," | sort -n | cut -d : -f 2-; }
```

`union_append_bundles` then inserts each bundle at aufs branch index 1 — immediately below the
writable branch, above everything inserted before it. Bundles arrive in ascending numeric order, so
each one outranks the last.

```
branch 0   changes            (writable)
branch 1   05-chromium.sb     ← inserted last, ranks highest
branch 2   04-apps.sb
…
branch 6   01-core.sb         ← inserted first, ranks lowest
```

**So `07-mytools.sb` overrides `01-core.sb`.** It is also why `savechanges` writes `99-changes-N.sb`:
99 beats everything.

Consequences that catch people out:

- **Gaps are fine.** `01`, `03`, `77` sorts exactly as you would expect. There is no requirement to
  renumber.
- **Two bundles can share a number.** `01-core` and `01-firmware` both do; `sort -n` then falls back
  to comparing the rest of the string, so `01-core` precedes `01-firmware` and firmware wins any
  overlap. They do not overlap in practice.
- **A bundle in `/slax/` ranks *below* everything in `/slax/modules/`**, regardless of its number,
  because `mount_bundles` concatenates the top-level listing first. Numbering a top-level bundle `99`
  does not promote it.
- **Under overlayfs the result is the same but the mechanism is not.** `init_union` builds the whole
  `lowerdir` chain with `sortmod | tac`, and `union_append_bundles` has nothing left to do — which is
  why `slax activate` cannot work there.

## Which numbers are whose

**This is slax-kitchen's convention, not upstream's.** Upstream documents no ranges at all:
`vendor/linux-live/DOC/bundle.txt` describes the squashfs format and never mentions a numeric
prefix, `98` appears nowhere in the upstream tree, and `99` exists only as a hard-coded default
path inside `savechanges`. `01`–`06` is an observable fact about six directory names, not a
reservation anyone wrote down. So the table below is ours to keep, and yours to read.

| range | whose | what goes there |
|---|---|---|
| `00`–`09` | upstream and slax-kitchen | **the platform.** Upstream ships `01`–`06`; the recipes here that adjust the OS rather than add to it use `07`–`09` |
| `10`–`89` | **yours** | applications and content. A fork numbers freely in here |
| `90`–`97` | slax-kitchen | headroom, and where [`renumber-bundles`](../50-cookbook/renumber-bundles.md) parks a stock bundle it has moved above everything. `97` is the highest a recipe may take. If you want your own top-of-stack, `80`–`89` is the tidier place for it |
| `98` | generated | `kitchen pack` writes `98-dpkg-db.sb` — **refused to recipes** |
| `99` | `savechanges` | saved sessions — **refused to recipes** |

`00`–`89` is advice. Nothing enforces it, nothing needs to: picking a number in the wrong band
changes your load order and nothing else, and a fork that wants a different split is entitled to
one.

**`98` and `99` are different, and are refused.** They are the two numbers where a collision costs
you something other than ordering:

- **`98`** — `kitchen pack` *deletes and rewrites* `98-dpkg-db.sb` on every pack. It has to: a
  generated bundle left in place would become the base on the next pack and freeze the package
  database at whatever the first one produced. A bundle you put at `98-dpkg-db.sb` is destroyed
  with no error; one at `98-anything-else` silently outranks the merged database.
- **`99`** — `savechanges` picks the next session number by taking the **last** file in
  `slax/modules/` and doing arithmetic on its name:

  ```sh
  ix=$(cd $TARGETDIR; ls -1 | sort -V | tail -n 1 | sed -r "s/^99-changes-//" | sed -r "s/[.]sb\$//")
  ix=$(($ix + 1))
  ```

  It assumes that last file is a `99-changes-N.sb`. Put a bundle called `99-mystuff.sb` there and
  it sorts *after* `99-changes-100.sb`, so every fresh boot computes `100` again and **each saved
  session overwrites the one before it**. Put a dot in the name — `99-my.tools.sb` — and the
  arithmetic is a shell syntax error, `ix` comes out empty, and the target becomes
  `99-changes-.sb`. Either way the loss is silent and it is the user's work.

`kitchen apply` therefore refuses `98-` and `99-` from every bundle verb and from
`bundle.renumber`, and says to use `97` instead — which sits above every other bundle and collides
with nothing. The refusal is at apply time rather than in the schema, so there is one
implementation of the rule rather than two that can drift.

One consequence of `98` worth knowing even though it harms nothing: on a stock image the first
saved session is `99-changes-6.sb`, because upstream's arithmetic runs on `05-chromium`. On an
image built here it is `99-changes-99.sb`, because it runs on `98-dpkg-db` instead. It still
increments correctly from there.

### If a shipped recipe's number collides with yours

Every recipe here is an example you are meant to copy and edit. The three application recipes sit
at `10`–`12` — `chromium-current`, `firefox-esr`, `libreoffice` — which is inside *your* range on
purpose, because installing an application is the kind of thing a fork does. Override the number
from your profile rather than editing the recipe:

```yaml
  - name: libreoffice
    vars: {bundle: 40-office}
```

→ [recipes in a fork](../40-workflow/recipes-in-a-fork.md)

## What is in them

| | Debian 12.2.0 | Slackware 15.0.4 |
|---|---|---|
| `01-core` | 122.3 MiB, 18,666 inodes | 118.6 MiB, 24,321 inodes |
| `01-firmware` | 91.1 MiB, 930 | 79.9 MiB, 783 |
| `02-xorg` | 58.9 MiB, 2,192 | 14.5 MiB, 2,454 |
| `03-desktop` | 37.7 MiB, 7,887 | 19.4 MiB, 7,989 |
| `04-apps` | 4.2 MiB, 1,791 | 4.4 MiB, 1,342 |
| `05-chromium` | 79.1 MiB, 277 | 114.6 MiB, 331 |
| `06-devel` | — | 80.9 MiB, 25,732 |

`05-chromium` has only ~300 inodes for ~100 MiB because Chromium ships as a handful of very large
files. `06-devel` is Slackware-only and carries gcc 13.2.0.

Read any of them in place, without extracting the ISO:

```sh
unsquashfs -o 137216 -ll isos/slax-64bit-debian-12.2.0.iso | head
```

`01-core.sb` always starts at LBA 67, i.e. byte 137,216, on every image.

## The flavour asymmetry — the biggest single difference

The two builds produce structurally different bundles, and this is the thing to internalise before
writing anything that modifies one.

```
Debian    01-core:  bin etc home lib lib64 libx32 opt root sbin srv usr var
Slackware 01-core:  bin boot dev etc home lib lib64 media mnt opt proc root
                    run sbin srv sys tmp usr var
```

**Debian's bundles contain no runtime directories at all.** No `/dev`, `/proc`, `/sys`, `/tmp`,
`/run`, `/boot`, `/media`, `/mnt`. Its `bin`, `lib`, `lib64`, `libx32` and `sbin` are merged-usr
symlinks into `usr/`. This follows directly from `MKMOD` in `lib/config`, which lists the directories
to include and thereby excludes everything else:

```sh
MKMOD="bin etc home lib lib64 libx32 opt root sbin srv usr var"
```

**Slackware's `01-core` is a complete FHS tree**, including 7,370 entries under `/dev` — 6,720 block
nodes, 603 character nodes and 47 directories, dated 2002: Slackware's traditional static device
tree. It gets there
because the Slackware build does not use Linux Live Kit's `build` for bundles at all; `modbuild` runs
`installpkg -root`, which creates the full hierarchy.

Three things follow:

1. **Neither bundle is a usable chroot on its own.** Debian's has no `/tmp`, so `apt-get update`
   fails with `Unable to mkstemp`; Slackware's has device nodes but no mounted `/proc`. Anything that
   installs packages into a bundle has to create the missing pieces and then exclude them from the
   result — which is exactly what `lib/apply.py`'s `RUNTIME_DIRS` and `BUNDLE_EXCLUDE` do.
2. **Rebuilding a Slackware bundle requires root or pseudo-file definitions**, because `mksquashfs`
   must reproduce 7,323 device nodes. Rebuilding a Debian bundle does not.
3. **The runtime directories are created at boot instead**, by `change_root`:
   `mkdir -p boot dev proc sys tmp media mnt run`, `chmod 1777 tmp`, plus `mknod` for `console`,
   `tty`, `tty0`, `tty1`, `null` where missing. That is the loop closed: the build omits them because
   the boot creates them.

## In-bundle hooks

`DOC/bundle.txt` reserves four paths that Slax acts on when a bundle is activated:

| path | |
|---|---|
| `/run/requires` | other bundles this one needs |
| `/run/activate.sh` | run on activation |
| `/run/deactivate.sh` | run on deactivation |
| `/run/startcmd.sh` | the command the launcher runs |

**No shipped bundle on any of the four images uses any of them** — the only `/run` content anywhere
is Slackware's `run/lock/pkgtools/ldconfig.lock`. The mechanism exists and is documented, but it is
untested by the official builds, so treat it accordingly.

## Modifying bundles

Three routes, cheapest first:

| | cost | |
|---|---|---|
| `rootcopy/` | none | files copied into the writable branch at boot. No squashfs work at all. [`rootcopy-overlay`](../50-cookbook/rootcopy-overlay.md) |
| new high-numbered bundle | one `mksquashfs` | overrides without touching anything shipped. [`add-packages`](../50-cookbook/add-packages.md) |
| edit a shipped bundle | unpack + repack, ~100 MiB | only when you must remove something. [`remove-bundle`](../50-cookbook/remove-bundle.md) |

Prefer the first that works. Adding a bundle never risks the shipped ones, and it is reversible by
deleting one file.

Removing a bundle is the one case where deletion beats override: dropping `05-chromium.sb` saves
79 MiB on Debian and 115 MiB on Slackware, and no amount of overriding reclaims that space. See
[`remove-chromium`](../50-cookbook/remove-chromium.md).
