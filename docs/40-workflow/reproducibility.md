# Reproducibility

How much of a rebuild is deterministic, where it is not, and what you can do about each case.

Short version: **the payload is fully reproducible; the containers are not, by default.** Both the
ISO and the initramfs carry nondeterminism that upstream does not attempt to control — and in the
initramfs's case, that is worth fixing even if you do not care about reproducible builds, because it
turns a repack into something you can diff.

## The four layers

| layer | deterministic? | why |
|---|---|---|
| **bundles** (`.sb`) | ⚠️ **contents yes, bytes no** | every file in them is identical, but the superblock carries its own creation time — see below |
| **initramfs** (`initrfs.img`) | ❌ **no** — `find` has no `sort` | fixable in one pipe |
| **ISO** (`genisoimage`) | ❌ no — volume timestamps **and extent order** | fixable with the xorriso backend |
| **ISO** (`xorriso`) | ✅ yes with `--date` | but it uppercases the application id |

## The initramfs — the interesting one

Upstream's pack command, from both `initramfs_create` and the shipped `initramfs_pack`:

```sh
find . -print | cpio -o -H newc | xz -T0 -f --extreme --check=crc32
```

There is no `sort`, so entry order is `find`'s readdir order. **That is not stable.** Measured on the
reference container, five runs of `find . -print` over an unchanged tree:

| filesystem | result |
|---|---|
| ext2/3/4 | identical every time — stable, but arbitrary (hash order, not alphabetical) |
| **overlayfs** (Docker's default) | **5 runs, 5 different orders** |

So on a container filesystem, two consecutive repacks of the *same* tree produce different archives:

```
unsorted, run A    8,869,652 B
unsorted, run B    8,900,924 B        same 768 files, different bytes, 31 KB apart
```

Add one pipe stage and it becomes exact:

```sh
find . -print | LC_ALL=C sort | cpio -o -H newc | xz -T0 -f --extreme --check=crc32
```

```
sorted, run 1      8,869,652 B   sha256 4ad7c7f9…
sorted, run 2      8,869,652 B   sha256 4ad7c7f9…     byte-identical
```

**cpio order is irrelevant to extraction**, so this changes nothing at boot. `LC_ALL=C` matters
because a locale-aware sort would order differently on different machines, which defeats the point.

### This also explains a shipped artifact

The two 64-bit images' initramfs trees are identical except that Slackware adds one terminfo file —
yet Slackware's `initrfs.img` is **3,448 bytes smaller** despite having four more entries. Different
build hosts, different readdir order, different xz output. Nothing is wrong; it is just not
controlled.

## The ISO

### `genisoimage` varies in two ways

**Timestamps**, and **extent order** — file data is laid out in directory-scan order, so the same
tree on a different filesystem produces the same files at different LBAs. Measured: an identical
38-file tree rebuilt on a GitHub runner put `bootx64.efi` at LBA 10877 where the reference image has
it at 212257. Same size, same contents, 99.9% of sectors different.

`-sort` can pin the order if you need it, at the cost of no longer matching upstream's own layout.

### Timestamps

Round-tripping the stock 64-bit Debian ISO through `unpack` → `pack` with the genisoimage backend:

```
size  original=435,853,312  rebuilt=435,853,312  delta=+0
sectors differing: 19/212,819 (0.0089%)
```

All 19 are in the metadata region, and the difference is **entirely three PVD timestamp fields** —
creation (offset 813), modification (830), effective (864). Every byte of the 415 MB payload matches,
and the boot-info-table checksum comes through unchanged at `0xe5d3e1ef`.

That is as close as genisoimage gets. It has no option to set the volume date.

### `xorriso` can, at a cost

```sh
kitchen pack -s work/iso -o out.iso --backend xorriso --date 2023100920484300
```

Now two runs are bit-identical — but xorriso **uppercases the application id** (`slax` → `SLAX`),
which genisoimage does not. Nothing reads that field, so it is cosmetic; it does mean a
byte-comparison against an upstream image will never match.

**There is no single backend that is both faithful to upstream and reproducible.** Both ship; pick by
task:

| goal | backend |
|---|---|
| match upstream's output as closely as possible | `genisoimage` (the default) |
| bit-identical rebuilds | `xorriso --date …` |
| UEFI or isohybrid | `xorriso` — genisoimage cannot add a second El Torito entry |

## Bundles — identical inside, not byte-identical

`mksquashfs` with upstream's four flags lays out the *contents* deterministically for a given input
tree:

```sh
mksquashfs SRC DST -comp xz -b 1024K -Xbcj x86 -always-use-fragments
```

**But the bundle itself is not byte-identical between runs.** A squashfs superblock carries its own
`mkfs_time`, so that exact command run twice, a second apart, produces two files differing in
**one byte at offset 8** — measured 2026-09-20 on squashfs-tools 4.6.1, with every entry inside
identical in type, mode, owner, size, link target and sha256. This page used to say bundles had "no
timestamps in the output beyond the files' own", which is what that byte disproves.

Two flags remove it:

```sh
mksquashfs SRC DST -comp xz -b 1024K -Xbcj x86 -always-use-fragments -mkfs-time 0 -all-time 0
```

Measured the same day: byte-identical across runs. They are **not** used for shipped bundles, and
that is a choice rather than an oversight — `-all-time 0` throws away every file's real mtime, which
changes what the image hands its users. Pass them when you want a comparable rebuild.

This is why [`kitchen diff --bundles`](../90-reference/cli.md) compares what is *inside* a bundle
rather than its bytes: comparing the container reports two builds of one tree as different, every
time, which is exactly the case you are usually asking about.

The other caveat is the *input tree*, not the tool. A bundle built by installing packages is only as
reproducible as the package repository — and bookworm is oldstable while Slackware's configured
mirror points at `-current`. If you need a reproducible bundle, pin the source:

```yaml
apt:
  sources:
    - name: snapshot
      uri: http://snapshot.debian.org/archive/debian/20231009T000000Z
      suite: bookworm
      components: [main]
```

Without pinning, the same recipe run six months apart produces different bundles, and that has
nothing to do with the tooling here.

## What to do

**If you just want to diff two builds**, sort the initramfs. That is the whole fix, and it is worth
doing unconditionally — an unsorted repack is not diffable even against itself.

**If you need bit-identical output**, all three:

```sh
find . -print | LC_ALL=C sort | cpio -o -H newc | xz -T0 -f --extreme --check=crc32
kitchen pack --backend xorriso --date <fixed>
# and pin the package sources in any bundle.packages recipe
```

**If you want to prove a rebuild did not corrupt anything** — which is the more common need —
`ci/roundtrip.sh` is the check:

```sh
ci/roundtrip.sh isos/slax-64bit-debian-12.2.0.iso
```

It unpacks, repacks with no recipes, and extracts both images again, then asserts that **every file
is byte-identical, every mode is preserved**, Rock Ridge and Joliet survived, the volume identifiers
match, the El Torito shape is unchanged, and the rebuilt boot-info-table is self-consistent.

It deliberately does **not** compare sector positions. genisoimage allocates file extents in
directory-scan order, and readdir order varies by filesystem — a perfectly valid rebuild on a GitHub
runner moved `/slax/boot/EFI/Boot/*` from LBA 212257 to 10877, which a sector comparison scored as
99.9% corrupted. Comparing layout position tests the build host, not the build; the script reports
moves as a note and moves on.

That is a fidelity test, not a reproducibility test, and it is the one wired into CI — because
"the payload survived" is what
actually matters for an ISO that has to boot.

## A published image is explained, not reproduced

A rebuilt ISO does not match the published one byte for byte, and nothing here pretends otherwise.
What a published image carries instead is a record that accounts for every file in it:
`<iso>.provenance.json` and the sources manifest, checked by
[`kitchen sources`](../90-reference/cli.md#sources-iso---json-f---markdown-f---fetch-dir---strict). `SHA256SUMS`
checks that a download is the file that was published; the records say what that file is made of.

**The source assets are reproducible, even though the image is not.** `ci/release-assets.sh` writes
the project archives with no timestamps (`git archive` of the recorded commits, gzip with mtime 0),
and each source package as a tar with fixed metadata. Measured on the `tor` image at commit
`6419fa4`: two runs, each downloading GRUB's source package from Launchpad again, wrote identical
`SHA256SUMS`. Anyone with the same image and commit can regenerate the source set and compare hashes,
which is a check a rebuild of the ISO cannot offer. See
[publishing an image](publishing-images.md).

## What is never reproducible, and does not need to be

| | |
|---|---|
| `isolinux.bin` inside the ISO vs on disk | `-boot-info-table` patches 56 bytes in during mastering. Compare the parsed checksum, not the bytes — `lib/isoparse.py` reports `self_consistent` |
| perch containers | sparse files created at first boot |
| anything under `/slax/changes/` | runtime state by definition |
