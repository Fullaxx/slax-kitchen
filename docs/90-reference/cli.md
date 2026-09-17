# `kitchen` command reference

```
kitchen <command> [options]
```

Paths are relative to your **current directory**, not the repo, so `work/` and `out/` behave like any
build directory. Both are gitignored.

---

## `build <profile>` — the whole pipeline

```sh
kitchen build example              # profiles/example.yaml
kitchen build profiles/mine.yaml   # or an explicit path
```

Runs **unpack → apply → pack → test** from one file, then deletes the work tree.

| Option | Effect |
|---|---|
| `--keep` | keep the work tree for inspection |
| `--no-test` | build only, skip the `test:` list |
| `-f`, `--force` | rebuild over an existing work tree and output |

Roughly 4 seconds for a four-recipe profile, excluding boot tests.

The base ISO comes from `base.iso` if the profile sets it, otherwise from
`isos/slax-<arch>-<flavour>-<version>.iso`. A missing base fails before any work happens and says
which of those two it was looking for.

**Test expectations are derived, not trusted.** A profile listing `uefi-bootable` or `isohybrid` is
automatically asserted on having actually got an EFI El Torito entry and a hybrid MBR. A recipe that
silently did nothing is exactly what this catches.

## `test <iso>` — check an ISO you already have

```sh
kitchen test out/slax-example-12.2.0.iso                 # structure (default)
kitchen test out/x.iso --structure --expect-uefi --expect-hybrid
kitchen test out/x.iso --bios --uefi --seconds 40
kitchen test out/x.iso --kernel --expect 'dpkg-status: 600 packages'
```

`--structure` runs `tests/structure/iso_assert.py`: El Torito shape, Rock Ridge, Joliet,
boot-info-table consistency, squashfs parameters on every bundle, and required files. About a second.
It expects the volume id `slax` unless `--volid` says otherwise; `kitchen build` passes whatever the
profile's recipes asked `pack` for, read through the same hint reader `pack` uses.

`--bios` / `--uefi` boot the ISO in QEMU and capture a serial log plus a screenshot into
`<iso-dir>/boot-tests/`. Without `/dev/kvm` these run under TCG and are slow; the command says so
rather than appearing to hang.

`--kernel` boots the kernel directly, bypassing the bootloader. It is the mode that can actually
fail on its own, because it puts the kernel on `ttyS0` by construction and requires livekit's
three markers in the serial log. `--expect STRING` adds your own requirements, repeatably — pair it
with the [`testkit`](../50-cookbook/testkit.md) recipe, which prints facts about the assembled
union just before `change_root`, and a structural claim becomes a boot assertion.

## `unpack <iso> [-o DIR]`

Explodes an ISO into `DIR/iso` (default `work/`) using xorriso's osirrox mode, which preserves Rock
Ridge names and permission bits. Writes `DIR/.kitchen/origin.yaml` with the source path, sha256 and
size — that provenance is what `pack` uses to name its output.

## Preflight

**`apply` and `build` check every tool, file and kernel capability the whole plan needs before
touching anything**, and report all of them at once:

```
preflight failed -- 2 unmet requirement(s), nothing has been modified:
  - missing tool 'grub-mkstandalone' needed by uefi-bootable  (apt-get install grub-efi-amd64-bin)
  - missing file '/usr/lib/ISOLINUX/isohdpfx.bin' needed by isohybrid  (apt-get install isolinux)
```

This exists because the alternative was measured and is unpleasant: applying four recipes with one
tool missing downloaded a 164 KB binary, edited two bootloader configs and wrote a pack hint before
dying on the fourth, leaving a half-modified work tree to clean up by hand.

Three details worth knowing:

- **`build` preflights before unpacking**, using the flavour and arch from the profile, so it fails
  in a second rather than after writing 400+ MiB. It also checks `pack`'s own requirements — the
  ISO backend, and `isohdpfx.bin` when the profile asks for `hybrid` — rather than discovering them
  after the whole build has run.
- **`when:` guards are evaluated first**, so a step that will be skipped does not demand its tools.
  A Slackware-only step never asks for Slackware tools on a Debian build.
- **Capabilities are probed by using them**, not by reading `CapEff`. A container runtime can
  present a capability that seccomp then blocks, so `bundle.packages` actually attempts a `chroot`
  and a `mknod`.

`--skip-preflight` exists for odd cases. `--preflight-only` checks and exits, touching nothing.

Requirements are declared per verb in `VERB_REQUIRES` (`lib/apply.py`); a new verb adds one entry
there and gets preflight, `doctor` reporting and the error messages for free.

## `apply <recipe>... [-w DIR]` / `apply --profile <profile>`

Applies recipes to a work tree. A name resolves against **every directory under `recipes/`** and
then the current directory, so a fork's `recipes/<project>/` needs no configuration — see
[recipes in a fork](../40-workflow/recipes-in-a-fork.md). A path is accepted anywhere, including
outside the repo. A name found in **two** directories is an error naming both files, rather than
whichever sorted first. `compat.requires` is resolved depth-first with cycle detection.

`--profile` takes the recipe list **and its per-recipe `vars:` overrides** from a profile instead
of naming recipes on the command line; the two forms are mutually exclusive. This is how
`kitchen build` invokes apply, and it is the supported way to change a shipped recipe's values
without editing the recipe.

`-n` / `--dry-run` reports what each step would do without touching anything.

**Order matters for `uefi-bootable`** — it builds the GRUB menu by parsing `isolinux.cfg`, so list it
after anything that adds a menu entry.

## `pack [-o FILE|DIR]`

Rebuilds an ISO from a work tree. Default output is `./out/<source>-custom.iso`, derived from
`origin.yaml`. An existing file is never overwritten without `--force`.

| Option | Effect |
|---|---|
| `--backend genisoimage\|xorriso` | default: genisoimage, or xorriso if `--uefi`/`--hybrid` |
| `--uefi`, `--hybrid` | normally set by recipes via pack hints, not by hand |
| `--volid`, `--appid`, `--sysid`, `--publisher`, `--preparer` | volume descriptor fields; [`iso-identity`](../50-cookbook/iso-identity.md) sets them as hints, and a flag given here wins |
| `--date YYYYMMDDhhmmsscc` | pin timestamps (xorriso only) |

See [repack-iso.md](../40-workflow/repack-iso.md) for why there are two backends.

**`pack` writes `<iso>.provenance.json` beside the ISO**: what each applied recipe fetched and built
— URLs and sha256s, the package versions apt resolved and each `.deb`'s sha256, the build host's
GRUB and the MBR `pack` copied from it, a build claim for a compiled binary — plus the backend, the
kitchen commit and its submodule pins, and the ISO's own sha256. Verbs record into
`<work>/.kitchen/provenance.json` as they run; `pack` finalizes it next to the ISO, where
`kitchen build` deleting the work tree cannot take it. Paths are basenames or paths inside the
image, and anything that looks like a place on the build machine is refused — the same rule as the
Tier C ledger, sharing one pattern.

## `fetch [target|--all] [--verify-only] [-o DIR]`

Download a base ISO and verify it. With no argument, lists the known targets and whether each is
already present.

```sh
kitchen fetch                          # what do I have?
kitchen fetch debian-64bit-12.2.0      # ~416 MiB
kitchen fetch --all                    # all four, ~1.8 GiB
kitchen fetch --all --verify-only      # check what is on disk, download nothing
```

| | |
|---|---|
| `target` | `<flavour>-<arch>-<version>`, e.g. `slackware-32bit-15.0.4` |
| `--all` | every target in `compat/sources.yaml` |
| `--verify-only` | verify what is on disk and exit non-zero if anything is wrong |
| `-o`, `--output-dir` | default `isos/`, which is gitignored |

**Verification is not optional.** Size *and* sha256 are checked against `compat/sources.yaml` after
every download. A file that already verifies is left alone, so re-running is free.

### Mirrors

`compat/sources.yaml` holds an ordered mirror list. Each is tried in turn; a mirror that fails to
connect, or serves a file that does not verify, is reported and the bad file deleted before the next
is tried:

```
$ kitchen fetch debian-32bit-12.2.0
  fetching slax-32bit-debian-12.2.0.iso  from images.rapidlinux.org
  images.rapidlinux.org failed: <urlopen error [Errno -2] Name or service not known>
  fetching slax-32bit-debian-12.2.0.iso  from ftp.linux.cz
  ok       slax-32bit-debian-12.2.0.iso verified
```

Because both hashes and sizes are pinned, mirror **order** is a courtesy and throughput decision
rather than a trust one — no mirror can poison a build. Adding one needs no code change: append to
`mirrors:` with a `layout` template over the target's keys (`"{file}"` or `"{tree}/{file}"`).

`ci/upstream-watch.sh` HEADs every target on every mirror weekly, so a new entry is monitored for
rot automatically.

### Bring your own ISO

Nothing requires `fetch`. `kitchen build --base /path/to.iso` and `kitchen unpack /path/to.iso`
accept any image — an ISO you already have, a corporate mirror, or one you previously customized.

## `probe <iso>` / `fingerprint <iso> [-o FILE]`

`probe` matches an ISO against `compat/` and classifies every difference as benign, explained by a
known recipe, unexplained, or critical. `fingerprint` generates a new `compat/` entry. See
[fingerprints.md](../70-compat/fingerprints.md).

## `status [work] [-v]`

What a work tree is, and what has been applied to it.

```
$ kitchen status work
work tree  /home/you/slax-kitchen/work
  origin     slax-64bit-debian-12.2.0.iso
             unpacked 2026-09-14T21:28:55Z, 415.7 MiB

  applied    4 recipes, in order
    1. branding  2026-09-14T21:28:56Z
       bundle.files
       + slax/modules/07-branding.sb
    2. boot-cmdline  2026-09-14T21:28:56Z
       boot.cmdline x2
       + slax/boot/isolinux.cfg
       + slax/boot/syslinux.cfg
    4. remove-bundle    2026-09-14T21:28:56Z
       bundle.remove
       - slax/modules/05-chromium.sb

  pack hints (set by recipes, applied at mastering time)
    checksums = sha256
    volid = SLAX-CUSTOM

  bundles    6, 314.3 MiB total (load order: higher wins)
    01-core.sb                122.3 MiB
    07-branding.sb              4.0 KiB  <- added here
```

`kitchen apply` has always written `<work>/.kitchen/journal.yaml`. **Nothing read it until
this command existed** — and a record nobody reads is not provenance, it is a file.

`-v` adds the source ISO's full path and sha256.

A `provenance` line says how much the recipes recorded — steps, downloads, package versions — or that
a tree was applied before provenance existed, so "fetched nothing" and "recorded nothing" are not
confused.

### What the journal records

Artifacts, not narration. Each entry is the recipe name, a UTC timestamp, the verbs that
**actually ran** (a `when:`-skipped step does not appear), and the durable paths it produced.
A `-` prefix means removed.

This changed when the journal became readable: recording everything printed gave ~69 lines of
prose per recipe, including warnings, indented file lists, and the same bundle under two
different wordings. `Ctx.say()` now prints, and `Ctx.record()` notes an artifact.

### Recipes are not idempotent

Applying one twice is a mistake, not a no-op, and `apply` now says so using the journal:

```
error: branding was already applied to this tree at 2026-09-14T21:28:56Z.
  It produced: slax/modules/07-branding.sb
  Recipes are not idempotent -- applying one twice is a mistake, not a no-op.
  See `kitchen status work`; to start over, unpack the base ISO again.
```

Previously this surfaced as whichever verb collided first — `slax/modules/07-branding.sb already
exists. Pick another number` — which is true, unhelpful, and points at the wrong problem.

## `diff <isoA> <isoB> [--bundles] [--limit N]`

What changed between two images — identity, boot structure, and every entry. Where `probe` asks
"is this a known release, and has it been modified?", `diff` answers "what is the difference between
**these two**?", which is the question you have when both are yours.

```
$ kitchen diff isos/slax-64bit-debian-12.2.0.iso out/custom.iso

  size        435,853,312 -> 359,440,384   (-72.9 MiB)

  identity    application_id  'slax' -> 'SLAX'

  boot        El Torito       x86-BIOS  (2 entries) -> x86-BIOS, EFI  (4 entries)

  entries     45 -> 48   (+4 added, -1 removed)
    +  /boot/efi.img                                    6.2 MiB
    +  /boot/grub/grub.cfg                              635 B
    -  /slax/modules/05-chromium.sb                     79.1 MiB
    =  /slax/boot/isolinux.bin                          boot-info-table only (checksum ok)
                                                        moved LBA 46 -> 3214

  DIFFERENT
```

Exit status is **0 when identical, 1 when different**, like `diff(1)`, so it drops into a script.

**Nothing is extracted.** xorriso reports each file's start LBA and size, so content hashes come
from reading those extents straight out of the image — comparing two 416 MiB ISOs takes about two
seconds and no scratch space.

Two cases get special handling, because the naive answer is confidently wrong:

| | |
|---|---|
| `isolinux.bin` | bytes 8–63 are the boot-info-table, written **after** the file is placed, so two functionally identical builds differ there whenever the extent moves. Compared past byte 64, with the checksum verified separately and the LBA move reported. See [el-torito.md](../10-anatomy/el-torito.md). |
| `*.sb` bundles | a squashfs is a container: "content changed" on a 79 MiB bundle is true and useless. `--bundles` lists which paths inside it moved, read at an offset without unpacking. |

```
  inside /slax/modules/07-branding.sb   5 -> 4 entries
    +  squashfs-root/etc/motd
    -  squashfs-root/etc/issue
```

The El Torito boot catalog is compared **by meaning** — platforms and bootable flags — not by bytes.
It has no file extent of its own, so a byte comparison could not see it at all.

## `sources <iso> [--json F] [--markdown F] [--fetch DIR] [--strict]`

What is in a built image, and where the source of each part lives. Every file in the ISO is
classified from evidence: the image's `<iso>.provenance.json` (written by `pack`) and the committed
stock manifests in [docs/30-inventory/manifests/](../30-inventory/manifests/). Nobody maintains a
list of what a recipe adds.

```
$ kitchen sources out/slax-tor-12.2.0.iso
sources  slax-tor-12.2.0.iso  (base debian-64bit-12.2.0)
  slax 34  debian 1  slackware 0  prebuilt 2  built 1  ours 6  unresolved 0
  warning: slax/modules/01-firmware.sb: Slax's own build removed the license texts of the firmware
           in this bundle (usr/lib/firmware/ipw2x00.LICENSE remains); the firmware-refresh recipe
           reinstalls Debian's, and remove-bundle with drop: 01-firmware leaves the firmware out
```

| Class | What it is | Where its source is |
|---|---|---|
| `slax` | byte-identical to the stock image (`isolinux.bin` compared past its boot-info table) | [linux-live](https://github.com/Tomas-M/linux-live) and the repositories it names |
| `debian` | a bundle a recorded `bundle.packages` step produced | each package's source version on `snapshot.debian.org` |
| `slackware` | the same, for Slackware packages | the Slackware tree the package came from |
| `prebuilt` | a recorded download installed unmodified (`bundle.fromTarball`, `boot.payload`), or the isohybrid MBR `pack` copied from the build host | the recipe's `upstream_source`; the host package's source version |
| `built` | compiled or assembled here: GRUB's EFI image, a busybox with a verified build claim, a binary a `bundle.script` declares | gathered by `--fetch` |
| `ours` | written or generated by a recipe or by kitchen (menus, `98-dpkg-db.sb`, a script's bundle) | the project source archive `--fetch` writes |
| `unresolved` | anything else | — |

**A file matches a recorded step only if its sha256 is the one recorded.** A bundle altered after the
step that made it is unresolved, not attributed to that step or to the recipe's journal entry. So
is a `bundle.script` bundle holding an ELF file that no package **vouches for** — one no package
owns, or one whose bytes differ from the md5 its package recorded, which is what a script
overwriting `/usr/bin/ssh` leaves behind — and that no `declares:` entry names. So is an image built
from a dirty kitchen tree, a busybox whose build claim does not match the binary, and an ISO whose
sha256 is not the one its provenance records.

**`slax/boot/initrfs.img`, when a recipe has repacked it, is counted rather than asserted.** Its
members are read straight out of the image (the cpio is parsed here, so no privilege and no `cpio`
binary are needed) and compared with the committed initramfs manifest, and the note says how many
of them are still Slax's and which are not.

**A download nobody pinned is said out loud.** `bundle.fromTarball` and `boot.payload` record
whether the recipe gave a `sha256:`; without one, what the build fetched is whatever that server
served that day, so it is a warning — and with `--strict`, unresolved. `--strict` does the same for
a download whose recipe names no `upstream_source`.

**`ours` has to be true.** Whatever a recipe copies in with a local `src:`, and the recipe file
itself, is recorded with its content digest. `sources` then asks git whether the recorded commit
holds exactly that content, so an uncommitted, untracked or edited input is unresolved. So is an
input outside both the kitchen and project checkouts, unless it is a file a build-host package owns:
`locale-timezone-keyboard` copies the host's tzfile, and it points at the host's `tzdata` source the
way the isohybrid MBR does. So is compiled code copied in that way, committed or not, because
nothing records its source.

Exit status: **0** when everything is classified, **1** when anything is unresolved, **2** when the
image has no provenance sidecar (it was packed by an older kitchen, or by something else).

| Option | Effect |
|---|---|
| `--json F` | the full manifest: every component, its class, sha256, packages and pointers |
| `--markdown F` | the same for people, with the firmware statement and the upstream source list |
| `--fetch DIR` | gather source: the kitchen tree at the recorded commit **with its submodules**, the tree of the project that vendors it (found as the git superproject, or named by `PROJECT_ROOT`), and the source of everything `built` |
| `--provenance F` | read the sidecar from somewhere other than `<iso>.provenance.json` |
| `--allow-dirty` | accept an image built from a tree with uncommitted changes (the recipe matrix builds from one) |
| `--strict` | a download whose recipe names no `upstream_source` is unresolved instead of a warning |

**Offline unless `--fetch`.** `kitchen build` never runs it; the recipe matrix runs it on every image
it builds. `--fetch` writes each archive deterministically (gzip with no timestamp), so the same
commit gives the same bytes. GitHub's automatic "Source code" archives leave submodules out, which
is why the project tree is archived here. A source package comes from `apt-get source` when the host
has deb-src entries, otherwise from Launchpad (an Ubuntu build host) or snapshot.debian.org (a
Debian one), each file checked against the `.dsc` or snapshot's hash. A busybox build's tarball is
checked against its build claim, and its `.config` is copied from beside the binary.

## `shell <bundle|work> [--stack A,B] [-c CMD]`

chroot into a bundle, or into a stack of them, to look around. Needs `CAP_SYS_CHROOT`
and `CAP_MKNOD`.

```
$ kitchen shell work --stack 01-core,07-mytools
kitchen shell: 01-core.sb + 07-mytools.sb
  18,671 files, stacked low to high (higher wins, as at boot)
  THROWAWAY: /tmp/kitchen-shell-xxxx/root is deleted on exit and the .sb files are never
  written. To capture changes, use the bundle.script verb instead.
```

With no `--stack` it layers **every** bundle in load order, which reproduces the booted
filesystem — the full Slax stack unpacks in about seven seconds.

**Nothing you do inside persists.** The bundles are unpacked to a temporary directory that
is deleted on exit and the `.sb` files are never written. That is deliberate: a session of
typing is not reproducible, so the supported way to capture changes is
[`bundle.script`](verbs.md#bundlescript--chroot), which runs a script in this same
environment and packs the delta.

**Only `01-core` ships a userland.** Every other bundle is a fragment meant to be layered
on it, so chrooting into one alone has no `/bin/sh`. `shell` detects that and prints the
stack you probably meant rather than letting `chroot` fail:

```
$ kitchen shell work --stack 07-mytools
kitchen shell: 07-mytools.sb has no /bin/sh.
  Only 01-core ships a userland; every other bundle is a fragment
  meant to be layered on it. Stack it on a base:
    kitchen shell work --stack 01-core.sb,07-mytools.sb
```

## `upstream-diff [--from REF] [--to REF] [--offline] [--write]`

Which pages in [15-upstream/](../15-upstream/) does an upstream move invalidate?

`ci/upstream-watch.sh` answers "did upstream move?" weekly. This answers the next question.
Those pages describe upstream's own source — what `livekitlib`'s 49 functions do, what
`initramfs_create` copies — and every one is a claim about a file at the pinned commit. When
the pin moves those claims go stale silently: nothing in the repo changes and no test fails.

```
$ kitchen upstream-diff
  a27eca6167fe -> 9825e9375700   (40 commits)

  DOCUMENTED AND CHANGED -- these pages assert behaviour that moved:

    docs/15-upstream/livekitlib-reference.md
      106 lines across 1 file
        livekitlib                                           +81 -25
```

Grouped by page and ranked by churn, because the action is "go re-read that page". Exit
status is **1 when something documented moved**, 0 otherwise, so it runs in CI.

**The map is scraped from the docs.** Each page cites its own source path, so a new page is
covered the day it is written and a hardcoded table cannot rot — which is the exact failure
this command exists to catch. `--write` regenerates
[drift-report.md](../15-upstream/drift-report.md).

## `doctor [--report|--install-hooks]`

Reports every required tool, the kernel/container capabilities this machine has, and which recipe
tiers it can therefore run. `--install-hooks` symlinks `.git/hooks/{pre-commit,pre-push}` into
`ci/hooks/`.

**`--report` is the one to paste into a bug report.** One plain-text block, no colour, and unlike
the default view it carries **tool versions** — which is what "works here, fails there" usually
comes down to:

```
kitchen:  0.1.0-dev (3b2f6e0)
uname:    Linux 6.8.0-124-generic x86_64 GNU/Linux
os:       Ubuntu 24.04.4 LTS
uid:      0 (root)
tools:
  mksquashfs           mksquashfs version 4.6.1 (2023/03/25)
  xz                   xz (XZ Utils) 5.4.5
toolchain:
  xz --check=crc32: yes
  squashfs-tools >= 4.2: yes (4.6.1)
capabilities:
  mknod=yes  chroot=yes  kvm=no ...
```

The `toolchain` block **probes** the two facts that decide correctness rather than convenience:
the kernel's xz decoder cannot do CRC64, which is xz's default, so an initramfs built without
`--check=crc32` is an image that will not boot. Both were asserted in prose here for a long time
and tested nowhere.

## `validate <file.yaml>...`

Checks recipes and profiles against their JSON Schemas. Variables are substituted **before**
validation, so a templated `bundle: "{{bundle}}"` is checked against its resolved value.

It also runs the cross-checks a schema cannot state: a recipe's `metadata.name` must match its
filename, a `Sources` mirror layout may not reference a field no target defines, and **a recipe that
removes or renumbers a bundle may contain nothing else** — that one decides where a recipe may sit
in a plan, so mixing it with a build makes its position a constraint on every other recipe. See
[composing bundles](../40-workflow/composing-bundles.md).

## `selftest [stage] [scope]`

Runs the commit gates: `pre-commit`, `pre-push` or `ci`. Hooks call the same script, so a hook can
never drift from CI.

Every gate runs at every stage; the stage only decides the default *scope* — `staged` for
`pre-commit`, `tree` for the other two. Pass a scope explicitly to override it.

## `help [command]`, and `--help`

`kitchen help` lists the commands; `kitchen help <command>` or `kitchen <command> --help` gives
detail for one. This works on **every** subcommand.

---

## Everything is implemented

Every command in `--help` works. `kernel.replace` is the one remaining **verb** worth building —
`initramfs.config` and `boot.secureboot` are declared and won't-do, so 23 of 26 are implemented;
see
[project status](../00-overview/status.md).

---

## Profile format

```yaml
apiVersion: slax-kitchen/v1
kind: Profile
metadata:
  name: example
base:
  flavour: debian          # debian | slackware
  arch: 64bit              # 32bit | 64bit
  version: "12.2.0"
  iso: path/to/base.iso    # optional; otherwise derived from the three above
recipes:                   # a bare name, or {name, vars} to override that recipe's vars
  - memtest86plus
  - isohybrid
  - name: serial-console
    vars: {port: ttyS1, speed: "9600"}
  - name: libreoffice
    vars: {bundle: 20-office}
  - uefi-bootable          # order matters: it parses isolinux.cfg, so keep it last
output:
  name: "slax-example-{{version}}.iso"    # {{version}} {{flavour}} {{arch}} {{name}}
  hybrid: true
  backend: xorriso         # optional
test: [structure]          # structure | kernel-boot | bios-boot | uefi-boot | usb | persistence
```

`usb` and `persistence` are accepted and reported as skipped — they need a KVM host.

**`vars:` overrides merge** over the recipe's own defaults, so setting one leaves the rest alone.
Naming a var the recipe does not declare is an error listing what it does declare, and the value
is schema-checked as though the recipe had been written that way — `bundle: NONSENSE` fails the
`NN-name` pattern at validation rather than deep inside `bundle.packages`. What was used is
recorded in the journal and shown by `kitchen status`.
