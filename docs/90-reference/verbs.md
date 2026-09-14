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

---

## Bundle

All seven produce or modify `slax/modules/*.sb`. **Load order is the numeric prefix and higher
wins**, so a bundle name must start with `NN-`; the verbs refuse one that does not, because a
bundle without a prefix has no defined position in the stack.

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

Absolute paths and `..` components in the archive are **refused**, not sanitised.

### `bundle.remove` ○

```yaml
- verb: bundle.remove
  match: "^05-chromium"     # regex over the filename
```

The only case where deleting beats overriding: a whiteout hides a file but does not reclaim its
space. See [remove-chromium](../50-cookbook/remove-chromium.md).

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
```

A key is **pinned by sha256**, like every other download here — an unpinned key lets a remote
party decide what your image trusts, and a bundle is where that becomes permanent. The
armour format is detected from the content, because apt reads it from the *extension* under
`signed-by=` and reports `NO_PUBKEY` for a key it is holding if the two disagree.

`keep: false` means the source list and key exist only in the build chroot. `keep: true` ships
them, so the booted system trusts that repository and can upgrade from it — say it deliberately.
`/var/lib/dpkg/arch` always ships, so a foreign architecture survives into the image.

### `bundle.script` ◐ chroot

`bundle.packages` generalised: run any script in a chroot of stacked bundles and package what
changed. For the things a package manager cannot do — compile something, generate keys, run a vendor
installer.

```yaml
- verb: bundle.script
  bundle: 07-generated
  from: [01-core]
  script: |
    #!/bin/sh
    set -e
    ssh-keygen -A
```

Two behaviours it shares with `bundle.packages`, both load-bearing:

- the delta is **added or modified** files, never names alone. A filename-only diff misses
  `var/lib/dpkg/status`, and a bundle without it leaves new binaries invisible to the package
  database.
- `BUNDLE_EXCLUDE` strips the runtime directories the chroot needed but a bundle must not ship,
  plus caches and lockfiles.

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

Place a file anywhere in the ISO tree, outside `/slax/`.

```yaml
- verb: iso.files
  files:
    - {dest: /README.txt, content: "…"}
```

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
