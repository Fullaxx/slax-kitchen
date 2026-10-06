# Verb reference

A recipe step names a **verb** and its fields. **23 of 26** declared verbs are implemented; a recipe
using an unimplemented one fails with a message naming what *is* available.

Every verb is validated against `schema/recipe.schema.json` before anything runs, and
`kitchen apply` [preflights](../40-workflow/toolchain.md#preflight) the whole plan — tools,
files and capabilities — before executing the first step.

| privilege | meaning |
|---|---|
| ○ | runs anywhere |
| ◐ `mknod` | needs `CAP_MKNOD` — the initramfs holds seven device nodes |
| ◐ `chroot` | needs `CAP_SYS_CHROOT` + `CAP_MKNOD` |

## Paths stay inside the tree

Every `dest:` (and `initramfs.patch`'s `file:`) is resolved relative to the root of the tree that
verb owns — the ISO tree, the rootcopy directory, the initramfs, or the staging root of a bundle —
and a path that resolves outside it is **refused**, not clamped:

```
rootcopy.files: dest '../../../../ESCAPED.txt' resolves outside the tree it belongs to
(/home/you/ESCAPED.txt). Paths are relative to the root of that tree; '..' is refused.
```

Symlinks are resolved before the check, so a bundle shipping `etc -> /etc` cannot turn a later
`dest: etc/passwd` into a write to the host. This matters because recipes are meant to be *shared*:
a ○ verb should not be able to touch anything outside the work tree, and that is now enforced rather
than assumed. It is not a sandbox — `bundle.script` and `bundle.packages` run arbitrary code by
design, which is why they are marked ◐ `chroot`.

## Saying where the source is

Verbs record what they fetched, built and copied in, in the image's provenance, and
[`kitchen sources`](cli.md#sources-iso---json-f---markdown-f) turns that into a report of where each
part came from and where its source lives. What the engine cannot work out for itself, a recipe may
state. Nothing requires it, and nothing refuses a recipe that leaves it out
([#62](https://github.com/Fullaxx/slax-kitchen/issues/62)):

```yaml
- verb: bundle.fromTarball
  bundle: 15-app
  src: https://example.org/app-1.0-linux-x86_64.tar.xz
  sha256: "…"                                         # optional; checked when given
  upstream_source: https://example.org/app/source/    # where its publisher keeps the source
```

| Field | On | What it says |
|---|---|---|
| `upstream_source` | `boot.payload`, `bundle.fromTarball`, `bundle.script`, each `apt.sources` entry, each `bundle.files` `url:` or `src:` entry | where the publisher of something installed **unmodified** keeps its source: a URL, or a list of them |
| `sha256` | `boot.payload`, `bundle.fromTarball`, each `bundle.files` `url:` entry | the download's sha256, pinned when the recipe is written. The engine refuses a download that does not match; without one, what it fetched is whatever that server served that day, and the report marks it |
| `declares` | `bundle.script` | binaries the script **compiled**, each with `path`, `source_url`, `source_sha256` and optionally `license` |

A pointer nobody gave reads "not named" in `SOURCES.md`. Neither that nor an unpinned download is an
error.

**A local `src:` is copied in as it is.** Files a verb copies in from beside the recipe go through
one resolver, which records where they sit in the kitchen or project checkout, so the report can
name them. Nothing is hashed and nothing is held to a commit: a file the project's build staged
where git ignores it is as good an input as a committed one. A local archive `bundle.fromTarball`
unpacks goes through the same resolver, and is named as the bundle's source by its file name. The
report's "no sha256 was pinned" is said of a download only, so it is not said of a local archive
([#75](https://github.com/Fullaxx/slax-kitchen/issues/75)).

### A file the build downloads

A file the build downloads goes through a verb that records the download:
[`bundle.fromTarball`](#bundlefromtarball-), given the archive's URL, for a tar or a zip, and a
[`bundle.files`](#bundlefiles-) `url:` entry for a single file — an installer, a font, a data file,
a static binary:

```yaml
- verb: bundle.files
  bundle: 30-installer
  files:
    - dest: /opt/tool/setup.exe
      url: https://example.org/tool/releases/tool-1.0-setup.exe
      sha256: "…"                                    # optional: pinned when the recipe is written
      upstream_source: https://example.org/tool/source/
```

The engine downloads the file, refuses it if the recipe pins a sha256 and the file does not match,
and `kitchen sources` lists it as a prebuilt part of the bundle: a download installed unmodified,
pointing at its `upstream_source`. That needs no chroot and nothing in the image — the step is
`privilege: none` — and [`bundle-from-url`](../50-cookbook/bundle-from-url.md) is a recipe that does
exactly this (#59).

A file the project's own build fetches or installs — an installer its build script downloads, a
Flatpak tree installed on the build host because the chroot cannot run Flatpak — is staged beside
the recipe, where git ignores it, and a `bundle.files` `src:` entry copies it in. Give the entry an
`upstream_source:` and the report lists what it copied in as a prebuilt part pointing there
([#60](https://github.com/Fullaxx/slax-kitchen/issues/60)):

```yaml
- verb: bundle.files
  bundle: 30-bottles
  files:
    - dest: /var/lib/flatpak
      src: ./bottles.files/var/lib/flatpak          # staged by the project's build script
      upstream_source: https://github.com/flathub/com.usebottles.bottles
```

A download that is part of work a script has to do anyway goes through
[`bundle.script`](#bundlescript--chroot). The script downloads the file and prints a
`KITCHEN-FETCHED` line for it, and `kitchen sources` lists the file as a prebuilt part of the
script's bundle, pointing at the step's `upstream_source`:

```yaml
- verb: bundle.script
  bundle: 30-installer
  network: true
  upstream_source: https://example.org/tool/source/
  script: |
    set -e
    url=https://example.org/tool/releases/tool-1.0-setup.exe
    want="…"    # the file's sha256, pinned when the recipe is written
    mkdir -p /opt/tool
    wget -nv -O /opt/tool/setup.exe "$url"
    got=$(sha256sum /opt/tool/setup.exe | cut -d' ' -f1)
    [ "$got" = "$want" ] || { echo "sha256 mismatch: $got" >&2; exit 1; }
    echo "KITCHEN-FETCHED $want opt/tool/setup.exe $url"
```

The line is recorded as the script printed it — the file's sha256, its path in the image and its
URL — and nothing checks it: the script's own check, above, is what holds the file to its pin, and
where the bytes came from is the script's word. The route costs what `bundle.script` costs:
`privilege: chroot`, network inside the chroot, and a downloader in the image. Stock Slax has `wget`
on all four targets. On Slackware it verifies no TLS certificate until `/etc/ssl/cert.pem` exists,
which `fix-slackware-bugs` writes ([issue 13](../30-inventory/known-upstream-bugs.md)).

Committing a payload instead is what `00-no-binaries` refuses, for a Windows payload or a large
file: it keeps them out of this repository's history, not out of images.

---

## Bundle

All seven produce or modify `slax/modules/*.sb`. **Load order is the numeric prefix and higher
wins**, so a bundle name must start with `NN-`; the verbs refuse one that does not, because a
bundle without a prefix has no defined position in the stack.

They also refuse **`98-`** and **`99-`**, including as `bundle.renumber`'s `to:`. Those two are the
only numbers where a collision costs something other than ordering: `kitchen pack` deletes and
rewrites `98-dpkg-db.sb` on every pack, and `savechanges` derives the next session number from the
last file in `slax/modules/` — a bundle sitting there makes every saved session overwrite the one
before it. Use `97`, which is above every other bundle and collides with nothing.
→ [which numbers are whose](../10-anatomy/bundles-squashfs.md#which-numbers-are-whose)

Every other number is convention only: `00`–`09` platform, `10`–`89` a fork's, `90`–`97` headroom.
Nothing enforces those, because getting one wrong changes your load order and nothing else.

Every bundle is built with upstream's exact `mksquashfs` line, so a built bundle is
format-indistinguishable from a shipped one (superblock flags `0x04e0`, 1 MiB blocks):

```sh
mksquashfs SRC DST -comp xz -b 1024K -Xbcj x86 -always-use-fragments -noappend
```

### `bundle.files` ○

Build a bundle from files given inline, by path, or by URL.

```yaml
- verb: bundle.files
  bundle: 07-branding
  files:
    - {dest: /etc/hostname, content: "myhost\n"}
    - {dest: /usr/bin/tool, src: ./tool, mode: "0755"}
    - {dest: /opt/data, src: ./datadir}        # a directory, copied with its links
    - dest: /usr/local/bin/jq                   # downloaded, and held to a pinned sha256
      url: https://example.org/jq/jq-linux-amd64
      sha256: "…"
      upstream_source: https://example.org/jq/source/
      mode: "0755"
```

[`rootcopy.files`](#rootcopyfiles-)' shape plus `url:`, but the result is a real bundle. Worth the
difference when you want the files to be one movable file, to be skippable with `noload=`, or to sit
at a defined point in the stack — rootcopy always lands in the writable layer and cannot be turned
off at the boot prompt.

Each entry takes exactly one of `content`, `src` or `url`, and the entries are schema-closed, so a
misspelt key is refused. A `url:` entry may give `sha256:`, and the engine refuses a download that
does not match it; either way it records the file, so `kitchen sources` lists it as a prebuilt part
of the bundle ([a file the build downloads](#a-file-the-build-downloads)). A `url:` or a `src:` entry
may give `upstream_source:`, which is where the report points for it. A download's mode is `0644`
unless the entry gives one. Downloads are placed after the other entries, and one that lands on a path another
entry wrote is refused. There is no mirror list and no cache: every build fetches, and a dry run
fetches nothing.

A `src:` directory is copied whole. Its symlinks stay symlinks, and a file with several names in
it is copied once and linked under the others, so a staged Flatpak, whose ostree objects are
hardlinks to the deployed files, costs what the stage does
([#64](https://github.com/Fullaxx/slax-kitchen/issues/64)). Links are kept within one entry, not
across entries. A `src:` or `content:` entry that lands on a file an earlier entry placed
replaces it, and one that would reach outside the bundle through a symlink an earlier entry
placed is refused.

### `bundle.fromDir` ○

Pack a directory as a filesystem root: `./usr/bin/foo` becomes `/usr/bin/foo` in the union.

```yaml
- verb: bundle.fromDir
  bundle: 07-mytools
  src: ./mytools            # relative to the recipe file
```

The offline equivalent of upstream's `dir2sb`.

### `bundle.fromTarball` ○

```yaml
- verb: bundle.fromTarball
  bundle: 07-myapp
  src: https://example.com/myapp-1.0.tar.gz
  sha256: "…"               # optional; checked when given
  strip: 1                  # drop the leading myapp-1.0/ directory
  prefix: /opt/myapp        # place under a subdirectory instead of the root
```

`src:` is a URL, or a path beside the recipe that is recorded as
[any local `src:`](#saying-where-the-source-is) is. It names a tar, plain or compressed with gzip,
bzip2 or xz, or a zip, and the verb tells them apart by content, not by name. A zip is held to
everything below, member by member. Its modes and symlinks come from the Unix attributes a zip made
on a Unix system carries, an entry made without them is `0644` (`0755` for a directory), and an
encrypted zip is refused ([#69](https://github.com/Fullaxx/slax-kitchen/issues/69)). `prefix:` is
held inside the bundle like any path a recipe names.

Absolute paths and `..` components in the archive are **refused**, not sanitised — and so
are link *targets*. A member named `x` that is a symlink to `/etc`, followed by a member
`x/cron.d/kitchen`, would otherwise write through it to the host. Symlinks and hardlinks are
both checked, after `strip:` has been applied, because stripping changes how deep a member
sits and therefore where a relative target lands. Containment is then re-checked against the
filesystem before each member is written, because a member can be a symlink that an *earlier*
member has already made point somewhere else — a purely textual check cannot see that.

The verb also **refuses setuid/setgid members, device nodes and FIFOs**. It is a ○ verb whose
output is mounted as root at every boot, and `sha256:` is optional, so otherwise an unpinned
archive's publisher would be choosing what runs privileged on your image. Use `bundle.script`
(◐ `chroot`) if a bundle genuinely needs one.

### `bundle.remove` ○

```yaml
- verb: bundle.remove
  match: "^05-chromium"     # regex over the filename
```

The only case where deleting beats overriding: a whiteout hides a file but does not reclaim its
space. See [remove-bundle](../50-cookbook/remove-bundle.md), which is the recipe that does this.

**A recipe that removes or renumbers a bundle may contain nothing else**, and `kitchen validate`
refuses one that does. A removal decides where its recipe may sit in a plan, so mixing it into a
recipe that also builds makes that recipe's position a constraint on every other one.

**This must run before every `bundle.packages` and `bundle.script` in the plan**, across all
recipes, and the run is refused otherwise. A bundle built against the default `from:` stack takes
everything below it as given; removing one of those afterwards leaves an unresolvable `NEEDED`
that no gate can see. See [composing-bundles](../40-workflow/composing-bundles.md).

### `bundle.renumber` ○

Change a bundle's position in the stack without rebuilding it.

```yaml
- verb: bundle.renumber
  match: "^05-chromium"
  to: "95"                  # a single digit is zero-padded: 5 -> 05
```

Two bundles may share a prefix — upstream ships `01-core` and `01-firmware` — in which case
`sort -n` falls back to comparing the rest of the name. An exact filename collision is refused.

### `bundle.packages` ◐ chroot

Install distro packages into a new bundle. **Debian only**; see
[add-packages](../50-cookbook/add-packages.md) for why Slackware is deferred.

```yaml
- verb: bundle.packages
  bundle: 07-extras
  packages: [tmux, ncdu]
  apt: {update: true, no_recommends: true}
```

**apt runs with `--no-remove`, and there is no way to turn that off.** If satisfying your
`packages:` list would mean removing something, the build stops before anything is unpacked. The
reasoning, the three ways out, and what evidence would justify an opt-out are in
[composing bundles](../40-workflow/composing-bundles.md#apt-wanted-to-remove-a-package).

**`apt.no_recommends` defaults to `true`**, which is right for a bundle — it is what stops one
package dragging in a desktop's worth of suggestions. The cost is that it also drops packages the
thing you asked for genuinely needs to be *usable*, and nothing warns you. Check the Recommends of
what you install and name the ones that matter.

Two real examples, both from [`libreoffice`](../50-cookbook/libreoffice.md): without
`libreoffice-gtk3` the suite runs but draws its own widgets and has no native file dialog; and
`libreoffice-base` installs happily without the JRE it needs, so it starts and then cannot open a
database. An application that looks installed and fails when clicked is worse than one you left out.

**`apt.reinstall` defaults to `false`.** Set it for packages the stock image already has at the same
version: `apt-get install` of those does nothing, and the stock copy has lost its `/usr/share/doc`,
because Slax's build deletes it. With `reinstall: true` apt unpacks them again; files that come back
unchanged in size, mtime and mode stay out of the bundle, so what ships is what differs — the
copyright files. Measured on ten stock firmware packages: a 64 KiB bundle of 32 files. See
[`firmware-refresh`](../50-cookbook/firmware-refresh.md).

**`from:` defaults to every bundle that will sit below this one**, which is almost always what
you want. Name a shorter stack and apt reinstalls libraries the image already has, and those
copies then shadow the originals from a higher bundle. It is a size-versus-independence dial:
a shorter stack gives a larger, self-contained bundle that survives its neighbours being
removed. See [composing bundles](../40-workflow/composing-bundles.md).

The bundle carries **no `var/lib/dpkg/status`** — Debian's database is a single file and a
union composes trees, not files. It ships a fragment instead, and `kitchen pack` merges them.

Foreign architectures and third-party repositories, for the packages Debian does not carry:

```yaml
- verb: bundle.packages
  bundle: 12-brave
  packages: [brave-browser]
  apt:
    architectures: [i386]        # dpkg --add-architecture, before the index refresh
    sources:
      - name: brave
        uri: https://brave-browser-apt-release.s3.brave.com/
        suite: stable
        components: [main]
        key_url: https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg
        key_sha256: "<64 hex>"   # required whenever key_url is given
        keep: false              # default: the repo and key do NOT ship
        upstream_source: https://github.com/brave/brave-browser   # where its source is
```

Each package's archive is recorded by matching its `.deb`'s sha256 against apt's own indexes, so
`kitchen sources` points a package from this repository at its `upstream_source`, and a package from
Debian at `snapshot.debian.org`. A package from an archive the step does not declare has nothing to
point at, and the report says so.

A key is **pinned by sha256**, and unlike other downloads here the pin is required: an unpinned
key lets a remote party decide what your image trusts, and a bundle is where that becomes
permanent. The armour format is detected from the content, because apt reads it from the *extension* under
`signed-by=` and reports `NO_PUBKEY` for a key it is holding if the two disagree.

`keep:` governs **only the two files kitchen writes** — `etc/apt/sources.list.d/<name>.list` and
`usr/share/keyrings/<name>-archive-keyring.{asc,gpg}`. `false` excludes them from the bundle once
they have done their job in the chroot; `true` ships them, so the booted system trusts that
repository through `signed-by=` and can upgrade from it. `/var/lib/dpkg/arch` always ships, so a
foreign architecture survives into the image.

> **`keep: false` does not mean the image will not trust the repository.** A package's own
> postinst output is ordinary dpkg output, and no exclusion pattern can reach it — so a package
> that installs its own source list and key ships them regardless of this setting, and
> `apt update && apt upgrade` may work against that vendor either way. Measured on all four of
> Brave, Chrome, Edge and Vivaldi, one of which additionally symlinks its key into
> `/etc/apt/trusted.gpg.d/`, where it is trusted for *every* source. See
> [`all-browsers`](../50-cookbook/all-browsers.md) for the worked case. Where `keep:` is genuinely
> decisive is a repository whose package does *not* configure itself — a private mirror, or
> backports.

> **Every step is schema-closed.** `$defs/step` uses `unevaluatedProperties: false`, so an
> unknown key is a validation error rather than something silently ignored — `frm:` for `from:`,
> `strpi:` for `strip:`, a field that only ever existed in a docstring. Note that
> `additionalProperties` would *not* work here: it only sees `properties` declared in the same
> schema object, not the ones an `if`/`then` branch introduces.
>
> That covers a step's own keys. An object nested inside a step is closed only where its own
> schema says so: `boot.menu`'s `add:` and `bundle.files`' entries are. The entries of
> `rootcopy.files`, `iso.files` and `initramfs.files`, `initramfs.patch`'s edits, and
> `boot.branding`'s `{src: …}` form are not yet, so a misspelt key inside one of those still passes
> validation.

### `bundle.script` ◐ chroot

`bundle.packages` generalised: run any script in a chroot of stacked bundles and package what
changed. For the things a package manager cannot do — compile something, generate keys, run a vendor
installer.

```yaml
- verb: bundle.script
  bundle: 07-generated
  network: true          # declare it if the script fetches anything
  script: |
    #!/bin/sh
    set -e
    ssh-keygen -A
```

Four behaviours it shares with `bundle.packages`, all load-bearing:

- the delta is **added or modified** files, never names alone. A filename-only diff misses the
  package database, and a bundle without it leaves new binaries invisible to dpkg.
- `BUNDLE_EXCLUDE` strips the runtime directories the chroot needed but a bundle must not ship,
  plus caches and lockfiles — including `var/lib/dpkg/status`, which is replaced by a fragment.
- the delta is staged **as the chroot had it**: owners, modes with setuid and setgid, symlinks, and
  a file the delta holds under several names as one file, the way dpkg installs perl
  ([#78](https://github.com/Fullaxx/slax-kitchen/issues/78)).
- the chroot's temporary directory is **its own `/tmp`**. `TMPDIR` is set to it, and the host's
  `TMP` and `TEMP` are left out. A host `TMPDIR` can name a directory the image does not have, and
  a script's `mktemp` or a package's maintainer script failed under one
  ([#88](https://github.com/Fullaxx/slax-kitchen/issues/88)). `BUNDLE_EXCLUDE` keeps `tmp/` out of
  the bundle.

**A script that downloads something says so.** The engine cannot see what a script fetched, so a
line on stdout of the form

```
KITCHEN-FETCHED <sha256> <path in the image> <url>
```

is recorded in the image's provenance as the script printed it, and left out of the output shown.
Nothing checks it against the bundle ([#62](https://github.com/Fullaxx/slax-kitchen/issues/62)).
`kitchen sources` lists each such file as a prebuilt part of the bundle. `firmware-refresh` prints
one per linux-firmware file, and [a file the build downloads](#a-file-the-build-downloads) shows the
shape for one.

`upstream_source:` says where what the script fetched is published, and `declares:` names what it
compiled; see [saying where the source is](#saying-where-the-source-is). A package the script
installs from a repository it added itself points at the step's `upstream_source`.

`network: true` **declares** that the step reaches the internet; preflight then checks for one
before any of the plan runs, rather than after unsquashing 122 MiB. It does not sandbox anything
and does not claim to: isolating the chroot's network needs a user namespace, which not every
environment this runs in has.

---

## Rootcopy

### `rootcopy.files` ○

Files copied into the writable layer at boot by `copy_rootcopy_content`, before anything starts.
**The cheapest customization there is** — no squashfs work at all.

```yaml
- verb: rootcopy.files
  files:
    - {dest: /root/.bashrc, src: ./bashrc}
    - {dest: /etc/skel, src: ./skel}             # a directory, copied with its links
```

Each entry takes `content`, or a `src:` path beside the recipe, since this verb downloads nothing.
It is placed as a [`bundle.files`](#bundlefiles-) entry is: a `src:` directory is copied with its
symlinks and hardlinks, a later entry replaces a file an earlier one placed rather than writing
through it, and `mode:` may not set setuid or setgid. Pack records every file in the image as
root's, and livekit copies rootcopy with `cp -a`, so `mode: "4755"` would make a setuid-root binary;
`bundle.script` is the route if one is really needed
([#77](https://github.com/Fullaxx/slax-kitchen/issues/77)). Every file an entry places is journalled
under its own path, so `kitchen status` lists it and `kitchen sources` names the recipe for it.

### `rootcopy.preinit` ○

Installs `/slax/rootcopy/run/preinit.sh`, which `livekitlib` **sources** (`. "$SRC" "$2"`) just
before `change_root`, with the assembled union as `$1`. The last point at which you can touch the
filesystem the system is about to boot into. An `exit` here ends the boot.

---

## Boot

### `boot.menu` ○ · `boot.cmdline` ○

```yaml
- verb: boot.menu
  targets: [isolinux.cfg, syslinux.cfg]     # default: both
  add: {label: mine, menu_label: "…", kernel: /slax/boot/vmlinuz, append: "…"}

- verb: boot.menu
  add: {label: quiet, menu_label: "…", from: default, append: "quiet"}   # a copy of `default`

- verb: boot.cmdline
  append: ["noload=05-chromium"]            # edits existing APPEND lines
  remove: ["automount"]                     # by key: drops `automount` and `automount=x`
  labels: [default]                         # optional: restrict to these LABELs
```

`append` and `remove` are **arrays**, and the step reports how many APPEND lines it actually
changed — re-applying it says `0 entries`, not the entry count. Each entry is split on whitespace,
as the APPEND line itself is, so a recipe var can carry several parameters in one string, and an
empty one carries none: that step then changes nothing. A step with neither field is refused
rather than silently doing nothing.

**`from:` copies an entry the menu already has, as it has it by then.** An entry written out in full
carries none of the edits made before it: `serial-console` used to spell out stock Slax's command
line, so after `boot-cmdline` its entry still had `automount`, and on an image whose own build had
removed it, the entry put it back (#50). With `from: <label>`, each target file's `KERNEL` or
`LINUX`, `COM32`, `INITRD` and `APPEND` are copied from that `LABEL` in the same file — `MENU` lines
are not — and `append:` is then applied by key: every parameter it names is dropped from the copy,
and then all of its own are added at the end. So `append: "console=tty0 console=ttyS0,115200n8"`
replaces whatever `console=` the source had and keeps both of its own. `from:` cannot be given with
`kernel`, `linux`, `com32` or `initrd`, since those are what it copies. A target file with no such
`LABEL`, or whose `LABEL` boots nothing (a `MENU DISABLED` entry), is refused; so is a copied kernel
that is not in the tree. Every target is read and every refusal made before anything is written, so
a refusal leaves both files as they were.

**Use `LINUX`, not `KERNEL`, for a non-bzImage payload** — see
[edit-bootloader](../40-workflow/edit-bootloader.md).

**The payload an entry names must exist, or be on its way.** `kernel`, `linux`, `com32` and `initrd`
are resolved against the work tree, and an entry naming a file that is neither there nor installed by
a step of the same recipe that *will run* is refused — the entry would be written into the bootloader
and boot nothing. "will run" is what makes `--dry-run` work: `boot.payload` installs nothing under a
dry run, so existence alone would refuse a plan that is perfectly fine. Only absolute paths are
checked; isolinux also accepts one relative to the config it appears in, which cannot be resolved
before the entry is placed. An entry copied with `from:` is checked after copying, once per file.
`append` is a kernel command line and is not inspected, so an `initrd=` inside it is not checked.
Paths are compared after normalising, so `/slax/boot/x` and `//slax/boot/./x` are the same file
rather than a refusal.

This is what a `when:`-guarded payload with an unguarded menu step used to produce: both guarded
steps skipped, the entry written anyway, `kitchen apply` exit 0.

### `boot.payload` ○

```yaml
- verb: boot.payload
  src: https://…/mt86plus_7.20_64.bin
  sha256: "…"
  extract: memtest.bin      # optional: pull one member from a .zip/.tar.*
  dest: slax/boot/memtest.bin
```

Archive type is detected from **content magic**, not the filename.

### `boot.branding` ○

Splash, help text, menu timeout, default entry.

```yaml
- verb: boot.branding
  bootlogo: ./splash.png       # menu background
  helpbg: ./helpbg.png         # behind the F1 screen
  help: "…"                    # or {src: ./help.txt}
  timeout: 10                  # SECONDS; upstream stores tenths, the verb converts
  default: toram               # must be an existing LABEL
```

The stock `TIMEOUT 40` is four seconds, not forty. `default:` refuses a label that does not exist
rather than producing a menu with no working default.

### `boot.grub` ○

Emit a GRUB snippet for chainloading this Slax from an **existing host bootloader** — distinct from
`boot.uefi`, which builds GRUB into the ISO's own ESP.

```yaml
- verb: boot.grub
  from: syslinux.cfg
  probe: /slax/boot/vmlinuz    # what `search --file` looks for
  dest: slax/boot/grub-snippet.cfg
```

Mirrors the real menu entries, moves `initrd=` off the kernel command line to a separate `initrd`
command as GRUB requires, and validates the result with `grub-script-check` before writing it.

### `boot.uefi` ○ · `boot.isohybrid` ○

Make the ISO bootable on UEFI, and `dd`-able to a stick. Both fix real gaps in every stock image —
see [the UEFI gap](../20-boot-sequence/uefi-cd-gap.md). **Apply `boot.uefi` last**: it generates the
GRUB menu from `isolinux.cfg`, so it mirrors whatever other recipes added.

---

## initramfs

All three need `CAP_MKNOD` and repack with `--check=crc32`, which the kernel's xz decoder requires.

### `initramfs.files` ◐ mknod

```yaml
- verb: initramfs.files
  files:
    - {dest: /bin/mytool, src: ./mytool, mode: "0755"}
```

Must be **static** — there is no dynamic loader. i386 works on all four targets; x86-64 on two. Both
are reported, not refused. Refuses to overwrite `blkid` or `eject` without `force: true`.

### `initramfs.modules` ◐ mknod

```yaml
- verb: initramfs.modules
  from_bundle: 01-core      # promote from a bundle; no external file needed
  subdir: kernel/extra
  modules: [nbd]
```

The initramfs carries 301 modules against 4,766 in `01-core.sb`. `dm-mod`, `md-mod`, `raid*` and
`virtio_*` are compiled into the kernel and are not promotable.

### `initramfs.patch` ◐ mknod

```yaml
- verb: initramfs.patch
  edits:
    - file: /init
      expect_sha256: "…"    # optional; pins the patch to a known build
      find: 'find_data 45 "$DATAMNT"'
      replace: 'find_data 90 "$DATAMNT"'
      count: 1              # default
```

Exact strings only — no diffs, no regex. Refuses on hash mismatch, absent string, wrong count, or a
result that fails `sh -n`. See [initramfs-boot-timeout](../50-cookbook/initramfs-boot-timeout.md).

---

## ISO

### `initramfs.busybox` ◐ mknod

Replace the initramfs busybox and regenerate its applet symlinks.

```yaml
- verb: initramfs.busybox
  src: ../../build/busybox-1.37.0-i386-static
```

A verb rather than an `initramfs.files` entry because swapping the binary alone leaves 245 symlinks
describing an applet set that no longer matches it. It regenerates them from `busybox --list` (not
upstream's usage-text scraping), never overwrites a real file — which is what keeps the standalone
`blkid` and `eject` winning — removes `bin/init` so the `init` applet cannot shadow the `/init`
script, and **deletes symlinks for applets the new build no longer has**.

Refuses to finish if `blkid` or `eject` has stopped being a real binary. Build the input with
`tools/build-busybox.sh`; see [the cookbook page](../50-cookbook/initramfs-busybox.md).

### `iso.files` ○

Place a file anywhere in the ISO tree, outside `/slax/`. This is the verb for things a
user sees when they mount the disc rather than boot it — a README, a licence, a folder of
documents.

Each entry needs `dest` plus exactly one source: `content` for inline text, or `src` for a
path on disk, resolved relative to the recipe's own directory unless it is absolute.

```yaml
- verb: iso.files
  files:
    - {dest: /README.txt, content: "…"}
    - {dest: /LICENSE, src: files/gpl-2.0.txt}
    - {dest: /autorun.sh, content: "#!/bin/sh\n", mode: "0755"}
    - {dest: /docs, src: files/manual}
```

`dest` is resolved inside the ISO tree and `..` is refused, symlinks included — a recipe is
meant to be shared, so a `dest` that walks out to the host is not the author's to choose.

Entries are placed as [`bundle.files`](#bundlefiles-) entries are
([#82](https://github.com/Fullaxx/slax-kitchen/issues/82)):
- a `src:` **directory** keeps its symlinks as symlinks, rather than copying in whatever they point
  at on the build machine. A file with several names in it stays one file, and the tree keeps the
  modes it had.
- `mode`, given for a directory, applies to the directory itself.
- a later entry replaces a file an earlier one placed, rather than writing through it.
- `mode:` may not set setuid or setgid. Pack records every file as root's, and livekit mounts the
  medium without `nosuid`, so `mode: "4755"` would make a setuid-root file on the boot medium.

Every file an entry places is journalled under its own path, so `kitchen status` lists it and
`kitchen sources` names the recipe for it. Under `--dry-run` nothing is written and nothing is
created.

### `iso.metadata` ○

Set the ISO9660 volume descriptor fields. Upstream leaves publisher, preparer, volume set,
copyright, abstract and bibliography **all blank**.

```yaml
- verb: iso.metadata
  volid: SLAX-CUSTOM        # 32 chars; the label you see when it is mounted
  appid: "…"                # 128
  sysid: "…"                # 32
  publisher: "…"            # 128
  preparer: "…"             # 128
```

Recorded as **pack hints** — these are set by the mastering tool, so there is nowhere in the tree
they could live. An over-long value is refused rather than silently truncated, and a flag on the
`kitchen pack` command line wins over the recipe. An empty value leaves that field as it is, so a
recipe's empty default never erases what an earlier recipe set.

### `iso.checksums` ○

```yaml
- verb: iso.checksums
  algorithm: sha256         # or sha512
  sign: "your-key-id"       # optional; gpg --detach-sign on the checksum file
```

Also a pack hint, and necessarily so: the checksum of an image cannot live inside that image.
`kitchen pack` writes `<output>.sha256` beside the ISO with a **relative** filename inside, so
`sha256sum -c` works from the output directory.

---

## Not implemented

`kernel.replace` — the last one, and the heaviest: both of its failure modes (no aufs, no
`CONFIG_IA32_EMULATION`) are silent.

## Won't do

**`initramfs.config`** — only `LIVEKITNAME` and `BEXT` are read at runtime, and `LIVEKITNAME` is
merely the default for `from=`, so a second Live Kit tree needs no rename.

**`boot.secureboot`** — the kernel is unsigned and custom-built, so the missing link needs a signing
key we cannot ship, enrolled per-machine by a physically present human, on a path we cannot
boot-test. Documented instead: [secure-boot](../20-boot-sequence/secure-boot.md).

Both with full reasoning in [status](../00-overview/status.md#rejected-with-reasons).
