# `kitchen diff`

**What it does.** `kitchen diff A B` says what differs between two ISO images, two bundles, or
two FAT12 images such as an image's UEFI system partition (the ESP). It prints what differs, ends
with a one-line verdict, and exits 0, 1 or 2, so a script can act on the answer.

This page is the one place that says exactly what `kitchen diff` does:
- how it tells what it was given, and which of its two diffs answers;
- what each one compares, and what neither ever compares;
- how to read what it prints;
- what each verdict and exit status means;
- what it needs and costs, and where it falls short.

[The CLI reference](cli.md#diff-a-b---bundles---limit-n) keeps a summary and points here.

The measurements and samples here were taken on 2026-10-23 with
[#99](https://github.com/Fullaxx/slax-kitchen/issues/99)'s change, on Ubuntu 24.04 with python
3.12, xorriso 1.5.6 and squashfs-tools 4.6.1, unless a row says otherwise. The unprivileged
measurements ran as uid 1000 in the `deb12` reference container, whose squashfs-tools is 4.5.1.

## What it is for

It answers "what is the difference between **these two**?" That is the question you have when
both are yours: a stock image and your build of it, two builds a recipe apart, or one bundle from
two work trees. The other commands that read an image answer other questions:

| command | answers |
|---|---|
| `kitchen probe <iso>` | is this a known release, and has it been modified? It checks against the fingerprints in `compat/` |
| `kitchen sources <iso>` | what is in a built image, and where each part's source lives |
| `kitchen status [work]` | what a work tree is, and what has been applied to it |
| `kitchen test <iso> --structure` | is the image well formed, and does it hold what the options assert? |
| `kitchen diff A B` | what differs between these two? |

## Two diffs, chosen by what it is given

`kitchen diff` reads the start of each file and decides what it is, by its content and never by
its name:

| it is | when | compared by |
|---|---|---|
| an ISO image | bytes 32,769 to 32,773 are `CD001`, the signature of the first volume descriptor | the **image diff** |
| a squashfs bundle | it starts with `hsqs`, the squashfs magic | the **container diff** |
| a FAT12 image | its first 512 bytes are a FAT12 boot sector: `55 AA` at byte 510, sector and cluster sizes FAT allows, a fixed root directory, and fewer than 4,085 clusters | the **container diff** |

The squashfs magic is checked first, then the volume descriptor, then FAT12. The ISO comes before
FAT12 because an isohybrid ISO starts with an MBR, and an MBR ends in `55 AA` as a boot sector
does.

**Two of a kind, or nothing.** An image's files and one bundle's entries are not a comparison
either diff can make, so a mix is refused, exit 2, naming both kinds. So is a file that is none of
the three:

```
kitchen diff: work/example-debian-64bit-12.2.0/iso/slax/modules/07-extras.sb is a squashfs bundle and out/slax-example-12.2.0.iso is an ISO image; it compares two ISO images, two bundles or two FAT12 images
kitchen diff: README.md is not an ISO image, a squashfs bundle or a FAT12 image
no such file: out/nothing.iso
```

## The image diff

**How it reads.** xorriso lists every entry twice: `lsdl` gives each entry's type, mode and size,
and `report_lba` gives where each file's data starts. `lib/isoparse.py` reads the volume
descriptors and the boot catalogue itself. Each file's sha256 is read straight out of its extent
in the image, so **nothing is extracted** and no scratch space is used.

**An image xorriso cannot list is refused**, exit 2, before anything is printed:
- when xorriso exits non-zero, or prints a line at SORRY or above on its ladder: SORRY, MISHAP,
  FAILURE, FATAL or ABORT. MISHAP sits below xorriso's default abort threshold, so it can print
  one and still exit 0;
- when the listing holds no regular file. xorriso 1.5.6 lists a file that is not an ISO at all as
  `/` alone, and exits 0, and such a listing used to diff as an image with no files, with every
  file of the other reported removed. A file that is not an ISO is now refused before xorriso
  sees it, and this rule still refuses one that starts like an ISO and lists nothing.

**What it compares, section by section:**

| section | compares | counts toward the verdict |
|---|---|---|
| `size` | the two files' sizes | no: it is printed, never judged. An image padded with one more sector read `identical`, its size line `(+2.0 KiB)` |
| `identity` | the volume, system, application, publisher and preparer ids, and whether Rock Ridge and Joliet are present | yes |
| `boot` | the El Torito platforms and each entry's bootable flag, and whether an isohybrid MBR and a GPT are present | yes |
| `entries` | every path in the image: added, removed or changed | yes |

A changed entry says why, on one line:

| it says | when |
|---|---|
| `content` | a file's bytes differ, at the same size |
| `948 -> 867 B` | a file's size differs |
| `mode rw-r--r-- -> rwxr-xr-x` | its permissions differ |
| `file -> link` | its type differs |
| `-> old => new` | a symlink's target differs |

More than one reason is joined with commas. A directory is compared by whether it exists and by
its mode.

**Two special cases**, where comparing bytes would give a confidently wrong answer:
- **`isolinux.bin`.** Its bytes 8 to 63 are the boot-info-table, which the mastering tool writes
  **after** it places the file. They record the file's own LBA and a checksum of everything past
  byte 64, so two functionally identical builds differ there whenever the file lands at a
  different extent. It is compared past byte 64. A copy that differs only in its table prints
  `=  … boot-info-table only (checksum ok)`, which does not count, and `moved LBA a -> b` when it
  moved. See [el-torito.md](../10-anatomy/el-torito.md).
- **The El Torito boot catalogue.** It has no file extent of its own, so a byte comparison could
  not see it at all. It is compared by meaning, in the `boot` section.

**`checksum ok` says less than it reads.** What is checked is that the second image's table records
the LBA its BIOS boot entry points at, and `CHECKSUM BAD` means it does not. The checksum field
itself is not recomputed, and `kitchen test --structure` checks the same field. In the four
builds read for this page, the recorded checksum matched one recomputed from the file.

**What it never compares, and why:**
- **times:** each file's, and the volume's. They say when an image was made, not what is in it,
  and [reproducibility.md](../40-workflow/reproducibility.md#what-varies-between-two-builds) says
  why two builds' times always differ;
- **where a file sits:** its extent's LBA, apart from `isolinux.bin`'s move, which is reported;
- **the system area's bytes:** the MBR's code and partition table, and the GPT's contents,
  including the disk GUID that every `--uefi --hybrid` pack picks anew. Only whether each is
  present;
- **owners and link counts** in the Rock Ridge tree. `kitchen pack` records every owner as root,
  with `-uid 0 -gid 0` (`lib/pack.sh`);
- **Joliet names**, beyond whether Joliet is present;
- **El Torito details** beyond the platform and the bootable flag: the load size, the media type,
  and where the boot image starts.

## `--bundles`: looking inside the containers

A bundle is a container with a creation time of its own, and it stores an mtime per file. The ESP
is a FAT12 container whose volume serial `mkfs.vfat` picks for every image, and its directory
entries record when each file was written. So two builds of one tree differ in bytes while every
file inside is identical, and `content` on a 79 MiB bundle is true and useless. `--bundles`
compares what is **inside**.

**Which entries.** Every changed file whose bytes differ, and are one kind of container on both
sides: a squashfs, or a FAT12 image, told from its first 512 bytes. A file named `.sb` is a bundle
by its name as well, so one whose magic has gone is a bundle nobody can read, not a file that
changed. A container whose mode alone changed is not unpacked: its mode line is the whole
difference, and it counts. So does a file that is a container on one side only, which is not
compared inside. Captured from a copy of an image whose ESP had lost its boot-sector signature:

```
  inside /boot/efi.img   not compared: a FAT12 image -> not a container
```

**Inside a bundle,** every entry is compared by:
- its **type**: file, directory, symlink, fifo, socket, or a character or block device with its
  major and minor numbers;
- its **mode**, setuid, setgid and sticky bits included;
- its **owner**, as a numeric uid and gid;
- a file's **size** and the sha256 of its **content**, and a symlink's **target**.

A device node or a fifo is compared by its type, and never opened: a fifo with no writer blocks
forever, and `/dev/zero` never ends.

**The mode and the owner come from `unsquashfs -lln`,** the bundle's own record. The rest comes
from unpacking it into `TMPDIR`. Run unprivileged, unpacking restores neither: every entry comes
out the runner's, and setuid and setgid bits are dropped. Reading both from the unpacked tree, as
uid 1000, a bundle whose file changed owner read `identical content`, exit 0, and `-rwsr-xr-x`
came out `-rwxr-xr-x`. The listing reads both as any user, so the answer is the same with or
without root, and took 0.05 s for `01-core.sb`'s 18,670 entries. Every entry the listing names must
be found in the unpacked tree, or the bundle is refused.

Each bundle is unpacked, read and removed before the next, so `TMPDIR` holds one at a time. INT,
TERM and HUP wait for a removal to finish
([#98](https://github.com/Fullaxx/slax-kitchen/issues/98),
[the hand-back](handback.md#which-ways-of-ending-it-survives)).

**Inside an ESP,** every file is compared by its size and the sha256 of its content, and the
volume by its label. It is read in memory by `lib/isoparse.py`, without mtools.

**The verdict under `--bundles`.** When something differs, and every difference is a changed
container whose contents and mode match, the verdict is `identical content`, exit 0. An identity or
boot difference, an entry added or removed, or any other change still ends `DIFFERENT`. Without
`--bundles` the verdict is by the bytes, as it always was: a rebuilt bundle or ESP ends
`DIFFERENT`, exit 1.

## The container diff, on its own

`kitchen diff a.sb b.sb`, or the same with two FAT12 images, makes the comparison `--bundles` makes
of each changed container, on two that are in no image: for example, one bundle from two work
trees.

- **Identical bytes** end `identical`, exit 0, and nothing is unpacked.
- **Otherwise both are read before anything is printed**, so one that cannot be read is refused,
  exit 2, with the reason on stderr and nothing on stdout. A bundle cut short read:
  `kitchen diff: unsquashfs could not read the bundle at offset 0 in short.sb: Read on filesystem failed because EOF`.
- **`--bundles` changes nothing here:** a container is always compared by what is inside it.

## Reading the output

Each sample below was captured on 2026-10-23, from the command shown or the case described.

**A build against its stock base** shows every section of the image diff:

```
$ ./kitchen diff isos/slax-64bit-debian-12.2.0.iso out/slax-example-12.2.0.iso --bundles
diff  isos/slax-64bit-debian-12.2.0.iso
   -> out/slax-example-12.2.0.iso

  size        435,853,312 -> 443,547,648   (+7.3 MiB)

  identity    application_id  'slax' -> 'SLAX'
              preparer_id     '' -> 'XORRISO-1.5.6 2023.06.07.180001, LIBISOBU...'

  boot        El Torito       x86-BIOS  (2 entries) -> x86-BIOS, EFI  (4 entries)
              isohybrid MBR   absent -> present
              GPT             absent -> present

  entries     45 -> 55   (+10 added, 2 changed)
    +  /boot                                            (dir)
    +  /boot/efi.img                                    6.2 MiB
    +  /boot/grub                                       (dir)
    +  /boot/grub/grub.cfg                              948 B
    +  /slax/boot/memtest.bin                           160.5 KiB
    +  /slax/modules/07-extras.sb                       660.0 KiB
    +  /slax/modules/98-dpkg-db.sb                      120.0 KiB
    +  /slax/rootcopy                                   (dir)
    +  /slax/rootcopy/run                               (dir)
    +  /slax/rootcopy/run/preinit.sh                    3.7 KiB
    ~  /slax/boot/isolinux.cfg                          1,202 -> 1,526 B
    ~  /slax/boot/syslinux.cfg                          1,537 -> 1,878 B
    =  /slax/boot/isolinux.bin                          boot-info-table only (checksum ok)
                                                        moved LBA 46 -> 3261

  DIFFERENT
```

- `preparer_id` is cut to fit: xorriso writes its own banner there, 80 characters in this image.
- `El Torito` names the platforms, and counts every entry in the boot catalogue, its validation
  entry and section headers included.
- An added or removed entry shows its size, and `(dir)` for a directory.
- The `=` line does not count. `isolinux.bin` moved, so its table changed, and nothing else did.
- No added entry is looked inside: only a changed one can be compared with its other side.

**Two builds of one profile**, under `--bundles`:

```
$ ./kitchen diff first.iso out/slax-example-12.2.0.iso --bundles
diff  first.iso
   -> out/slax-example-12.2.0.iso

  size        443,547,648 -> 443,547,648   (same)

  entries     55 -> 55   (3 changed)
    ~  /boot/efi.img                                    content
    ~  /slax/modules/07-extras.sb                       content
    ~  /slax/modules/98-dpkg-db.sb                      content

  inside /boot/efi.img   1 -> 1 files
    (identical content -- every file matches; only the container's own bytes differ, as a rebuild's do)

  inside /slax/modules/07-extras.sb   64 -> 64 entries
    (identical content -- every entry matches; only the container's own bytes differ, as a rebuild's do)

  inside /slax/modules/98-dpkg-db.sb   4 -> 4 entries
    (identical content -- every entry matches; only the container's own bytes differ, as a rebuild's do)

  identical content
```

Each changed container has an `inside` section, which counts what it holds on each side. Without
`--bundles`, the same pair ends after `entries`, `DIFFERENT`, exit 1. The two ESPs differed in 20
bytes: their volume serials, 4 bytes at offset 39, and two bytes of the times in each of eight
directory entries.

**Two profiles**, where a container's contents do differ:

```
$ ./kitchen diff out/slax-example-12.2.0.iso out/slax-tor-12.2.0.iso --bundles
...
    ~  /slax/modules/98-dpkg-db.sb                      122,880 -> 118,784 B

  inside /boot/efi.img   1 -> 1 files
    (identical content -- every file matches; only the container's own bytes differ, as a rebuild's do)

  inside /slax/modules/98-dpkg-db.sb   4 -> 4 entries
    ~  var/lib/dpkg/status                              size, content

  DIFFERENT
```

Inside a container, a changed entry says which of its facts differ, by the names in the list
above: `type`, `mode`, `owner`, `size`, `link target`, `content`. An added entry is `+` and a
removed one `-`. An ESP whose label changed says so on a line of its own:
`label  'SLAXEFI' -> 'OTHERLBL'`.

**The container diff**, on one bundle from two work trees:

```
$ ./kitchen diff ../a/work/example-debian-64bit-12.2.0/iso/slax/modules/07-extras.sb \
                 work/example-debian-64bit-12.2.0/iso/slax/modules/07-extras.sb
diff  ../a/work/example-debian-64bit-12.2.0/iso/slax/modules/07-extras.sb
   -> work/example-debian-64bit-12.2.0/iso/slax/modules/07-extras.sb

  size        675,840 -> 675,840   (same)
  kind        a squashfs bundle

  inside      64 -> 64 entries
    (identical content -- every entry matches; only the container's own bytes differ, as a rebuild's do)

  identical content
```

Two ESPs read the same, with `kind        a FAT12 image` and `1 -> 1 files`.

**A container nobody can read**, under `--bundles`: here a copy of the second image with
`07-extras.sb`'s first four bytes zeroed. It is named in place, and every other container is still
compared:

```
  inside /slax/modules/07-extras.sb   could not be read
    unsquashfs could not read the bundle at offset 442298368 in b.iso: FATAL ERROR: Can't find a valid SQUASHFS superblock on b.iso

  inside /slax/modules/98-dpkg-db.sb   4 -> 4 entries
    (identical content -- every entry matches; only the container's own bytes differ, as a rebuild's do)

  DIFFERENT
  (one or more containers could not be read -- see above)
```

That run exits 2.

## Verdicts and exit statuses

| the last line | exit | when |
|---|---|---|
| `identical` | 0 | nothing differs. In the image diff: no identity, boot or entry difference, and an `isolinux.bin` that differs only in its boot-info-table does not count. In the container diff: the two files' bytes match |
| `identical content` | 0 | the bytes differ, and only the containers' own. In the image diff, under `--bundles`: every difference is a changed bundle or FAT12 image whose contents and mode match. In the container diff: every entry matches, and an ESP's label does |
| `DIFFERENT` | 1 | anything else |
| none: a refusal on stderr | 2 | refused before any report: a command line it cannot parse; a file that does not exist; one that is none of the three kinds; two of different kinds; a tool that is not installed; an image xorriso cannot list; in the container diff, a container that cannot be read |
| `DIFFERENT`, then `(one or more containers could not be read -- see above)` | 2 | under `--bundles`, a changed container could not be read. The images did differ, and the question was not fully answered |
| none | 130, 143 or 129 | ended by INT, TERM or HUP. The unpacked bundle is removed first: each of the three, sent one second into a container diff of two `01-core.sb` with about 17,000 paths unpacked, left `TMPDIR` empty. INT also prints Python's traceback |

## Options

| option | does |
|---|---|
| `--bundles` | for two ISO images: compares what is inside each changed bundle or FAT12 image, and judges the images by that. It needs `unsquashfs`. Two containers are always compared by what is inside, so it changes nothing for them |
| `--limit N` | how many changed paths to list, 20 unless given. In `entries`, every added and removed path is listed, then changed ones until N lines in all, and a last line `... M more`. Inside each container, the added, removed and changed lists each stop at N, and each says how many more, as in `... 41 more removed` |

## Needs and costs

**Tools,** checked once `diff` has read what it was given, and before it lists or unpacks
anything: a missing one is refused, exit 2, naming its package.
- two ISO images: `xorriso`, and `unsquashfs` under `--bundles`;
- two bundles: `unsquashfs`;
- two FAT12 images: nothing, as `lib/isoparse.py` reads them.

**Time,** median of three, with both inputs already in the page cache:

| comparison | time |
|---|---|
| the image diff, two `example` builds | 0.62 s |
| the same pair, `--bundles` | 0.74 s |
| two `tor` builds, `--bundles`: five containers, the largest of them 138.4 MiB | 2.27 s |
| the container diff, two `07-extras.sb` | 0.12 s |
| the container diff, two ESPs | 0.09 s |
| the container diff, `01-core.sb` of the stock 64-bit Debian image against a repack of it: 18,670 entries | 5.29 s |

**Space.** The image diff, and FAT12 images, need none. A bundle is unpacked into `TMPDIR`, one at
a time: that `01-core.sb` is 122.3 MiB, and 602 MiB unpacked.

**Memory.** An ESP is read whole, with each of its files beside it. The ESP that `example`,
`boot-matrix` and `tor` build was 6,488,064 bytes.

**Under `sudo`,** `diff` writes nothing in your tree, so it gives nothing back
([the hand-back](handback.md)).

## Common uses

Each block was run as written, from a checkout, on 2026-10-23.

**Two builds of one profile.** Keep the first image, build again, and compare:

```sh
sudo ./kitchen build example
cp out/slax-example-12.2.0.iso first.iso
sudo ./kitchen build example --force
./kitchen diff first.iso out/slax-example-12.2.0.iso --bundles
```

The second build needs `--force`. Without it, it builds the whole image again and then stops at
`pack: out/slax-example-12.2.0.iso already exists`. Its report is the second sample above.

**A build against its stock base**, which says what the profile changed. Its report is the first
sample above.

```sh
./kitchen diff isos/slax-64bit-debian-12.2.0.iso out/slax-example-12.2.0.iso --bundles
```

**What one recipe changed.** Pack the tree before and after:

```sh
./kitchen unpack isos/slax-64bit-debian-12.2.0.iso
./kitchen pack -o before.iso
./kitchen apply recipes/available/branding.yaml
./kitchen pack -o after.iso
./kitchen diff before.iso after.iso
```

`branding` added `/slax/modules/07-branding.sb`, 4.0 KiB, and changed nothing else: `+1 added`,
`DIFFERENT`, exit 1.

**One bundle, or the ESP, from two work trees.** Build each checkout with `--keep`. Then, from one
of them, with the other beside it as `../a`:

```sh
./kitchen diff ../a/work/example-debian-64bit-12.2.0/iso/slax/modules/07-extras.sb \
               work/example-debian-64bit-12.2.0/iso/slax/modules/07-extras.sb
./kitchen diff ../a/work/example-debian-64bit-12.2.0/iso/boot/efi.img \
               work/example-debian-64bit-12.2.0/iso/boot/efi.img
```

**In a script,** by the exit status:

```sh
./kitchen diff first.iso out/slax-example-12.2.0.iso --bundles > diff.txt
case $? in
  0) echo "the same contents" ;;
  1) echo "they differ: see diff.txt" ;;
  *) echo "not answered: see diff.txt" ;;
esac
```

## What a verdict does not say, and the pitfalls

- **`DIFFERENT` is not "broken".** It says only that something differs, and the report says what.
  Images built from different profiles, or for different targets, differ almost everywhere.
- **A rebuild without `--bundles` is `DIFFERENT`.** The shipped profiles that build a bundle,
  `example`, `browsers`, `browsers-firmware` and `tor`, make it anew with each build, and every
  bundle records when it was made. The UEFI profiles, those four and `boot-matrix`, make their ESP
  anew too (read 2026-10-23). Only `--bundles` judges either by what is inside.
- **The loader is not among those differences.** In `example`, `boot-matrix` and `tor`, each built
  twice, `EFI/BOOT/BOOTX64.EFI` was the same 6,193,152 bytes in both builds, so each rebuild ended
  `identical content`.
- **`identical content` is not `identical`.** The bytes differ, and only the containers' own:
  usually a bundle's creation time and stored mtimes, or an ESP's serial and times. `diff` does not
  say why. A different compressor or block size, padding, or a corrupted byte outside any file's
  data would land there too.
- **Extended attributes are never compared**, file capabilities among them, with root or without:
  two bundles that differ only in one read `identical content`. On 2026-10-23 no stock or built
  bundle carried any: `unsquashfs -stat` read `Number of xattr ids 0` for each one.
- **Unprivileged, a bundle holding a device node cannot be read.** unsquashfs cannot create one
  without root, so the bundle is refused, exit 2. No stock or built bundle held one on 2026-10-23.
- **An ESP is compared by its files and its label,** and by nothing else. It is not compared by
  its volume serial or its times, by its files' FAT attributes (read-only, hidden, system), or by
  their long names. Files go by their 8.3 short names, as in `EFI/BOOT/BOOTX64.EFI`. An empty
  directory is not seen, because a directory is seen only through the files in it.
- **`checksum ok`** on `isolinux.bin` checks the table's LBA, not its checksum, as the image diff
  section says.
- **`--limit` hides lines, and says so:** every list it cuts short ends `... M more`. Raise it to
  see the rest.
- **Why two builds' bytes differ at all** is in
  [reproducibility.md](../40-workflow/reproducibility.md#what-varies-between-two-builds).

## History

| commit | what changed |
|---|---|
| `20e82bd` | written on 2026-09-14: identity, boot structure, and every file by its extent, with `--bundles` comparing each changed bundle's file list |
| `7f8ded8` | its tools are checked before it runs them, where a missing xorriso had ended in a traceback |
| `69435b3` | a listing that failed is refused, not read as an image with no files |
| `0be9227` | a MISHAP from xorriso is a failure too |
| `a941ca2` ([#30](https://github.com/Fullaxx/slax-kitchen/issues/30)) | bundles are compared by what is inside them, not by their file lists |
| `fe17036` | the `(identical content …)` line stopped naming a cause nothing had measured |
| `1eda24e` | one bundle nobody can read no longer withholds the verdict on the rest |
| `003a72e` ([#98](https://github.com/Fullaxx/slax-kitchen/issues/98)) | TERM and HUP end it as exits, so the unpacked bundle is removed |
| [#99](https://github.com/Fullaxx/slax-kitchen/issues/99) | two diffs behind one command; ESPs compared inside; `identical content` a verdict, exit 0; owners and modes read from the bundle's listing; `--limit` says what it left out |

## Where it lives

- **`lib/diff.py`:**
  - `main()` tells each input's kind with `file_kind()`, asks for its tools, and chooses a diff;
  - `diff()` is the image diff: `_entries()` and `_xorriso()` list an image, and `_hash_all()`
    hashes its extents;
  - `diff_containers()` is the container diff;
  - `container_kind()` tells a bundle or a FAT12 image from its first 512 bytes;
  - `bundle_manifest()` and `_bundle_listing()` read a bundle, and `esp_manifest()` an ESP;
  - `manifest_changes()` and `_inside()` compare two manifests and print what differs.
- **`lib/isoparse.py`:** `IsoReader` reads the volume descriptors, the boot catalogue and the
  boot-info-table. `fat12()` reads a FAT12 image, and `fat12_layout()` its boot sector.
- **`lib/diff.py` runs `main()` through `handback.run`,** which makes TERM and HUP exits.
- **The tests:**
  - in `tests/unit/test_diff.py`: `test_a_rebuild_is_not_a_change`, `test_a_change_is_named`,
    `test_a_bundle_that_cannot_be_read_is_refused`,
    `test_an_unreadable_bundle_does_not_abandon_the_report`,
    `test_a_term_during_bundles_takes_the_unpacked_bundle_away`,
    `test_two_bundles_compare_by_content`, `test_two_esps_compare_by_content` and
    `test_an_image_diff_hands_its_changed_containers_over`;
  - in `tests/unit/test_listing.py`: `test_entries_refuses_a_listing_it_cannot_trust` and
    `test_diff_refuses_before_it_reports`;
  - in `tests/unit/test_apply.py`: `test_parse_lsdl`, for the listing's parser;
  - in `tests/unit/test_need.py`: `test_every_command_refuses_instead_of_crashing`, whose row for
    `diff` gives it two files that begin as ISO images.
