# Glossary

Slax vocabulary, plus the few `slax-kitchen` terms that are not obvious. Terms that mean something
different here than elsewhere are marked ⚠.

---

**aufs** — the union filesystem Slax stacks bundles with. Never merged into mainline Linux, which is
why Slax builds a [custom kernel](../30-inventory/kernel.md). Falls back to overlayfs if absent, at
the cost of `slax activate`. → [union](../10-anatomy/union-and-persistence.md)

**BEXT** — the bundle file extension, `sb`, set in `/lib/config`. Appears in `livekitlib` as
`*.$BEXT`.

**branch** — one layer of an aufs union. Bundles are inserted at branch index 1, just below the
writable branch at index 0.

**bundle** — a squashfs image holding part of a root filesystem, named `NN-name.sb`. The unit of
everything in Slax. → [bundles](../10-anatomy/bundles-squashfs.md)

**boot-info-table** ⚠ — 56 bytes that `genisoimage` patches **into `isolinux.bin` itself** after
laying out the image, so the loader can find itself without a path. Makes the on-ISO copy differ from
the on-disk one, permanently. → [el-torito](../10-anatomy/el-torito.md)

**changes** — the writable top layer of the union, at `/run/initramfs/memory/changes`. Everything you
modify lands here. tmpfs unless persistence is on.

**COM32 module** — a `.c32` file loaded by a SYSLINUX loader. Generation-specific: a 4.x module will
not load under a 6.03 core.

**DynFileFS** — Tomáš's FUSE filesystem presenting a sparse growable file, used to hold a perch
session on FAT32 or NTFS. Split at 4000 MB because FAT cannot hold a file ≥ 4 GiB.

**El Torito** — the ISO9660 boot specification. Its catalog declares one entry per firmware platform;
Slax declares exactly one, for BIOS. → [the UEFI gap](../20-boot-sequence/uefi-cd-gap.md)

**flavour** ⚠ — Debian-based or Slackware-based. Orthogonal to word size, so four targets.
Read from the version file in `01-core`, and `unknown` for a tree that carries neither. → [how it
is read](../70-compat/fingerprints.md#flavour-is-read-the-same-way)

**initrfs.img** — Slax's initramfs. Note the spelling: no `d`.

**LIVEKITNAME** — the top-level directory name, `slax`. Set in `/lib/config`, and **compiled into
`isolinux.bin`**, so renaming it needs the loader patched.

**Linux Live Kit** — the framework Slax is built on. Also by Tomáš.
<https://www.linux-live.org>

**MKMOD** ⚠ — the list in `/lib/config` of top-level directories to put in a bundle. It is a
**denylist by omission**: anything not listed is excluded, which is why Debian's bundles have no
`/tmp`, `/proc` or `/dev`.

**perch** — **per**sistent **ch**anges. Enabled by the substring `perch` appearing anywhere on the
kernel command line, so `perchdir=` and `perchsize=` each imply it.
→ [persistence](../05-using-slax/persistence-perch.md)

**rootcopy** — `/slax/rootcopy/`, copied verbatim into the union at boot by
`copy_rootcopy_content`. The cheapest customization mechanism Slax has, and absent from every
shipped ISO. → [rootcopy-overlay](../50-cookbook/rootcopy-overlay.md)

**session** — one numbered perch directory under `/slax/changes/`, selected by `perchdir=`.

**sortmod** — the `livekitlib` function that orders bundles by the numeric prefix of the
**basename**, so ordering is independent of directory depth.

**toram** — copy everything into RAM at boot, then unmount the medium so it can be removed.

**union** — the merged filesystem the system runs as `/`, at `/run/initramfs/memory/union`.

**whiteout** ⚠ — a `.wh.<name>` file in the writable branch marking a lower-layer file as deleted.
Packing one into a bundle makes that bundle *delete* a file. Distinct from aufs's own
`.wh..wh.*` bookkeeping, which must never be packed.

---

## `slax-kitchen` terms

**fingerprint** — the committed description of a known release in `compat/`: file lists, hashes,
kernel banner, El Torito shape. What `kitchen probe` compares an unknown ISO against.

**pack hint** — a note a verb leaves in `<work>/.kitchen/pack.yaml` telling `kitchen pack` to do
something extra later, e.g. `uefi: true` after `boot.uefi` built an ESP.

**preflight** — the check `kitchen apply` runs **before** any step, verifying that every tool and
file every verb will need is present. Added because verbs that checked their own requirements let
three recipes apply before the fourth failed.

**profile** — a named build: one base ISO plus an ordered list of recipes plus an output name.

**recipe** — a YAML file naming one or more verbs to apply to an unpacked ISO.

**verb** — one operation a recipe step can request, e.g. `bundle.packages`, `boot.menu`,
`rootcopy.files`. → [CLI reference](../90-reference/cli.md)

**work directory** — `<work>/iso/`, the unpacked ISO tree, plus `<work>/.kitchen/` holding
provenance and pack hints.

---

## Easily confused

| | |
|---|---|
| `perch` vs `savechanges` | perch keeps a **live** session on the medium; `savechanges` freezes the current session into a **bundle** |
| `load=` vs `noload=` | `noload=` translates commas to `\|` and applies to all bundles; `load=` does neither |
| `activate` vs adding a bundle | `slax activate` splices into the **running** union; copying a `.sb` to `/slax/modules/` takes effect **next boot** |
| `isolinux.bin` vs `extlinux.x64` | the first is a loader the BIOS runs; the second is an **installer** you run |
| `bootx64.efi` vs `syslinux.efi` | byte-identical; two names for firmware and for SYSLINUX |
| `/slax/` vs `/slax/modules/` | a bundle in `/slax/` mounts **first**, so it ranks **lower**, whatever its number |
| a layer vs a union layer | in [LAYERING.md](../../LAYERING.md), a **project** built on another project's image; everywhere else, a **bundle** in the running system's stack |
