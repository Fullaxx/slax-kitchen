#!/usr/bin/env python3
"""kitchen diff -- what actually changed between two images, or two bundles or ESPs.

TWO DIFFS BEHIND ONE COMMAND, chosen by what it is given, told from its first bytes (#99):

  the image diff      two ISOs: identity, boot structure, and every file, by content
                      and mode. Under --bundles it hands each changed container -- a
                      bundle, or the UEFI ESP -- to the container diff, and judges the
                      images by what is inside them.
  the container diff  two bundles, or two ESPs, compared by what is inside them. A
                      rebuild that changed nothing ends `identical content`, exit 0.

docs/90-reference/diff.md says in full what each compares, prints and returns.

`probe` answers "is this a known release, and has it been modified?" against the
fingerprints in compat/. This answers the different question "what is the difference
between THESE two?", for the case where both are yours: a stock ISO and your build of
it, two of your builds a recipe apart, or one bundle from two work trees.

NOTHING IS EXTRACTED, except one case that says so. xorriso reports each file's start
LBA and size, so the content hash comes from reading that extent straight out of the
image. Extracting two 416 MiB ISOs to compare 38 files would move ~830 MiB through the
filesystem to answer a question that needs ~830 MiB of *reads* and no writes at all.
When this was written (2026-09-14) that was about 40 s against about 6 s, and it needs no
scratch space; docs/90-reference/diff.md has a later figure.

The exception is bundle_manifest(), under --bundles and in the container diff: a content
hash of a file INSIDE a squashfs cannot be read from the ISO's extents, and comparing the
file list instead is what made two builds of one tree indistinguishable from two that
really differ. An ESP needs no extraction: esp_manifest() reads its files in memory.

THREE SPECIAL CASES, each of which would otherwise produce a confidently wrong answer:

  isolinux.bin  bytes 8..63 are the boot-info-table, written by the mastering tool
                AFTER the file is placed -- they encode the file's own LBA and a
                checksum of everything past byte 64. Two functionally identical builds
                differ there whenever the file lands at a different extent. Compared
                past byte 64, with the checksum verified separately.

  *.sb bundles  a squashfs is a container with a creation time of its own, and it stores
                an mtime per file -- so two builds of the same tree differ in BYTES while
                every file in them is identical. "content changed" on a 79 MiB bundle is
                true and useless, so --bundles compares what is INSIDE: type, mode, owner,
                size, link target and sha256 per entry, mtimes deliberately excluded.

  the ESP       /boot/efi.img is a FAT12 image whose volume serial mkfs.vfat picks for
                each build, and whose directory entries record times. Two builds of one
                ESP differed in exactly those 20 bytes, with the same label and the same
                BOOTX64.EFI (measured 2026-10-23, #99), so --bundles compares its files by
                content, and its label.
"""
from __future__ import annotations

import argparse
import filecmp
import hashlib
import os
import re
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handback  # noqa: E402
import need  # noqa: E402
from isoparse import IsoReader, fat12, fat12_layout  # noqa: E402

SECTOR = 2048
# The boot-info-table occupies bytes 8..63 of isolinux.bin. 64 is the first byte the
# mastering tool does not touch; see docs/10-anatomy/el-torito.md.
BIT_LEN = 64


def _human(n: float) -> str:
    """Bytes as something a person can compare at a glance."""
    for unit in ("B", "KiB", "MiB", "GiB"):
        if abs(n) < 1024 or unit == "GiB":
            return f"{n:,.0f} {unit}" if unit == "B" else f"{n:,.1f} {unit}"
        n /= 1024
    return f"{n} B"


# 'e' is xorriso's own type letter for the El Torito boot catalog. It has no "File data"
# extent, so report_lba never mentions it, and a first version of this pattern accepted
# only [-dl] -- silently dropping the one file `uefi-bootable` rewrites. The catalog is
# compared by meaning instead, in the El Torito section.
TYPES = {"-": "file", "d": "dir", "l": "link", "e": "bootcat"}
LSDL = re.compile(r"^([-dle])(\S{9})\s+\d+\s+\S+\s+\S+\s+(\d+)\s+.{12}\s+'(.*)'$")


def parse_lsdl(line: str):
    """One `xorriso -exec lsdl` line -> (path, entry), or None if it is not an entry."""
    m = LSDL.match(line)
    if not m:
        return None
    kind, perm, size, path = m.groups()
    ent = {"type": TYPES[kind], "mode": perm, "size": int(size),
           "lba": None, "target": None}
    if kind == "l" and " -> " in path:
        path, ent["target"] = path.split(" -> ", 1)
    return path, ent


class ListingError(RuntimeError):
    """xorriso could not list an image -- which is not the same as an image with no files."""


def _xorriso(iso: str, action: str) -> str:
    """One `-find / -exec <action>` pass, refused rather than returned when it failed.

    The exit status alone does not say so: measured on xorriso 1.5.6, listing a file that
    is not an ISO at all exits 0, prints no FAILURE line, and lists only `/`. So a failure
    is a non-zero exit OR any line at SORRY or above on xorriso's ladder -- SORRY, MISHAP,
    FAILURE, FATAL, ABORT -- and emptiness is judged by the caller. MISHAP matters: it sits
    below the default abort threshold (FAILURE), so xorriso can report one and exit 0.
    """
    r = subprocess.run(["xorriso", "-indev", iso, "-find", "/", "-exec", action, "--"],
                       capture_output=True, text=True)
    said = [ln.strip() for ln in r.stderr.splitlines()
            if re.search(r"\b(SORRY|MISHAP|FAILURE|FATAL|ABORT)\b", ln)]
    if r.returncode != 0 or said:
        raise ListingError(f"xorriso could not list {os.path.basename(iso)}: "
                           + (said[0] if said else f"it exited {r.returncode}"))
    return r.stdout


def _entries(iso: str) -> dict:
    """path -> dict(type, mode, size, lba). One xorriso pass for each of the two facts.

    `lsdl` gives type/mode/size for everything including directories and symlinks;
    `report_lba` gives the extent, and only for files. Directories have no extent of
    their own worth comparing, so they carry lba=None and are compared by existence.

    RAISES ListingError rather than returning a listing nobody can trust. This returned
    whatever parsed -- so a listing that failed read as an image with no files, and
    `kitchen sources`, which accounts for the files it is given, passed an image
    it had examined none of: "unresolved 0", exit 0. An image with no regular file at all
    is refused the same way; no image this toolkit reads is one.
    """
    out: dict = {}
    for line in _xorriso(iso, "lsdl").splitlines():
        parsed = parse_lsdl(line)
        if parsed:
            path, ent = parsed
            out[path] = ent
    if not any(e["type"] == "file" for e in out.values()):
        raise ListingError(f"xorriso listed no files in {os.path.basename(iso)}: "
                           f"it could not be read, or it is not an ISO image")

    lba_out = _xorriso(iso, "report_lba")
    for line in lba_out.splitlines():
        if not line.startswith("File data lba:"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            lba, size = int(parts[1]), int(parts[3])
        except ValueError:
            continue
        path = parts[4].strip().strip("'")
        if path in out:
            out[path]["lba"] = lba
            out[path]["size"] = size
    return out


def _sha256_extent(fh, lba: int, size: int, skip: int = 0) -> str:
    """Hash `size` bytes starting at `lba`, optionally ignoring the first `skip`."""
    h = hashlib.sha256()
    fh.seek(lba * SECTOR + skip)
    left = size - skip
    while left > 0:
        chunk = fh.read(min(left, 1 << 22))
        if not chunk:
            break
        h.update(chunk)
        left -= len(chunk)
    return h.hexdigest()


def _hash_all(iso: str, ents: dict) -> None:
    with open(iso, "rb") as fh:
        for path, e in ents.items():
            if e["lba"] is None:
                continue
            skip = BIT_LEN if os.path.basename(path) == "isolinux.bin" else 0
            e["sha256"] = _sha256_extent(fh, e["lba"], e["size"], skip)
            if skip:
                e["head"] = _sha256_extent(fh, e["lba"], skip)


# One `unsquashfs -lln` line: the type and mode as ls prints them, the numeric owner, the
# size or a device's major and minor, the date and time, and the path -- followed, for a
# symlink, by " -> " and its target.
LLN = re.compile(r"([-dlcbps])([-rwxsStT]{9}) (\d+)/(\d+) +(?:\d+|\d+, *\d+) "
                 r"\d{4}-\d\d-\d\d \d\d:\d\d (.*)")


def _mode(perm: str) -> int:
    """ls's nine permission characters as a mode: 'rwsr-xr-x' is 0o4755."""
    m = sum(1 << (8 - i) for i, ch in enumerate(perm) if ch not in "-ST")
    return (m | (0o4000 if perm[2] in "sS" else 0) | (0o2000 if perm[5] in "sS" else 0)
            | (0o1000 if perm[8] in "tT" else 0))


def _bundle_listing(container: str, offset: int, root: str) -> dict:
    """Every entry of the squashfs at `offset`, as `unsquashfs -lln` lists it under `root`:
    the line's path, with " -> target" for a symlink -> (mode, (uid, gid)).

    THE RECORD, NOT THE EXTRACTION, for the mode and the owner. Unprivileged, unsquashfs
    restores neither: every entry it extracts is the runner's, and it drops setuid and
    setgid bits. Measured 2026-10-23 as uid 1000 with squashfs-tools 4.5.1: a bundle whose
    file changed owner read `identical content`, exit 0, and -rwsr-xr-x came out
    -rwxr-xr-x (#99). The listing reads both from the bundle itself, as any user, and took
    0.05 s for 01-core's 18,670 entries.
    """
    r = subprocess.run(["unsquashfs", "-lln", "-o", str(offset), "-d", root, container],
                       capture_output=True, text=True, encoding=sys.getfilesystemencoding(),
                       errors=sys.getfilesystemencodeerrors())
    if r.returncode != 0:
        why = (r.stderr or r.stdout).strip().splitlines()
        raise ListingError(
            f"unsquashfs could not read the bundle at offset {offset} in "
            f"{os.path.basename(container)}: "
            + (why[0] if why else f"it exited {r.returncode}"))
    out = {}
    for line in r.stdout.splitlines():
        m = LLN.fullmatch(line)
        if m:
            out[m.group(5)] = (_mode(m.group(2)), (int(m.group(3)), int(m.group(4))))
    return out


def bundle_manifest(container: str, offset: int = 0) -> dict:
    """Every entry inside a squashfs, by what it IS: type, mode, owner, size, link target
    and the sha256 of its content. Mtimes are deliberately absent, and so are extended
    attributes: nothing here reads them. The mode and the owner are the bundle's own
    record, from _bundle_listing(); the rest is read from the extracted tree.

    WHY CONTENT AND NOT THE FILE LIST. A squashfs is a container with a creation time of
    its own, and it stores an mtime per file, so two builds of the same tree differ in
    BYTES while every file in them is identical. This compared the file LIST and printed
    "(same file list; contents differ)" whenever the lists matched -- the one answer that
    is true whether or not anything changed. Measured: one stock tree, `enable-ssh`
    applied to two copies, packed; the bundles differed in the superblock's "Creation or
    last append time" and nothing else, and diff called it DIFFERENT. Downstream, two
    builds whose only difference was two comment lines named four bundles as "contents
    differ", three of them identical inside (#30).

    Mtimes are left out for the same reason: they are when the tree was packed, not what
    is in it.

    THIS ONE EXTRACTS, unlike everything else in this module, because a content hash of a
    file inside a squashfs cannot be read from the ISO's extents. Only under --bundles or
    in the container diff, and only for a bundle whose bytes already differ -- but it is
    not free, and the biggest bundle is the one most likely to have changed: 01-core of
    the stock 64-bit Debian ISO is 122.3 MiB and 18,670 entries, and this took 2.75 s for
    it on 2026-09-20. Re-measure rather than trusting that; it is a fact about a machine.

    RAISES ListingError rather than returning an empty manifest. A bundle that could not
    be read is not a bundle with no files -- the same rule _entries() states, and the same
    bug: this used to answer an empty set, and the caller then either skipped the bundle
    in silence or, where only one side failed, reported every file in it as added.
    """
    import tempfile
    tmp = tempfile.mkdtemp(prefix="kitchen-diff-")
    root = os.path.join(tmp, "x")
    try:
        listed = _bundle_listing(container, offset, root)
        r = subprocess.run(["unsquashfs", "-q", "-n", "-o", str(offset), "-d", root,
                            container], capture_output=True, text=True)
        if r.returncode != 0:
            why = (r.stderr or r.stdout).strip().splitlines()
            raise ListingError(
                f"unsquashfs could not read the bundle at offset {offset} in "
                f"{os.path.basename(container)}: "
                + (why[0] if why else f"it exited {r.returncode}"))
        out: dict = {}
        for dirpath, dirnames, filenames in os.walk(root):
            for n in sorted(dirnames + filenames):
                full = os.path.join(dirpath, n)
                st = os.lstat(full)
                # BY st_mode, never by "not a directory, so a file". A bundle may carry
                # device nodes and fifos -- a bundle.script can make them in its chroot --
                # and hashing one would read from the device: a fifo with no writer blocks
                # forever, and /dev/zero never ends.
                if stat.S_ISLNK(st.st_mode):
                    kind = "link"
                elif stat.S_ISDIR(st.st_mode):
                    kind = "dir"
                elif stat.S_ISREG(st.st_mode):
                    kind = "file"
                elif stat.S_ISCHR(st.st_mode):
                    kind = f"chr {os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
                elif stat.S_ISBLK(st.st_mode):
                    kind = f"blk {os.major(st.st_rdev)}:{os.minor(st.st_rdev)}"
                elif stat.S_ISFIFO(st.st_mode):
                    kind = "fifo"
                else:
                    kind = "socket" if stat.S_ISSOCK(st.st_mode) else "other"
                target = os.readlink(full) if kind == "link" else ""
                rec = listed.pop(full + (f" -> {target}" if kind == "link" else ""), None)
                if rec is None:
                    raise ListingError(
                        f"unsquashfs extracted {os.path.relpath(full, root)} from "
                        f"{os.path.basename(container)} and did not list it")
                digest = ""
                if kind == "file":
                    h = hashlib.sha256()
                    with open(full, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                    digest = h.hexdigest()
                out[os.path.relpath(full, root)] = {
                    "type": kind,
                    "mode": rec[0],
                    "owner": rec[1],
                    "size": st.st_size if kind == "file" else 0,
                    "link target": target,
                    "content": digest,
                }
        # EVERY LISTED ENTRY, OR A REFUSAL. One the walk never reached -- a directory
        # the bundle records as d---------, extracted that way and entered by nobody but
        # root -- would be missing from both manifests alike, and they would agree.
        listed.pop(root, None)
        if listed:
            first = sorted(listed)[0]
            raise ListingError(
                f"{len(listed)} entr{'y' if len(listed) == 1 else 'ies'} that unsquashfs "
                f"listed in {os.path.basename(container)} could not be read, "
                f"{os.path.relpath(first.split(' -> ')[0], root)} among them")
        return out
    finally:
        handback.remove_tree(tmp)


def manifest_changes(ma: dict, mb: dict) -> tuple[list, list, list]:
    """(added, removed, changed) between two manifests. `changed` names WHAT differs.

    The words come from the manifest's own keys, so a field added there is reported
    without a second list here needing to learn about it.
    """
    added = sorted(set(mb) - set(ma))
    removed = sorted(set(ma) - set(mb))
    changed = []
    for q in sorted(set(ma) & set(mb)):
        va, vb = ma[q], mb[q]
        if va == vb:
            continue
        changed.append((q, ", ".join(k for k in va if va[k] != vb[k])))
    return added, removed, changed


# What main() and the messages call each kind of input.
KINDS = {"iso": "an ISO image", "squashfs": "a squashfs bundle", "fat12": "a FAT12 image"}


def container_kind(head: bytes) -> str | None:
    """'squashfs' or 'fat12' when `head` -- a file's first 512 bytes -- starts a container
    the container diff can look inside, or None.

    BY CONTENT, NOT BY NAME. A bundle is a squashfs by its magic, and the ESP a FAT12 image
    by isoparse's boot-sector rule. Nothing but convention makes /boot/efi.img the ESP, and
    a recipe can lay down a FAT12 image of its own anywhere.
    """
    if head[:4] == b"hsqs":
        return "squashfs"
    if fat12_layout(head[:512]) is not None:
        return "fat12"
    return None


def file_kind(path: str) -> str | None:
    """'iso', 'squashfs' or 'fat12' for the file at `path`, or None: what main() chooses
    a diff by. The ISO before FAT12, because an isohybrid ISO starts with an MBR, which
    ends in 55 AA as a FAT boot sector does; the volume descriptor at sector 16 decides."""
    with open(path, "rb") as f:
        head = f.read(16 * SECTOR + 6)
    if head[:4] == b"hsqs":
        return "squashfs"
    if head[16 * SECTOR + 1:16 * SECTOR + 6] == b"CD001":
        return "iso"
    return container_kind(head)


def esp_manifest(container: str, offset: int = 0, size: int | None = None) -> tuple[str, dict]:
    """The label and every file of the FAT12 image at `offset` in `container`, each file
    by its size and the sha256 of its content. The volume serial and the times are
    deliberately absent.

    WHY ITS FILES AND NOT ITS BYTES. mkfs.vfat picks a volume serial for every image it
    makes, and each directory entry records when the file was written, so two builds of
    one ESP differ in bytes with every file in them identical. Measured 2026-10-23, three
    shipped UEFI profiles built twice each: each pair of ESPs differed in 20 bytes -- the
    serial, and two bytes of the times in each of eight directory entries -- with the same
    label and the same EFI/BOOT/BOOTX64.EFI (#99).

    Read in memory, not extracted: isoparse.fat12 reads a FAT12 image without mtools, and
    the ESP here is a few MiB. Directories are seen only through the files in them.

    RAISES ListingError rather than returning an empty manifest, as bundle_manifest does:
    an ESP that could not be read is not an ESP with no files.
    """
    with open(container, "rb") as f:
        f.seek(offset)
        data = f.read() if size is None else f.read(size)
    img = fat12(data=data, head=len(data))
    if img is None:
        raise ListingError(f"the FAT12 image at offset {offset} in "
                           f"{os.path.basename(container)} could not be read")
    return img.label, {q: {"size": len(body), "content": hashlib.sha256(body).hexdigest()}
                       for q, body in img.files.items()}


def _manifest(kind: str, container: str, offset: int = 0,
              size: int | None = None) -> tuple[dict, dict]:
    """(facts, manifest) for the container of `kind` at `offset` in `container`: the
    facts about the container as a whole that count as content -- an ESP's label; a
    bundle has none -- and its entries. Raises ListingError as both manifests do."""
    if kind == "squashfs":
        return {}, bundle_manifest(container, offset)
    label, files = esp_manifest(container, offset, size)
    return {"label": label}, files


def _inside(where: str, kind: str, a: tuple, b: tuple, limit: int) -> bool:
    """Print what differs inside one pair of containers, each given as _manifest() gives
    it. True when nothing does: every entry, and every fact, matches."""
    (fa, ma), (fb, mb) = a, b
    unit, one = ("entries", "entry") if kind == "squashfs" else ("files", "file")
    title = f"inside {where}   " if where else f"{'inside':<12}"
    print(f"\n  {title}{len(ma)} -> {len(mb)} {unit}")
    facts = [(k, fa[k], fb.get(k)) for k in fa if fa[k] != fb.get(k)]
    for k, va, vb in facts:
        print(f"    {k:<6} {va!r} -> {vb!r}")
    gained, lost, edited = manifest_changes(ma, mb)
    # Each list stops at --limit, and SAYS SO. These stopped in silence, so a container
    # with more changes of one kind than that listed --limit of them and said nothing of
    # the rest: found reading this for docs/90-reference/diff.md (#99).
    for sign, rows, word in (("+", gained, "added"), ("-", lost, "removed"),
                             ("~", edited, "changed")):
        for r in rows[:limit]:
            print(f"    ~  {r[0]:<48} {r[1]}" if sign == "~" else f"    {sign}  {r}")
        if len(rows) > limit:
            print(f"    ... {len(rows) - limit} more {word}")
    if facts or gained or lost or edited:
        return False
    # The container's bytes differ and nothing in it does. Say which, because this is
    # what two builds of one tree look like and the old message -- "(same file list;
    # contents differ)" -- asserted the opposite.
    #
    # NAMING THE CAUSE WOULD BE A SECOND WRONG ANSWER. What is measured here is that no
    # entry differs; WHY the container's bytes do is not. A rebuild's creation time, or an
    # ESP's volume serial, is the usual reason and the one worth pointing at, but a
    # different compressor or block size, padding, or plain corruption all land here too
    # -- a corrupted byte outside any file's data reaches this line, measured.
    print(f"    (identical content -- every {one} matches; only the"
          " container's own bytes differ, as a rebuild's do)")
    return True


def _header(a: str, b: str) -> None:
    """The two paths and their sizes: how every report here starts."""
    print(f"diff  {a}\n   -> {b}\n")
    sa, sb = os.path.getsize(a), os.path.getsize(b)
    delta = sb - sa
    print(f"  size        {sa:,} -> {sb:,}"
          + (f"   ({'+' if delta > 0 else '-'}{_human(abs(delta))})" if delta else "   (same)"))


def _head(fh, e: dict, n: int = 512) -> bytes:
    """The first n bytes of the file entry `e` describes, and none past its end."""
    fh.seek(e["lba"] * SECTOR)
    return fh.read(min(n, e["size"]))


def diff(a: str, b: str, show_bundles: bool = False, limit: int = 20) -> int:
    """The image diff: two ISOs, by identity, boot structure and every file.

    Under show_bundles (--bundles), each changed file that is a container goes to the
    container diff, and the images are judged by what is inside them (#99): when every
    difference is a container whose contents and mode match, the verdict is `identical
    content`, exit 0. Without it, any byte that differs is DIFFERENT, exit 1.
    """
    for p in (a, b):
        if not os.path.isfile(p):
            print(f"no such file: {p}", file=sys.stderr)
            return 2
    # Listed before anything is printed, so an image that cannot be listed is refused
    # outright rather than halfway through a report about it.
    ea, eb = _entries(a), _entries(b)

    _header(a, b)

    differs = False
    # A container that could not be read at all. Kept apart from `differs`, because the
    # two say different things: the images DID differ, and the question was not fully
    # answered. That is exit 2, the status this file already uses for a refusal.
    unreadable = False

    # --- identity and structure -------------------------------------------------
    ident = []
    with IsoReader(a) as ra, IsoReader(b) as rb:
        ia, ib = ra.info(), rb.info()
        for f in ("volume_id", "system_id", "application_id", "publisher_id",
                  "preparer_id", "rock_ridge", "joliet"):
            va, vb = getattr(ia, f, None), getattr(ib, f, None)
            if va != vb:
                ident.append((f, va, vb))
        boot = []
        def _cat(info):
            # Each platform contributes a validation entry plus its bootable one, so
            # report the platforms -- "2 -> 4 entries" says nothing a reader can use.
            plats = []
            for e in info.boot_entries:
                if e.platform_name not in plats:
                    plats.append(e.platform_name)
            n = len(info.boot_entries)
            return f"{', '.join(plats)}  ({n} {'entry' if n == 1 else 'entries'})"
        ka = [(e.platform_name, e.bootable) for e in ia.boot_entries]
        kb = [(e.platform_name, e.bootable) for e in ib.boot_entries]
        if ka != kb:
            boot.append(("El Torito", _cat(ia), _cat(ib)))
        for f, label in (("isohybrid_mbr", "isohybrid MBR"), ("gpt", "GPT")):
            va, vb = getattr(ia, f), getattr(ib, f)
            if va != vb:
                boot.append((label, "absent" if not va else "present",
                             "absent" if not vb else "present"))
        bta, btb = ia.boot_info_table, ib.boot_info_table

    def _short(v, n=46):
        # xorriso writes its own version banner into preparer_id, which would otherwise
        # push every other column off the terminal.
        t = repr(v)
        return t if len(t) <= n else t[:n - 4] + "..." + t[-1]

    if ident:
        differs = True
        print()
        for i, (f, va, vb) in enumerate(ident):
            print(f"  {'identity' if i == 0 else '':<11} {f:<15} {_short(va)} -> {_short(vb)}")
    if boot:
        differs = True
        print()
        for i, (f, va, vb) in enumerate(boot):
            print(f"  {'boot' if i == 0 else '':<11} {f:<15} {va} -> {vb}")

    # --- files ------------------------------------------------------------------
    _hash_all(a, ea)
    _hash_all(b, eb)

    added = sorted(set(eb) - set(ea))
    removed = sorted(set(ea) - set(eb))
    changed, bit_only = [], []
    for p in sorted(set(ea) & set(eb)):
        x, y = ea[p], eb[p]
        # One path, one line. A file whose content AND mode both moved is a single
        # changed entry with both facts on it, not two rows and a doubled count.
        why = []
        if x["type"] != y["type"]:
            why.append(f"{x['type']} -> {y['type']}")
        elif x["type"] == "link":
            if x["target"] != y["target"]:
                why.append(f"-> {x['target']} => {y['target']}")
        elif x["type"] == "file":
            if x.get("sha256") != y.get("sha256"):
                why.append("content" if x["size"] == y["size"]
                           else f"{x['size']:,} -> {y['size']:,} B")
            elif x.get("head") is not None and x["head"] != y["head"]:
                bit_only.append(p)
        if x["mode"] != y["mode"]:
            why.append(f"mode {x['mode']} -> {y['mode']}")
        if why:
            changed.append((p, ", ".join(why)))

    def _size(e):
        return "(dir)" if e["type"] == "dir" else _human(e["size"])

    nfa, nfb = len(ea), len(eb)
    print()
    if added or removed or changed:
        differs = True
        bits = []
        if added:
            bits.append(f"+{len(added)} added")
        if removed:
            bits.append(f"-{len(removed)} removed")
        if changed:
            bits.append(f"{len(changed)} changed")
        print(f"  entries     {nfa} -> {nfb}   ({', '.join(bits)})")
        shown = 0
        for p in added:
            print(f"    +  {p:<48} {_size(eb[p])}")
            shown += 1
        for p in removed:
            print(f"    -  {p:<48} {_size(ea[p])}")
            shown += 1
        for p, what in changed:
            if shown >= limit:
                print(f"    ... {len(added) + len(removed) + len(changed) - shown} more")
                break
            print(f"    ~  {p:<48} {what}")
            shown += 1
    else:
        print(f"  entries     {nfa} entries, all identical")

    # The one difference that is expected rather than suspicious: the mastering tool
    # rewrites this table whenever the file moves, so say so instead of "content".
    for p in bit_only:
        ok = "checksum ok" if (btb and btb["self_consistent"]) else "CHECKSUM BAD"
        print(f"    =  {p:<48} boot-info-table only ({ok})")
    if bta and btb and bta["file_lba"] != btb["file_lba"]:
        print(f"       {'':<48} moved LBA {bta['file_lba']} -> {btb['file_lba']}")

    # --- inside containers ------------------------------------------------------
    # The changed entries whose every difference is their container's own bytes: the
    # contents match, and so does the mode. Under --bundles these alone do not make the
    # images DIFFERENT, and nothing else does that either (#99).
    rebuilt = set()
    if show_bundles:
        with open(a, "rb") as fha, open(b, "rb") as fhb:
            for p, _ in changed:
                x, y = ea[p], eb[p]
                # Only a container whose BYTES differ has an inside worth reading. One
                # whose mode alone changed was unpacked twice, and said "only the
                # container's own bytes differ" of two identical files (measured
                # 2026-10-23); its mode line says all there is.
                if (x["type"] != "file" or y["type"] != "file"
                        or x["lba"] is None or y["lba"] is None
                        or x.get("sha256") == y.get("sha256")):
                    continue
                # A .sb is a bundle by its name as well as its magic: one whose magic is
                # gone is a bundle nobody can read, exit 2 -- not a file that changed.
                if p.endswith(".sb"):
                    kx = ky = "squashfs"
                else:
                    kx, ky = container_kind(_head(fha, x)), container_kind(_head(fhb, y))
                if kx is None and ky is None:
                    continue
                if kx != ky:
                    print(f"\n  inside {p}   not compared: "
                          f"{KINDS.get(kx, 'not a container')} -> "
                          f"{KINDS.get(ky, 'not a container')}")
                    continue
                # CAUGHT PER CONTAINER. Both manifests refuse rather than answering an
                # empty one, which is right -- but letting that out of here printed the
                # whole file-level report and then stopped with no verdict at all, which
                # is the "halfway through a report" this function's own opening refuses to
                # do for an image it cannot list. One container nobody can read is not a
                # reason to withhold the answer about every other one.
                try:
                    ma = _manifest(kx, a, x["lba"] * SECTOR, x["size"])
                    mb = _manifest(ky, b, y["lba"] * SECTOR, y["size"])
                except ListingError as e:
                    print(f"\n  inside {p}   could not be read")
                    print(f"    {e}")
                    unreadable = True
                    continue
                if _inside(p, kx, ma, mb, limit) and x["mode"] == y["mode"]:
                    rebuilt.add(p)

    content_only = (show_bundles and differs and not ident and not boot
                    and not added and not removed and all(p in rebuilt for p, _ in changed))
    print()
    print("  identical" if not differs
          else "  identical content" if content_only else "  DIFFERENT")
    if unreadable:
        print("  (one or more containers could not be read -- see above)")
    return 2 if unreadable else (1 if differs and not content_only else 0)


def diff_containers(a: str, b: str, kind: str, limit: int = 20) -> int:
    """The container diff: two bundles, or two FAT12 images such as the ESP, compared by
    what is inside them (#99) -- the comparison --bundles makes of each changed container
    in an image, run on two that are not in one. For a bundle from two work trees, say.

    0 when the contents match: `identical` when the bytes do too, `identical content`
    when only the containers' own bytes differ. 1 when the contents differ.

    RAISES ListingError when either cannot be read, before anything is printed -- main()
    refuses it, exit 2, as diff() refuses an image it cannot list.
    """
    same = filecmp.cmp(a, b, shallow=False)
    if not same:
        ma, mb = _manifest(kind, a), _manifest(kind, b)
    _header(a, b)
    print(f"  kind        {KINDS[kind]}")
    if same:
        print("\n  identical")
        return 0
    content_only = _inside("", kind, ma, mb, limit)
    print()
    print("  identical content" if content_only else "  DIFFERENT")
    return 0 if content_only else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="kitchen diff",
        description="compare two ISO images -- identity, boot structure and every file -- "
                    "or two bundles, or two FAT12 images such as the ESP, by what is "
                    "inside them; docs/90-reference/diff.md has the detail")
    ap.add_argument("a", help="an ISO image, a squashfs bundle or a FAT12 image")
    ap.add_argument("b", help="another of the same kind")
    ap.add_argument("--bundles", action="store_true",
                    help="two ISOs: also compare what is inside each changed bundle or "
                         "FAT12 image, and judge the images by that")
    ap.add_argument("--limit", type=int, default=20, metavar="N",
                    help="stop listing changed paths after N, and say how many more "
                         "(default 20)")
    args = ap.parse_args(argv[1:])
    kinds = []
    for p in (args.a, args.b):
        if not os.path.isfile(p):
            print(f"no such file: {p}", file=sys.stderr)
            return 2
        try:
            kinds.append(file_kind(p))
        except OSError as e:
            print(f"kitchen diff: {p}: {e.strerror}", file=sys.stderr)
            return 2
        if kinds[-1] is None:
            print(f"kitchen diff: {p} is not an ISO image, a squashfs bundle or a FAT12 image",
                  file=sys.stderr)
            return 2
    ka, kb = kinds
    if ka != kb:
        # Two of a kind, or nothing: an image's files against one bundle's entries is not
        # a comparison either diff can make.
        print(f"kitchen diff: {args.a} is {KINDS[ka]} and {args.b} is {KINDS[kb]}; "
              "it compares two ISO images, two bundles or two FAT12 images", file=sys.stderr)
        return 2
    try:
        if ka == "iso":
            need.require(["xorriso"] + (["unsquashfs"] if args.bundles else []),
                         "kitchen diff")
            return diff(args.a, args.b, args.bundles, args.limit)
        # A FAT12 image needs no tool: isoparse reads it.
        need.require(["unsquashfs"] if ka == "squashfs" else [], "kitchen diff")
        return diff_containers(args.a, args.b, ka, args.limit)
    except ListingError as e:
        # An image that could not be listed used to diff as an image with no files --
        # every file of the other reported removed, or added.
        print(f"kitchen diff: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # TERM and HUP as exits, so the `finally` takes --bundles' unpacked bundle away. Python's
    # default TERM left 344 MiB of it in TMPDIR, and HUP 489 MiB (#98). Nothing is given
    # back: diff writes nothing in the user's tree.
    raise SystemExit(handback.run(main, sys.argv, []))
