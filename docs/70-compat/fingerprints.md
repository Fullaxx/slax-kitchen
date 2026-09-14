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

Two traps when writing such a probe, both hit during implementation:

- **Do not probe `bin/busybox`.** It is i386 static on *all four* ISOs by design, so it tells you
  nothing about the userland.
- **Follow symlinks (`file -L`).** On Slackware `usr/bin/ls` is a symlink to `../../bin/ls`, and an
  unresolved `symbolic link to ...` string contains no bitness at all — which silently produced the
  wrong answer until fixed.

## How it reads the ISO without mounting anything

| Data | Method |
|---|---|
| ISO structure, El Torito, Rock Ridge, squashfs superblocks | `lib/isoparse.py`, pure python |
| `/slax/boot/*` | `xorriso -osirrox`, Rock Ridge safe |
| files inside a bundle | **`unsquashfs -o <offset>` reading the `.sb` in place inside the ISO** |
| the initramfs | `xz -dc \| cpio -idm` |

That third one is worth knowing: `unsquashfs -o` takes a byte offset, so you can pull
`/etc/slax-version` out of a 122 MB bundle **without ever extracting the bundle** — `lib/isoparse.py`
supplies the offset, and the read takes about 20 ms.

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
