#!/usr/bin/env python3
"""kitchen diff -- what actually changed between two ISOs.

`probe` answers "is this a known release, and has it been modified?" against the
fingerprints in compat/. This answers the different question "what is the difference
between THESE two images?", for the case where both are yours: a stock ISO and your
build of it, or two of your builds a recipe apart.

NOTHING IS EXTRACTED, except one case that says so. xorriso reports each file's start
LBA and size, so the content hash comes from reading that extent straight out of the
image. Extracting two 416 MiB ISOs to compare 38 files would move ~830 MiB through the
filesystem to answer a question that needs ~830 MiB of *reads* and no writes at all. On
this machine the difference is about 40 s versus about 6 s, and it needs no scratch
space.

The exception is bundle_manifest(), under --bundles only: a content hash of a file INSIDE
a squashfs cannot be read from the ISO's extents, and comparing the file list instead is
what made two builds of one tree indistinguishable from two that really differ.

TWO SPECIAL CASES, both of which would otherwise produce a confidently wrong answer:

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
"""
import argparse
import hashlib
import os
import re
import stat
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import need  # noqa: E402
from isoparse import IsoReader  # noqa: E402

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
    `kitchen sources --strict`, which accounts for the files it is given, passed an image
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


def bundle_manifest(container: str, offset: int = 0) -> dict:
    """Every entry inside a squashfs, by what it IS: type, mode, owner, size, link target
    and the sha256 of its content. Mtimes are deliberately absent.

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
    file inside a squashfs cannot be read from the ISO's extents. Only under --bundles,
    and only for a bundle whose bytes already differ -- but it is not free, and the
    biggest bundle is the one most likely to have changed: 01-core of the stock 64-bit
    Debian ISO is 122.3 MiB and 18,670 entries, and this took 2.75 s for it on
    2026-09-20. Re-measure rather than trusting that; it is a fact about a machine.

    RAISES ListingError rather than returning an empty manifest. A bundle that could not
    be read is not a bundle with no files -- the same rule _entries() states, and the same
    bug: this used to answer an empty set, and the caller then either skipped the bundle
    in silence or, where only one side failed, reported every file in it as added.
    """
    import shutil
    import tempfile
    tmp = tempfile.mkdtemp(prefix="kitchen-diff-")
    root = os.path.join(tmp, "x")
    try:
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
                # device nodes and fifos -- bundle.fromTarball admits them under
                # privilege: mknod -- and hashing one would read from the device: a fifo
                # with no writer blocks forever, and /dev/zero never ends.
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
                digest = ""
                if kind == "file":
                    h = hashlib.sha256()
                    with open(full, "rb") as fh:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
                    digest = h.hexdigest()
                out[os.path.relpath(full, root)] = {
                    "type": kind,
                    "mode": stat.S_IMODE(st.st_mode),
                    "owner": (st.st_uid, st.st_gid),
                    "size": st.st_size if kind == "file" else 0,
                    "link target": os.readlink(full) if kind == "link" else "",
                    "content": digest,
                }
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


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


def diff(a: str, b: str, show_bundles: bool = False, limit: int = 20) -> int:
    for p in (a, b):
        if not os.path.isfile(p):
            print(f"no such file: {p}", file=sys.stderr)
            return 2
    # Listed before anything is printed, so an image that cannot be listed is refused
    # outright rather than halfway through a report about it.
    ea, eb = _entries(a), _entries(b)

    print(f"diff  {a}\n   -> {b}\n")

    sa, sb = os.path.getsize(a), os.path.getsize(b)
    delta = sb - sa
    print(f"  size        {sa:,} -> {sb:,}"
          + (f"   ({'+' if delta > 0 else '-'}{_human(abs(delta))})" if delta else "   (same)"))

    differs = False
    # A bundle that could not be read at all. Kept apart from `differs`, because the two
    # say different things: the images DID differ, and the question was not fully
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
        # xorriso writes its own 76-character version banner into preparer_id, which
        # would otherwise push every other column off the terminal.
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

    # --- inside bundles ---------------------------------------------------------
    if show_bundles:
        bundles = [p for p, _ in changed if p.endswith(".sb")]
        for p in bundles:
            # CAUGHT PER BUNDLE. bundle_manifest refuses rather than answering an empty
            # manifest, which is right -- but letting that out of here printed the whole
            # file-level report and then stopped with no verdict at all, which is the
            # "halfway through a report" this function's own opening refuses to do for an
            # image it cannot list. One bundle nobody can read is not a reason to withhold
            # the answer about every other one.
            try:
                ma = bundle_manifest(a, ea[p]["lba"] * SECTOR)
                mb = bundle_manifest(b, eb[p]["lba"] * SECTOR)
            except ListingError as e:
                print(f"\n  inside {p}   could not be read")
                print(f"    {e}")
                unreadable = True
                continue
            gained, lost, edited = manifest_changes(ma, mb)
            print(f"\n  inside {p}   {len(ma)} -> {len(mb)} entries")
            for q in gained[:limit]:
                print(f"    +  {q}")
            for q in lost[:limit]:
                print(f"    -  {q}")
            for q, why in edited[:limit]:
                print(f"    ~  {q:<48} {why}")
            if not gained and not lost and not edited:
                # The bundle's bytes differ and nothing in it does. Say which, because
                # this is what two builds of one tree look like and the old message --
                # "(same file list; contents differ)" -- asserted the opposite.
                #
                # NAMING THE CAUSE WOULD BE A SECOND WRONG ANSWER. What is measured here
                # is that no entry differs; WHY the container's bytes do is not. A
                # rebuild's creation time is the usual reason and the one worth pointing
                # at, but a different compressor or block size, padding, or plain
                # corruption all land here too -- a corrupted byte outside any file's
                # data reaches this line, measured.
                print("    (identical content -- every entry matches; only the"
                      " container's own bytes differ, as a rebuild's do)")

    print()
    print("  identical" if not differs else "  DIFFERENT")
    if unreadable:
        print("  (one or more bundles could not be read -- see above)")
    return 2 if unreadable else (1 if differs else 0)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="kitchen diff",
        description="compare two ISOs: identity, boot structure, and every file")
    ap.add_argument("a")
    ap.add_argument("b")
    ap.add_argument("--bundles", action="store_true",
                    help="also list what changed inside differing .sb bundles")
    ap.add_argument("--limit", type=int, default=20,
                    help="max changed paths to print (default 20)")
    args = ap.parse_args(argv[1:])
    need.require(["xorriso"] + (["unsquashfs"] if args.bundles else []), "kitchen diff")
    try:
        return diff(args.a, args.b, args.bundles, args.limit)
    except ListingError as e:
        # An image that could not be listed used to diff as an image with no files --
        # every file of the other reported removed, or added.
        print(f"kitchen diff: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
