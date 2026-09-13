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

## `probe <iso>` / `fingerprint <iso> [-o FILE]`

`probe` matches an ISO against `compat/` and classifies every difference as benign, explained by a
known recipe, unexplained, or critical. `fingerprint` generates a new `compat/` entry. See
[fingerprints.md](../70-compat/fingerprints.md).

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

`fetch`, `shell`, `diff` and `upstream-diff` are listed in `--help` and **error out explicitly**
rather than pretending to work. See [project status](../00-overview/status.md).

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
