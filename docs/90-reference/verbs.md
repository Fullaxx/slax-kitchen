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

Verbs record what they fetched and built in the image's provenance, and
[`kitchen sources`](cli.md#sources-iso---json-f---markdown-f---fetch-dir---strict) turns that into a list
of where each part's source lives. Three things the engine cannot work out for itself, a recipe
states:

```yaml
- verb: bundle.fromTarball
  bundle: 15-app
  src: https://example.org/app-1.0-linux-x86_64.tar.xz
  sha256: "…"
  upstream_source: https://example.org/app/source/   # where its publisher keeps the source
```

```yaml
# beside metadata: and steps:, for a recipe whose output must not be published
redistribution:
  allowed: false
  why: installs a browser whose licence does not permit redistributing it
```

| Field | On | What it says |
|---|---|---|
| `upstream_source` | `boot.payload`, `bundle.fromTarball`, `bundle.script`, each `apt.sources` entry | where the publisher of something installed **unmodified** keeps its source: a URL, or a list of them |
| `declares` | `bundle.script` | binaries the script **compiled**, each with `path`, `source_url`, `source_sha256` and optionally `license` |
| `redistribution` | the recipe | `allowed: false` and a `why:` when an image containing this recipe's output must not be published |

A download with no `upstream_source` is a warning, and unresolved under `kitchen sources --strict`;
so is a download the recipe pinned no `sha256:` for, because what it fetched is whatever that server
served that day. An ELF file that a `bundle.script` leaves behind that no package **vouches for** —
no package owns it, or its bytes are not the ones the owning package recorded an md5 for — and that
no `declares:` entry names is always unresolved: something was compiled or overwritten, and nothing
says from what. On Slackware, whose package database records no checksums, ownership is all there
is, which is why `declares:` exists.

**A local `src:` needs nothing extra, only a commit.** Files a verb copies in from beside the recipe
go through one resolver, which records their path in the kitchen or project checkout and their
content. `kitchen sources` checks both against the recorded commit, because those files are what
the project source archive is promising to hold. A unit test fails any verb that resolves a
recipe-relative path without it. `bundle.fromTarball` and `initramfs.busybox` are the exceptions:
what they take in is a download or a build output, described by `upstream_source`
or a build claim. `boot.payload` takes either, and is not an exception — a URL is described by
`upstream_source`, and a local file is recorded like any other file copied in from a checkout.

### A file the build downloads

A file the build downloads goes through a verb that records the download:
[`bundle.fromTarball`](#bundlefromtarball-), given the tarball's URL, for a tarball, and
[`bundle.script`](#bundlescript--chroot) for anything else — an installer, a font, a data file. The
script downloads the file, checks it against a sha256 the recipe pins, and prints a
`KITCHEN-FETCHED` line. `kitchen sources` then lists the file with the step's `upstream_source`,
in a bundle classed `ours`:

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
    echo "KITCHEN-FETCHED $got opt/tool/setup.exe $url"
```

The engine records the line as the script prints it, and checks it against nothing: a line naming
the wrong sha256 passes `kitchen sources --strict`. The pin is the script's own check, which is why a
mismatch has to fail the step. The route costs what `bundle.script` costs: `privilege: chroot`,
network inside the chroot, and a downloader in the image. Stock Slax has `wget` on all four
targets. On Slackware it verifies no TLS certificate until `/etc/ssl/cert.pem` exists, which
`fix-slackware-bugs` writes ([issue 13](../30-inventory/known-upstream-bugs.md)).

The obvious shortcut does not get through: a file staged where git ignores it and copied in by
`bundle.files` is an input the recorded commit does not hold, so it is unresolved.
`kitchen sources --allow-dirty` accepts it, and `ci/release-assets.sh` never passes that flag.
Committing the file instead is what `00-no-binaries` exists to refuse, for a Windows payload or a
large file.

A prebuilt ELF binary that no package ships reaches a bundle accounted for only inside a tarball
today. One that a `bundle.script` leaves behind is unresolved unless `declares:` names it, and
`declares:` is for what the script compiled ([#52](https://github.com/Fullaxx/slax-kitchen/issues/52)).

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

Build a bundle from files given inline or by path.

```yaml
- verb: bundle.files
  bundle: 07-branding
  files:
    - {dest: /etc/hostname, content: "myhost\n"}
    - {dest: /usr/bin/tool, src: ./tool, mode: "0755"}
    - {dest: /opt/data, src: ./datadir}        # a directory is copied recursively
```

The same shape as [`rootcopy.files`](#rootcopyfiles-), but the result is a real bundle. Worth the
difference when you want the files to be one movable file, to be skippable with `noload=`, or to sit
at a defined point in the stack — rootcopy always lands in the writable layer and cannot be turned
off at the boot prompt.

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
  sha256: "…"               # warns loudly if omitted
  strip: 1                  # drop the leading myapp-1.0/ directory
  prefix: /opt/myapp        # place under a subdirectory instead of the root
```

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
Debian at `snapshot.debian.org`. A package from an archive the step does not declare is unresolved.

A key is **pinned by sha256**, like every other download here — an unpinned key lets a remote
party decide what your image trusts, and a bundle is where that becomes permanent. The
armour format is detected from the content, because apt reads it from the *extension* under
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

Two behaviours it shares with `bundle.packages`, both load-bearing:

- the delta is **added or modified** files, never names alone. A filename-only diff misses the
  package database, and a bundle without it leaves new binaries invisible to dpkg.
- `BUNDLE_EXCLUDE` strips the runtime directories the chroot needed but a bundle must not ship,
  plus caches and lockfiles — including `var/lib/dpkg/status`, which is replaced by a fragment.

**A script that downloads something says so.** The engine cannot see what a script fetched, so a
line on stdout of the form

```
KITCHEN-FETCHED <sha256> <path in the image> <url>
```

is recorded in the image's provenance and left out of the output shown. `firmware-refresh` prints one
per linux-firmware file, and [a file the build downloads](#a-file-the-build-downloads) shows the
shape for one. ELF files the step leaves behind that no package database owns are recorded too,
with their sha256.

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
```

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

- verb: boot.cmdline
  append: ["noload=05-chromium"]            # edits existing APPEND lines
  remove: ["automount"]                     # by key: drops `automount` and `automount=x`
  labels: [slax]                            # optional: restrict to these LABELs
```

`append` and `remove` are **arrays**, and the step reports how many APPEND lines it actually
changed — re-applying it says `0 entries`, not the entry count. A step with neither field is
refused rather than silently doing nothing.

**Use `LINUX`, not `KERNEL`, for a non-bzImage payload** — see
[edit-bootloader](../40-workflow/edit-bootloader.md).

**The payload an entry names must exist, or be on its way.** `kernel`, `linux`, `com32` and `initrd`
are resolved against the work tree, and an entry naming a file that is neither there nor installed by
a step of the same recipe that *will run* is refused — the entry would be written into the bootloader
and boot nothing. "will run" is what makes `--dry-run` work: `boot.payload` installs nothing under a
dry run, so existence alone would refuse a plan that is perfectly fine. Only absolute paths are
checked; isolinux also accepts one relative to the config it appears in, which cannot be resolved
before the entry is placed. `append` is a kernel command line and is not inspected, so an
`initrd=` inside it is not checked. Paths are compared after normalising, so `/slax/boot/x` and
`//slax/boot/./x` are the same file rather than a refusal.

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

If `src` names a **directory** it is copied recursively. `mode` applies to a single file
only; on a directory copy it is silently ignored, and the tree keeps the modes it had.
Under `--dry-run` nothing is written and nothing is created.

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
`kitchen pack` command line wins over the recipe.

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
