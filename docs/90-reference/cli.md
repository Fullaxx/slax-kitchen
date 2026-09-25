# `kitchen` command reference

```
kitchen <command> [options]
```

Paths are relative to your **current directory**, not the repo, so `work/` and `out/` behave like any
build directory. Both are gitignored.

**Every command checks the tools it needs before it starts**, and names the package for any that
is missing. `kitchen test` checks what all the modes you asked for need, at once, before running
any of them. The commands that only read an image (`test --structure`, `diff`, `sources`, `probe`,
`fingerprint`) used to run xorriso and the rest unchecked, and a missing one ended in a Python
traceback.

---

## `build <profile>` — the whole pipeline

```sh
kitchen build example              # profiles/example.yaml
kitchen build profiles/mine.yaml   # or an explicit path
```

Runs **unpack → apply → pack → test** from one file, then deletes the work tree.

| Option | Effect |
|---|---|
| `--base TARGET` | build against a different base than the profile pins, e.g. `slackware-64bit-15.0.4` — a target name, [not a path](#bring-your-own-iso) |
| `--keep` | keep the work tree for inspection |
| `--no-test` | build only, skip the `test:` list |
| `-f`, `--force` | rebuild over an existing work tree and output |
| `--local` | run the profile's boot tests here, not on the [boot host](../60-testing/boot-host.md) |

Roughly 4 seconds for a four-recipe profile, excluding boot tests.

The base ISO comes from `base.iso` if the profile sets it. Otherwise the filename is **looked up**
in `compat/sources.yaml`, which states a `file:` for every target — `isos/` plus that name. Only a
base naming no known target falls back to building the name from the three fields, and note they
are not in the same order: the target is `<flavour>-<arch>-<version>` and the ISO is
`slax-<arch>-<flavour>-<version>.iso`. Re-deriving it was a second source of truth in the other
field order, which is what the lookup replaced. A missing base fails before any work happens and
says which of the two it was looking for.

**Test expectations are derived, not trusted.** A profile listing `uefi-bootable` or `isohybrid` is
automatically asserted on having actually got an EFI El Torito entry and a hybrid MBR. A recipe that
silently did nothing is exactly what this catches. An entry lost because nobody listed the recipe is
caught from the image instead: one that carries `boot/efi.img` with no EFI entry fails, which is
what building on a UEFI-bootable base without `uefi-bootable` produces
([LAYERING.md, step 6](../../LAYERING.md#what-a-consumer-does)). A lost hybrid MBR leaves nothing
behind to catch.

## `test <iso>` — check an ISO you already have

```sh
kitchen test out/slax-example-12.2.0.iso                 # structure (default)
kitchen test out/x.iso --structure --expect-uefi --expect-hybrid
kitchen test out/x.iso --bios --uefi --seconds 40
kitchen test out/x.iso --kernel --expect 'dpkg-status: 600 packages'
```

`--structure` runs `tests/structure/iso_assert.py`: El Torito shape, Rock Ridge, Joliet,
boot-info-table consistency, squashfs parameters on every bundle, required files, and no
`boot/efi.img` without an EFI entry. About a second.
It expects the volume id `slax` unless `--volid` says otherwise; `kitchen build` passes whatever the
profile's recipes asked `pack` for, read through the same hint reader `pack` uses.

The required files are found in the same listing `diff` and `sources` use, by exact path. An image
whose files cannot be listed gets one failure saying so, not five claiming the kernel and bootloader
are missing. An image with no `/slax/boot` gets one failure saying it is not a Slax image.

`--bios` / `--uefi` boot the ISO in QEMU and capture a serial log plus a screenshot into
`<iso-dir>/boot-tests/`. Without `/dev/kvm` these run under TCG and are slow; the command says so
rather than appearing to hang.

They choose their boot entry by reading the ISO's own menu with `xorriso`. **A menu that cannot
be read is not a menu without the entry.** Without `xorriso`, or when the menu will not extract,
or when the serial entry's label cannot be typed at the `boot:` prompt, the mode fails and boots
nothing, saying which of the three it was. It used to report all three as "no serial entry in
this ISO" and pass on a screenshot of an unasserted boot. On a machine without `xorriso` that was
the normal outcome. Only an image that really has no serial entry falls back to screenshot
evidence, and `--no-keys` does the same when asked.

`--keys SPEC` drives the menu yourself instead: comma-separated **QEMU qcodes** — `down`, `ret`,
`esc`, `home`, `a`–`z`, `0`–`9` — with `Ns` to wait, as in `'3s,down,down,ret'`. There is no
repetition syntax, and it is `ret`, not `enter`. Both halves are checked, by whoever can answer:
a token that is not in that grammar is refused **here**, before anything boots and before the tree
goes to a [boot host](../60-testing/boot-host.md); a name QEMU does not know is refused by QEMU,
whose answer is reported rather than discarded. It used to be discarded, so a mistyped key was
simply never pressed — the run spent its whole `--seconds` ceiling and then blamed the *sequence*
for not selecting an entry, which points at timing rather than at the typo.

`--kernel` boots the kernel directly, bypassing the bootloader. It is the mode that can actually
fail on its own, because it puts the kernel on `ttyS0` by construction and requires livekit's
three markers in the serial log. `--expect STRING` adds your own requirements, repeatably — pair it
with the [`testkit`](../50-cookbook/testkit.md) recipe, which prints facts about the assembled
union just before `change_root`, and a structural claim becomes a boot assertion.

## `boot-host [check|clean|show]` — the machine boot tests run on

A `boot-host.ini` in the repository root names a machine with KVM to run the boot modes
above on, with the evidence landing here as usual. `--structure` is never sent: it reads
the image, and the image is already here.

```sh
cp boot-host.example.ini boot-host.ini && chmod 600 boot-host.ini
./kitchen boot-host check       # can it run the tests? names anything missing
./kitchen boot-host clean       # remove cached images, finished runs and old agents
./kitchen boot-host show        # what the file says, without connecting
```

`--local` on `kitchen test`, `kitchen build` and `ci/tier-c.sh` boots here for one command;
`KITCHEN_BOOT_HOST=local` does it for a shell. With a boot host in use, `kitchen test` checks
for `ssh`, `rsync` and `git` here rather than qemu and `mkfs.ext4`, which are checked on the
boot host by the boot host before it starts.

The file is gitignored and refused by the commit gates, because it names a machine that
would then receive every cloner's images. A configured host that cannot be used **fails the
command** — exit 2 for a configuration this will not act on, exit 3 for a host it could not
reach or that is missing something — and never falls back to a local boot, which would turn
a broken ssh key into the symptom "my tests got slower". `KITCHEN_BOOT_HOST=local` boots
here regardless. See [the boot host](../60-testing/boot-host.md).

## `unpack <iso> [-o DIR]`

Explodes an ISO into `DIR/iso` (default `work/`) using xorriso's osirrox mode, which preserves Rock
Ridge names and permission bits. Writes `DIR/.kitchen/origin.yaml` with the source path, sha256 and
size — that provenance is what `pack` uses to name its output.

An extraction that failed is refused, and leaves no tree and no record behind: a non-zero exit from
xorriso, a line at `SORRY` or above (the rule `diff` uses), or no `slax/boot/isolinux.bin` in the
result — which is all a file that is not an ISO produces, since xorriso extracts nothing from one
and still exits 0. What xorriso said is shown: its lines at `SORRY` or above, or its last lines
when it printed none, which is how a crash or a missing binary shows.

A `DIR` that already holds a work tree is refused: `DIR/iso`, or a `DIR/.kitchen` whose tree was
deleted. `-f`/`--force` replaces both, because the record in `.kitchen` describes the tree it came
with. Kept beside a fresh one, it had `status` list recipes the tree did not have, `apply` refuse
to apply them, and `pack` master the old tree's hints.

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
whichever sorted first — two *different* files, that is: the same file found twice, or through a
symlink, is one recipe. `compat.requires` is resolved depth-first with cycle detection, a cycle
being the same file reached again; when two of its files share a name, the message gives their
paths. Two different files with one name in the resolved plan are an error too, naming both,
whether the profile lists both or one arrives through another recipe's `compat.requires`: a
recipe's vars and its journal entry are keyed by its name, so only one of them could ever be
applied.

`--profile` takes the recipe list **and its per-recipe `vars:` overrides** from a profile instead
of naming recipes on the command line; the two forms are mutually exclusive. This is how
`kitchen build` invokes apply, and it is the supported way to change a shipped recipe's values
without editing the recipe.

A profile also declares the **base** it is written for, and `--profile` now holds it to that: if
`base.flavour` or `base.arch` disagrees with the work tree, apply refuses with exit 2 and nothing is
touched. Previously the base was ignored here — `kitchen build` chose its ISO from it, but applying
a profile to an existing tree took whatever `-w` pointed at, so a 64-bit profile could be applied to
a 32-bit tree and every `when: arch==` step would build the other architecture's half. A tree whose
architecture cannot be read is *not* a disagreement, and `--facts` still overrides.

**Where the facts come from, in order.** `flavour` and `arch` are read from `01-core` — the version
file for one, an ELF header for the other. Where that fails, the last resort is the **basename** of
the ISO in `<work>/.kitchen/origin.yaml`: `slax-64bit-debian-12.2.0.iso` names both. The directory
it sits under is never read, which was issue #27 — one stock 64-bit ISO kept in a folder called
`slax-32bit-and-64bit/` read as 32-bit, and `memtest86plus` installed the i586 build into it. A
name carrying neither leaves the fact `unknown`, and a `when:` guard on an unknown fact is false
rather than true.

`--facts k=v,k=v` overrides what `when:` guards see, **merged over the facts read from the tree** —
naming one leaves the rest measured, so `--facts flavour=debian` does not blank `arch` out from
under a step guarding on it. It reaches the steps that actually run, which it did not before: the
override used to change only the preflight plan, so one invocation could plan one set of steps and
apply another.

```
$ kitchen apply --profile profiles/minimal.yaml -w work-32
error: the profile is for arch 64bit, but this work tree is 32bit
  unpack the base the profile names, or pass --facts to override
```

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

See [repack-iso.md](../40-workflow/repack-iso.md) for why there are two backends. The xorriso
backend is judged the way [`unpack`](#unpack-iso--o-dir) judges an extraction — a non-zero exit,
or a line at `SORRY` or above, `MISHAP` included — and on failure `pack` shows what xorriso said.

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

Nothing requires `fetch`. `kitchen unpack /path/to.iso` accepts any Slax image — one you already
have, a corporate mirror, or one you previously customized — and a profile can point a build at
one with `base.iso:`, which wins over the derived path. A relative `base.iso:` is relative to the
repository that holds the profile, so a project that vendors the engine names an image in its own
tree. Building one project on another's released image this way is
[LAYERING.md](../../LAYERING.md). What was written when that image was mastered does not come with
it — its volume id, a UEFI boot entry, a hybrid MBR. `kitchen pack` writes those afresh, from this
build's recipes or its own flags ([LAYERING.md](../../LAYERING.md#what-a-consumer-does), steps 5
and 6).

**`--base` is not that.** It takes a *target name*, checked against `compat/sources.yaml`, so
`--base /path/to.iso` is refused naming the four that exist. It selects which known release to
build; `base.iso:` supplies an image the project does not know. This page used to say `--base`
took a path. It never did.

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
error: branding.yaml: branding was already applied to this tree at 2026-09-14T21:28:56Z.
  It produced: slax/modules/07-branding.sb
  Recipes are not idempotent -- applying one twice is a mistake, not a no-op.
  See `kitchen status work`; `kitchen unpack /home/you/slax-kitchen/isos/slax-64bit-debian-12.2.0.iso -o work --force` starts over from the image it was unpacked from.
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

Exit status is **0 when identical, 1 when different**, like `diff(1)`, so it drops into a script —
and **2 when something could not be read**, which is not an answer about the two images. Under
`--bundles` a bundle nobody can read is named in place and the rest of the report still reaches its
verdict; one unreadable bundle does not withhold the answer about every other one.
It is **2 when an image cannot be listed**. xorriso does not always say so itself: 1.5.6 lists a
file that is not an ISO as `/` alone and exits 0. So a listing with no files in it is refused, where
it used to diff as an empty image, with every file of the other reported removed.

**Nothing is extracted.** xorriso reports each file's start LBA and size, so content hashes come
from reading those extents straight out of the image — comparing two 416 MiB ISOs takes about two
seconds and no scratch space.

Two cases get special handling, because the naive answer is confidently wrong:

| | |
|---|---|
| `isolinux.bin` | bytes 8–63 are the boot-info-table, written **after** the file is placed, so two functionally identical builds differ there whenever the extent moves. Compared past byte 64, with the checksum verified separately and the LBA move reported. See [el-torito.md](../10-anatomy/el-torito.md). |
| `*.sb` bundles | a squashfs is a container with its own creation time, and it stores an mtime per file — so **two builds of the same tree differ in bytes while every file in them is identical**. "content changed" on a 79 MiB bundle is true and useless. `--bundles` compares what is *inside*: type, mode, owner, size, link target and sha256 per entry, with mtimes deliberately excluded. |

```
  inside /slax/modules/07-branding.sb   5 -> 4 entries
    +  etc/motd
    -  etc/issue
    ~  etc/hostname                                    content
```

When a bundle's bytes differ but nothing inside it does — a plain rebuild — it says so
rather than leaving you to guess:

```
  inside /slax/modules/08-ssh.sb   6 -> 6 entries
    (identical content -- every entry matches; only the container's own bytes differ, as a rebuild's do)
```

This is the one place `diff` extracts, and only for a bundle whose bytes already differ: a
content hash of a file inside a squashfs cannot be read from the ISO's extents. A bundle
that cannot be read is refused with exit 2, not reported as a bundle with no files.

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
of them are still Slax's and which are not. An initramfs this cannot read — a corrupt or truncated
archive, or one that decompresses to more than 512 MiB — is reported as not compared, with the
reason, and so is a target with no committed manifest. Neither is guessed at.

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
image has no provenance sidecar (it was packed by an older kitchen, or by something else), or when
its files cannot be listed.

That last case used to pass. Classification accounts for the files it is given, and xorriso 1.5.6
lists a file it cannot read as an ISO as `/` alone and exits 0. So an unreadable image came out
"unresolved 0", exit 0, under `--strict` too: a pass for an image nothing had examined.

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

**`kvm` means this account can write `/dev/kvm`**, which is the test every boot uses to choose
KVM over TCG. It used to test for read access, so a device that was readable but not writable had
doctor saying `KVM (fast)` about boots that ran under TCG. When the device is present and not
writable, doctor now says why:
- the account is not in a group that can write it, so join that group and log in again;
- the device's mode lets no group write it;
- its permissions already allow the account (it is root, anyone may write the node, it owns it,
  or the session is in a group that can write it), and it is refused anyway. That is not a
  permission bit at all: usually a container's device cgroup, which admits only what
  `--device` passed in.

In that case doctor no longer suggests `--device /dev/kvm`.

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
  iso: path/to/base.iso    # optional; relative to the profile's repo; else compat/sources.yaml
recipes:                   # a name or a path, or {name, vars} to override its vars
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
