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
```

`--structure` runs `tests/structure/iso_assert.py`: El Torito shape, Rock Ridge, Joliet,
boot-info-table consistency, squashfs parameters on every bundle, and required files. About a second.

`--bios` / `--uefi` boot the ISO in QEMU and capture a serial log plus a screenshot into
`<iso-dir>/boot-tests/`. Without `/dev/kvm` these run under TCG and are slow; the command says so
rather than appearing to hang.

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

## `apply <recipe>... [-w DIR]`

Applies recipes to a work tree. Names resolve against `recipes/available/`, `recipes/examples/` and
the current directory. `compat.requires` is resolved depth-first with cycle detection.

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
| `--date YYYYMMDDhhmmsscc` | pin timestamps (xorriso only) |

See [repack-iso.md](../40-workflow/repack-iso.md) for why there are two backends.

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

## `doctor [--install-hooks]`

Reports every required tool, the kernel/container capabilities this machine has, and which recipe
tiers it can therefore run. `--install-hooks` symlinks `.git/hooks/{pre-commit,pre-push}` into
`ci/hooks/`.

## `validate <file.yaml>...`

Checks recipes and profiles against their JSON Schemas. Variables are substituted **before**
validation, so a templated `bundle: "{{bundle}}"` is checked against its resolved value.

## `selftest [stage]`

Runs the commit gates: `pre-commit`, `pre-push` or `ci`. Hooks call the same script, so a hook can
never drift from CI.

---

## Not implemented yet

`shell` and `upstream-diff` are listed in `--help` and **error out explicitly** rather than
pretending to work:

```
$ kitchen shell 07-mytools.sb
error: 'shell' is not implemented yet -- see docs/00-overview/status.md
```

See [project status](../00-overview/status.md).

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
recipes: [memtest86plus, serial-console, isohybrid, uefi-bootable]
output:
  name: "slax-example-{{version}}.iso"    # {{version}} {{flavour}} {{arch}} {{name}}
  hybrid: true
  backend: xorriso         # optional
test: [structure]          # structure | bios-boot | uefi-boot | usb | persistence
```

`usb` and `persistence` are accepted and reported as skipped — they need a KVM host.
