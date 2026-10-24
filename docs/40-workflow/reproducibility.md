# Reproducibility

What a rebuild keeps, what it can never reproduce, and how to compare two builds.

Short version: **a build that changes a file records when it did, so two builds that change
anything are never bit-identical, and nothing here promises they are**
([CONTRIBUTING](../../CONTRIBUTING.md#promise-only-what-can-be-true)). What is promised instead:

- **fidelity:** a repack with no recipes keeps every file's bytes and mode, which
  [`ci/roundtrip.sh`](../../ci/roundtrip.sh) checks in CI;
- **comparison:** [`kitchen diff`](../90-reference/diff.md) compares two images' files by content,
  not by time, and with `--bundles` what is inside each bundle and each FAT12 image, the ESP among
  them;
- **matching contents:** two builds can hold the same contents when their inputs match, though that
  is not promised in general.

## What varies between two builds

| layer | between two builds | compare with |
|---|---|---|
| **a bundle a build makes** | its superblock records when it was made, and each file a script writes records when it was written — see below | `kitchen diff --bundles` |
| **`98-dpkg-db.sb`** | rebuilt by every `kitchen pack` of a tree where a recipe installed Debian packages | `kitchen diff --bundles` |
| **the ESP** (`/boot/efi.img`) | `mkfs.vfat` picks a volume serial for every image, and each directory entry records when it was written. Two builds' ESPs differed in those 20 bytes and in nothing they hold (2026-10-23) | `kitchen diff --bundles` |
| **initramfs** (`initrfs.img`) | rewritten when a verb edits it. Kitchen sorts its entries, so their order is stable: one tree packs the same twice. Upstream's command does not sort | [extract both](edit-initramfs.md) and compare the trees |
| **ISO** | each file's modify, access and change times, as the tree has them; the volume's dates, unless `--date` pins them; under genisoimage, the extent order; under `--uefi --hybrid`, the GPT's disk GUID | `kitchen diff` |

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

So on a container filesystem, two consecutive repacks of the *same* tree produce different archives.
As captured when this page was written, on 2026-09-13 (`a8b3fc1`):

```
unsorted, run A    8,869,652 B
unsorted, run B    8,900,924 B        same 768 files, different bytes, 31 KB apart
```

Add one pipe stage, and two packs of one tree are byte-identical:

```sh
find . -print | LC_ALL=C sort | cpio -o -H newc | xz -T0 -f --extreme --check=crc32
```

```
sorted, run 1      8,869,652 B   sha256 4ad7c7f9…
sorted, run 2      8,869,652 B   sha256 4ad7c7f9…     byte-identical
```

**cpio order is irrelevant to extraction**, so this changes nothing at boot. `LC_ALL=C` matters
because a locale-aware sort would order differently on different machines, which defeats the point.

**Kitchen's initramfs verbs already sort** (`_initramfs_pack` in `lib/apply.py`, since `46efdd9`),
so a repack can be diffed. A build that edits the initramfs still writes a new `initrfs.img`, and
the image records when.

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
creation (offset 813), modification (830), effective (864). Every byte of the 415 MiB payload matches,
and the boot-info-table checksum comes through unchanged at `0xe5d3e1ef`.

That is as close as genisoimage gets. It has no option to set the volume date, so `kitchen pack`
warns when `--date` is given with it, and packs without it.

### `xorriso` can pin the volume's dates, and nothing else

```sh
kitchen pack -s work/iso -o out.iso --backend xorriso --date 2023100920484300
```

That sets the volume's creation and modification dates. Each file's Rock Ridge entry still records
the file's own modify, access and change times, taken from the tree, so the image still differs
between builds. Measured on 2026-10-21 and 2026-10-22, on `slax-64bit-debian-12.2.0.iso`
([#95](https://github.com/Fullaxx/slax-kitchen/issues/95)):

- **two unpacks of one image do not pack the same:** extraction sets each file's change time;
- **one tree packs the same twice only where reading moves no access times:** from the first pack
  on a `noatime` mount, and from the second on `relatime`;
- **a `--uefi --hybrid` pack gets a random GPT disk GUID,** so even one tree packs differently
  every time.

xorriso also **uppercases the application id** (`slax` → `SLAX`), which genisoimage does not.
Nothing reads that field, so it is cosmetic; it does mean a byte-comparison against an upstream
image will never match.

Both backends ship; pick by task:

| goal | backend |
|---|---|
| match upstream's output as closely as possible | `genisoimage` (the default) |
| UEFI or isohybrid | `xorriso` — genisoimage cannot add a second El Torito entry |
| pinned volume dates | `xorriso --date …` |

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

A bundle a build makes differs by more than that byte, because each file a script writes records
when it was written. One script that writes the same file every run, built twice in two trees, gave
identical files in bundles 60 bytes apart (2026-10-21). And `kitchen pack` rebuilds one bundle
itself, `98-dpkg-db.sb`, on every pack of a tree where a recipe installed Debian packages: two packs
of one such tree differed in it (2026-10-22). Both are in
[#95](https://github.com/Fullaxx/slax-kitchen/issues/95).

Kitchen does not pin those times to make the bytes match. That would give each file the wrong time,
and the image would hand that to its users
([CONTRIBUTING](../../CONTRIBUTING.md#promise-only-what-can-be-true)).

This is why [`kitchen diff --bundles`](../90-reference/diff.md) also compares what is *inside* a
bundle: its bytes report two builds of one tree as different, every time, which is exactly the case
you are usually asking about.

The other caveat is the *input tree*, not the tool. A bundle built by installing packages holds what
the package repository served that day — and bookworm is oldstable while Slackware's configured
mirror points at `-current`. If two builds need the same packages, pin the source:

```yaml
apt:
  sources:
    - name: snapshot
      uri: http://snapshot.debian.org/archive/debian/20231009T000000Z
      suite: bookworm
      components: [main]
```

Without pinning, the same recipe run six months apart produces bundles with different contents, and
that has nothing to do with the tooling here.

The caller's environment is an input too, and that one is the tooling's. A build chroot inherits
it, except that its home and its temporary directory are its own, and the caller's XDG
directories are left out ([#88](https://github.com/Fullaxx/slax-kitchen/issues/88),
[#94](https://github.com/Fullaxx/slax-kitchen/issues/94)). So a script, or a package's maintainer
script, that reads a variable one caller set and another did not can build a different bundle for
each. No shipped `bundle.script` reads one it does not set (read 2026-10-22).

## What to do

**To compare two builds**, diff them:

```sh
kitchen diff a.iso b.iso --bundles
```

It compares every file by content and mode, and under `--bundles` what is inside each changed
bundle and ESP, and compares no recorded time; see [`kitchen diff`](../90-reference/diff.md).
When every difference is a container whose contents match, as a rebuild's are, it ends
`identical content`, exit 0. Measured 2026-10-23: two builds each of `example`, `boot-matrix` and
`tor` ended that way, and two of `minimal`, which makes neither a bundle nor an ESP, read
`identical`. Without `--bundles`, the first three pairs end `DIFFERENT`. On 2026-10-22, one tree
packed twice read `identical`, its volume dates three seconds apart.

**To prove a rebuild did not corrupt anything** — which is the more common need —
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
"the payload survived" is what actually matters for an ISO that has to boot.

**For two builds to hold the same contents**, pin the package sources, as above, and keep anything
that varies by itself, such as a date, out of what a script writes.

## A published image is explained, not reproduced

A rebuilt ISO does not match the published one byte for byte, and nothing here pretends otherwise.
What a published image carries instead is `<iso>.provenance.json`, the record of what went into
it, from which [`kitchen sources`](../90-reference/cli.md#sources-iso---json-f---markdown-f) accounts
for every file. `SHA256SUMS` checks that a download is the file that was published; the records say
what that file is made of.

See [publishing an image](publishing-images.md) for what travels with one.

## What is never reproducible, and does not need to be

| | |
|---|---|
| the time of anything a build changes | each changed file, every bundle a build makes and the image itself record when they were made. `--date` pins only the image's own dates |
| `isolinux.bin` inside the ISO vs on disk | `-boot-info-table` patches 56 bytes in during mastering. Compare the parsed checksum, not the bytes — `lib/isoparse.py` reports `self_consistent` |
| perch containers | sparse files created at first boot |
| anything under `/slax/changes/` | runtime state by definition |
