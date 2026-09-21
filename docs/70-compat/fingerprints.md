# Fingerprints and `kitchen probe`

A **fingerprint** is the structural identity of a Slax release: what the ISO container looks like,
which kernel and initramfs it carries, and what the bundles are. One YAML file per supported
release lives in `compat/`.

```sh
kitchen probe out/slax-custom.iso      # what is this, and what changed?
kitchen fingerprint some.iso -o compat/new-release.yaml
```

## Why bother

Two questions, one mechanism:

- **"What is this ISO?"** An unmodified download matches a `compat/` entry exactly.
- **"What did my recipes actually do?"** A customized ISO matches one *closely*, and the difference
  list is a readable summary of the changes — including ones you did not intend.

## Supported targets

Four, all fingerprinted from the real ISOs:

| Fingerprint | Kernel | Notes |
|---|---|---|
| `debian-64bit-12.2.0` | `6.1.38` | |
| `debian-32bit-12.2.0` | `6.1.38-smp` | **upstream mislabel**, see below |
| `slackware-64bit-15.0.4` | `6.1.38` | |
| `slackware-32bit-15.0.4` | `6.1.38-smp` | |

32-bit Slax kernels carry `LOCALVERSION=-smp`, so their module directory is `lib/modules/6.1.38-smp`.
**Anything that hardcodes `6.1.38` breaks on 32-bit.**

## Arch detection does not trust the ISO

`/etc/slax-version` inside `01-core.sb` on the **32-bit Debian ISO reads `Slax 12.2.0 64bit`** — it
is byte-identical to the 64-bit build's copy. Upstream mislabelled it. The Slackware 32-bit build
gets it right.

So the fingerprinter probes a real ELF instead and records both:

```yaml
identity:
  slax_version_file: Slax 12.2.0 64bit        # what it claims
  arch_probe_file: usr/bin/ls
  arch_probe_result: ELF 32-bit LSB pie executable
  arch: 32bit                                  # authoritative
  arch_mismatch: /etc/slax-version claims 64bit but the ELF userland is 32bit -- upstream mislabelled this build
```

The probe lives in `lib/fingerprint.py` as `arch_from_extract()`, and `kitchen apply` reads
`when: arch==` from the same function — so a fingerprint and a recipe guard cannot disagree about
one tree. Nothing is executed: byte 4 of an ELF header is `EI_CLASS`, the field whose whole job is
to declare the binary's width.

Three traps when writing such a probe, all hit in this tree:

- **Do not probe `bin/busybox`.** It is i386 static on *all four* ISOs by design, so it tells you
  nothing about the userland.
- **You must deal with symlinks, but do not follow them off the extract.** On Slackware `usr/bin/ls`
  is a symlink to `../../bin/ls`, so an unresolved `symbolic link to …` string carries no bitness at
  all — this page used to say "follow them with `file -L`" for that reason, and that is the trap.
  Slackware's `01-core` also has `usr/bin/bash -> /bin/bash`, **absolute**, and `file -L` resolves an
  absolute target against the *host's* root. Measured on the 32-bit Slackware image on 2026-09-20:
  `file -bL` on that path answered `ELF 64-bit LSB pie executable, x86-64` — this machine's `bash`,
  in the probe this page calls authoritative. `resolve_within()` re-roots an absolute target at the
  extract and refuses anything still climbing out.
- **Do not read the return status of the extraction to decide whether it worked.** Measured on
  squashfs-tools 4.6.1, 2026-09-20: `unsquashfs` given a member list exits **0** whether or not any
  of those members existed, so a check on it is a check that cannot fail. Ask whether a candidate is
  there instead.

Each candidate is tried in order and the first that resolves to a real ELF wins, so the recorded
`arch_probe_file` is the *candidate that answered* — `usr/bin/ls` on all four ISOs — rather than the
file the bytes were finally read from, which on Slackware is `bin/ls`.

## Flavour is read the same way

The Debian bases carry `/etc/debian_version` inside `01-core.sb`; the Slackware bases carry
`/etc/slackware-version`. Neither pair carries the other's, so **which file is present is the
answer** — nothing has to be opened to see it:

```yaml
identity:
  slackware_version: Slackware 15.0+
  os_release_id: slackware
metadata:
  flavour: slackware                           # authoritative
```

The probe is `flavour_from_extract()`, beside `arch_from_extract()` in `lib/fingerprint.py`, and
`kitchen apply` reads `when: flavour==` from that same function — the same arrangement as arch, for
the same reason. It answers **`unknown`** for a `01-core` it could not read, and a `when:` guard on
an unknown fact is false rather than true.

Which is the whole of issue #35, because this module used to answer differently from the recipe
guard. It decided flavour with

```python
flav = "slackware" if "slackware" in str(ident).lower() else "debian"
```

— a substring match over the *stringified* identity dict, so it matched a key name as readily as a
value, and it had no third answer. An image whose `01-core` carried neither file, or no `01-core` at
all, was recorded as Debian: `flavour: debian` sitting beside `arch: unknown` and `version: unknown`
under an empty `identity: {}`. `kitchen apply` called the same bundle `unknown`, which is precisely
the disagreement `arch_from_extract()` exists to prevent.

`probe` could not report it, either. It regenerates the fingerprint with the same function that
wrote `compat/`, so both sides of the comparison carried the same default and always agreed — a
field listed as **critical** below that could not fire.

Two consequences worth knowing:

- **`/etc/os-release` is not consulted**, though the old substring match reached it through
  `os_release_id`. A rebuilt `01-core` that drops `slackware-version` but keeps `os-release` now
  reads `unknown` instead of `slackware`. That is the answer arch already gives for a tree it cannot
  read, and `--facts flavour=slackware` is how to say otherwise for a run.
- **The member list and the probe come from one tuple.** `FLAVOUR_CANDIDATES` supplies both the
  paths `unsquashfs` is asked to extract and the paths the probe looks for. A list that spelled them
  out separately could stop extracting one and turn every ISO `unknown` without a word — the same
  hazard `ARCH_CANDIDATES` is spread to avoid.

## How it reads the ISO without mounting anything

| Data | Method |
|---|---|
| ISO structure, El Torito, Rock Ridge, squashfs superblocks | `lib/isoparse.py`, pure python |
| `/slax/boot/*` | `xorriso -osirrox`, Rock Ridge safe |
| files inside a bundle | **`unsquashfs -o <offset>` reading the `.sb` in place inside the ISO** |
| the initramfs | `xz -dc \| cpio -idm` |

That third one is worth knowing: `unsquashfs -o` takes a byte offset, so you can pull
`/etc/slax-version` out of a 122 MB bundle **without ever extracting the bundle** — `lib/isoparse.py`
supplies the offset. What the read costs is in
[iso-container](../10-anatomy/iso-container.md).

Whole run: ~3.5 s per ISO.

## Difference classification

Not every difference is a problem, so `probe` classifies rather than just counting:

- **benign** — changes any rebuild causes: `iso.sha256`, size/padding, and any `*.offset` (a file's
  byte position inside the ISO is placement, not identity). `iso.application_id` is benign too,
  because the xorriso backend uppercases it — see [repack-iso.md](../40-workflow/repack-iso.md).
- **explained** — a change a known recipe is *meant* to cause, e.g. `eltorito.uefi_bootable`
  `false → true` is attributed to `uefi-bootable`. Some recipes exist precisely to set an
  *arbitrary* value — `iso-identity` puts whatever you like in `iso.volume_id`, `iso.publisher_id`
  and `iso.preparer_id` — so those entries match on the field changing at all rather than on a
  particular value. Only recipes whose effect is visible in the fingerprint appear here; a recipe
  that adds a bundle shows up as an unexplained bundle, which is the honest answer.
  `iso.publisher_id` and `iso.preparer_id` are blank on all four stock images, so any value at all
  means someone rebuilt it.
- **unexplained** — everything else. Listed individually. A whole added or removed bundle collapses
  to one line rather than nine superblock fields.
- **critical** — `kernel.release`, `initramfs.scripts.*`, flavour or arch. If these move, it is not
  the release it claims to be, and `probe` exits non-zero.

## Verdicts

```
MATCH (byte-identical to the known release)
MATCH (rebuild of the known release)          # only benign differences
MODIFIED (N unexplained differences)
MODIFIED (all differences explained by known recipes)
DIFFERENT RELEASE                             # exit code 1
```

## Adopting a new Slax release

```sh
kitchen probe slax-new.iso                       # expect: MODIFIED or DIFFERENT RELEASE
kitchen fingerprint slax-new.iso -o compat/<flavour>-<arch>-<version>.yaml
kitchen selftest ci
```
Then work through whatever breaks and write it up in `version-notes/`. Because 30 of 32
`/slax/boot/` files are byte-identical across flavours, most of a new fingerprint is shared.
