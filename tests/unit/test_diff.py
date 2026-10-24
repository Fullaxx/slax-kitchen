#!/usr/bin/env python3
"""Unit tests for `kitchen diff`'s containers: bundles, and FAT12 images such as the ESP.

A squashfs is a container with a creation time of its own, and it stores an mtime per
file, so two builds of the same tree differ in BYTES while every file in them is
identical. `diff --bundles` compared the file LIST only and said "(same file list;
contents differ)" either way -- the one answer that is true whether or not anything
changed, which made two builds a recipe apart exactly the case it could not report.
Issue #30.

The ESP is the same case in FAT12: a volume serial and directory times, new with every
build. And the verdict still counted each container's bytes, so no rebuild could end
anything but DIFFERENT. The container diff compares two of either by what is inside, and
the image diff hands its changed containers to it under --bundles. Issue #99.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "kdiff", os.path.join(_HERE, "..", "..", "lib", "diff.py"))
kd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kd)

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def _have_tools(what: str) -> bool:
    """A REQUIRED TOOL's absence is a named failure, never a silent pass: `kitchen doctor`
    asserts both of these, and a check that cannot fail is worse than no check."""
    missing = [t for t in ("mksquashfs", "unsquashfs") if not shutil.which(t)]
    if not missing:
        return True
    FAILURES.append(f"{', '.join(missing)} not installed, so {what} cannot be tested "
                    f"(required tools -- see kitchen doctor)")
    return False


# A stub xorriso serving canned answers, and an image written here rather than mastered.
# BOTH COPIED FROM tests/unit/test_listing.py, deliberately and not factored out: unit test
# files in this tree are standalone -- none imports another, and each runs directly with
# `python3 tests/unit/<file>.py`. What is shared is the shape, not the code.
#
# THE REASON IS CI. The gates job installs containers/packages/lint.txt and nothing else --
# shellcheck, yamllint, python3-yaml, python3-jsonschema -- and squashfs-tools only comes
# free from the ubuntu-24.04 runner image. xorriso is in neither. This test used to build
# two real ISOs with `xorriso -as mkisofs` and took the whole gate down with
# `FileNotFoundError: 'xorriso'` (run 35548115254), while passing here, where xorriso is
# installed. docs/60-testing/ci.md calls that job "~1 min, no ISOs".
STUB_XORRISO = r"""#!/bin/sh
for a in "$@"; do
    case "$a" in
        lsdl) cat "$STUB_DIR/lsdl" 2>/dev/null ;;
        report_lba) cat "$STUB_DIR/lba" 2>/dev/null ;;
    esac
done
exit 0
"""

SECTOR = 2048


def _stub_xorriso(tmp: str, path: str, size: int, lba: int) -> str:
    """A directory holding the stub and the one file it will report. Returns the dir."""
    d = os.path.join(tmp, "stub")
    os.makedirs(d, exist_ok=True)
    exe = os.path.join(d, "xorriso")
    with open(exe, "w") as f:
        f.write(STUB_XORRISO)
    os.chmod(exe, 0o755)
    dirs = ("/", "/slax", "/slax/modules")
    with open(os.path.join(d, "lsdl"), "w") as f:
        f.write("".join(f"drwxr-xr-x    1 0 0    0 Sep 19 14:37 '{x}'\n" for x in dirs)
                + f"-rw-r--r--    1 0 0 {size} Sep 19 14:37 '{path}'\n")
    with open(os.path.join(d, "lba"), "w") as f:
        f.write("Report layout: xt , Startlba ,   Blocks , Filesize , ISO image path\n"
                f"File data lba:  0 ,       {lba} ,        {size // SECTOR + 1} ,       "
                f"{size} , '{path}'\n")
    return d


def _iso_carrying(path: str, payload: bytes, lba: int) -> None:
    """A primary volume descriptor and a terminator -- enough for IsoReader to open it --
    with `payload` laid down at `lba`, so _hash_all reads a real extent and
    bundle_manifest has a real squashfs to open at that offset."""
    img = bytearray(SECTOR * 24)
    pvd = bytearray(SECTOR)
    pvd[0], pvd[1:6], pvd[6] = 1, b"CD001", 1
    struct.pack_into("<I", pvd, 80, 24)
    struct.pack_into("<H", pvd, 128, SECTOR)
    struct.pack_into("<I", pvd, 158, 23)
    struct.pack_into("<I", pvd, 166, SECTOR)
    img[16 * SECTOR:17 * SECTOR] = pvd
    img[17 * SECTOR] = 255
    img[17 * SECTOR + 1:17 * SECTOR + 6] = b"CD001"
    img += bytearray(lba * SECTOR - len(img))
    img += payload
    with open(path, "wb") as f:
        f.write(bytes(img))


def _sb(src: str, path: str, mkfs_time: str, uid: int = 0) -> None:
    """Pack `src`, stamping the superblock rather than waiting for the clock to move.

    -mkfs-time, not time.sleep(1.1): the point is two containers that differ while their
    contents do not, and sleeping for it costs the gate a second at every commit and every
    push AND makes the fixture depend on wall-clock resolution. Every entry is owned by
    `uid`, user and group, which mksquashfs records without the runner being root.
    """
    subprocess.run(["mksquashfs", src, path, "-noappend", "-no-progress",
                    "-force-uid", str(uid), "-force-gid", str(uid),
                    "-mkfs-time", mkfs_time], capture_output=True, check=True)


def _fat12(label: str, files: dict, serial: int = 0, stamp: int = 0) -> bytes:
    """A FAT12 image laid out as mkfs.vfat -F 12 and mtools lay one out, only smaller:
    512-byte sectors and clusters, one reserved sector, two FATs and a 16-entry root
    directory. `files` maps paths such as EFI/BOOT/BOOTX64.EFI to their bytes, and every
    directory on the way is made. `label` goes where mkfs.vfat -n puts it: the boot sector
    and a root entry.

    COPIED FROM tests/unit/test_apply.py's fat12_image(), as the stub above is copied, with
    what a rebuild changes added: `serial`, the volume serial mkfs.vfat picks for every
    image, and `stamp`, the time every directory entry records. Two builds of one ESP
    differed in exactly those, measured (#99). Built here because CI's gates job has no
    mkfs.vfat, and the repository takes no disk images."""
    sec = 512
    dirs = sorted({"/".join(p.split("/")[:i])
                   for p in files for i in range(1, p.count("/") + 1)})
    first, chain, c = {}, {}, 2
    for d in dirs:                              # a cluster each: sixteen entries is plenty
        first[d], chain[c] = c, 0xFFF
        c += 1
    for p, data in files.items():
        n = max(1, -(-len(data) // sec))
        first[p] = c
        for i in range(n):
            chain[c + i] = c + i + 1 if i < n - 1 else 0xFFF
        c += n
    spf = -(-(c * 3 // 2 + 2) // sec)
    root_at = sec * (1 + 2 * spf)
    data_at = root_at + sec
    img = bytearray(data_at + (c - 2) * sec)
    img[0:11] = b"\xeb\x3c\x90mkfs.fat"
    struct.pack_into("<HBHBHHBH", img, 11, sec, 1, 1, 2, 16, len(img) // sec, 0xF8, spf)
    struct.pack_into("<HH", img, 24, 32, 2)     # a disk geometry, as mkfs.vfat writes one
    img[38] = 0x29
    struct.pack_into("<I", img, 39, serial)
    img[43:54] = (label or "NO NAME").ljust(11).encode()
    img[54:62] = b"FAT12   "
    img[510:512] = b"\x55\xaa"
    fat = bytearray(spf * sec)
    fat[0:3] = b"\xf8\xff\xff"
    for n, v in chain.items():
        i = n + n // 2
        if n & 1:
            fat[i] = fat[i] & 0x0F | v << 4 & 0xF0
            fat[i + 1] = v >> 4 & 0xFF
        else:
            fat[i] = v & 0xFF
            fat[i + 1] = fat[i + 1] & 0xF0 | v >> 8 & 0x0F
    img[sec:sec + len(fat)] = fat
    img[sec + len(fat):sec + 2 * len(fat)] = fat

    def entry(name, attr, cluster=0, size=0):
        base, _, ext = name.partition(".")
        whole = attr == 0x08 or name in (".", "..")     # a label, and the dot entries
        name11 = name.ljust(11) if whole else base.ljust(8) + ext.ljust(3)
        when = struct.pack("<I", stamp)                 # created, and last written
        return (name11.encode() + bytes([attr]) + bytes(2) + when + bytes(4) + when
                + struct.pack("<HI", cluster, size))

    def under(parent):
        return b"".join(
            [entry(d.rsplit("/", 1)[-1], 0x10, first[d]) for d in dirs
             if d.rpartition("/")[0] == parent]
            + [entry(p.rsplit("/", 1)[-1], 0x20, first[p], len(data))
               for p, data in files.items() if p.rpartition("/")[0] == parent])

    def put(at, blob):
        img[at:at + len(blob)] = blob
    put(root_at, (entry(label, 0x08) if label else b"") + under(""))
    for d in dirs:
        put(data_at + (first[d] - 2) * sec,
            entry(".", 0x10, first[d])
            + entry("..", 0x10, first.get(d.rpartition("/")[0], 0)) + under(d))
    for p, data in files.items():
        put(data_at + (first[p] - 2) * sec, data)
    return bytes(img)


def _run(argv: list, stub: str | None = None) -> tuple:
    """lib/diff.py's main() on `argv`, in this process: (status, stdout, stderr).

    In-process, so a run costs no interpreter start. With `stub`, the stub xorriso in that
    directory goes first on PATH -- and comes off again in finally: a test that leaves PATH
    rewritten would break every file after it in the same run."""
    import contextlib
    import io
    old_path, old_dir = os.environ.get("PATH", ""), os.environ.get("STUB_DIR")
    if stub:
        os.environ["PATH"] = stub + os.pathsep + old_path
        os.environ["STUB_DIR"] = stub
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = kd.main(["kitchen diff"] + argv)
            except SystemExit as e:             # need.require refuses this way
                rc = e.code
    finally:
        os.environ["PATH"] = old_path
        if old_dir is None:
            os.environ.pop("STUB_DIR", None)
        else:
            os.environ["STUB_DIR"] = old_dir
    return rc, out.getvalue(), err.getvalue()


def _verdict(out: str) -> str:
    """A report's verdict: its last line."""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def test_a_rebuild_is_not_a_change():
    """The same tree, packed twice: every file identical, the containers not."""
    if not _have_tools("a rebuild against itself"):
        return
    src = tempfile.mkdtemp()
    os.makedirs(os.path.join(src, "etc"))
    with open(os.path.join(src, "etc", "conf"), "w") as f:
        f.write("one\n")
    os.symlink("conf", os.path.join(src, "etc", "link"))
    # A FIFO, because entries are classified by st_mode and never opened: a fifo with no
    # writer blocks forever, so a regression here HANGS rather than fails. bundles really
    # can carry one -- a bundle.script can make fifos and device nodes in its chroot.
    os.mkfifo(os.path.join(src, "etc", "pipe"))

    d = tempfile.mkdtemp()
    a, b = os.path.join(d, "a.sb"), os.path.join(d, "b.sb")
    _sb(src, a, "1000")
    _sb(src, b, "2000")

    check("the two bundles differ in bytes",
          open(a, "rb").read() != open(b, "rb").read(), True)
    ma, mb = kd.bundle_manifest(a), kd.bundle_manifest(b)
    check("a manifest was read", len(ma) > 0, True)
    check("the fifo is classified, not opened", ma["etc/pipe"]["type"], "fifo")
    check("the symlink records its target", ma["etc/link"]["link target"], "conf")
    check("nothing in them differs", kd.manifest_changes(ma, mb), ([], [], []))


def test_a_change_is_named():
    """One file's content, one file's mode, one added, one removed."""
    if not _have_tools("a change inside a bundle"):
        return
    d = tempfile.mkdtemp()
    src_a, src_b = os.path.join(d, "a"), os.path.join(d, "b")
    for s in (src_a, src_b):
        os.makedirs(os.path.join(s, "etc"))
        with open(os.path.join(s, "etc", "keep"), "w") as f:
            f.write("same\n")
        with open(os.path.join(s, "etc", "conf"), "w") as f:
            f.write("one\n" if s == src_a else "two\n")
        with open(os.path.join(s, "etc", "prog"), "w") as f:
            f.write("#!/bin/sh\n")
        os.chmod(os.path.join(s, "etc", "prog"), 0o644 if s == src_a else 0o755)
    # ...and one whose length changes too, so both halves of "size, content" are covered.
    # etc/conf above is deliberately the SAME LENGTH on both sides -- "one" and "two" --
    # because that is the change a size comparison cannot see and a hash can.
    for s, body in ((src_a, "short\n"), (src_b, "rather longer than it was\n")):
        with open(os.path.join(s, "etc", "grew"), "w") as f:
            f.write(body)
    with open(os.path.join(src_a, "etc", "gone"), "w") as f:
        f.write("x\n")
    with open(os.path.join(src_b, "etc", "new"), "w") as f:
        f.write("y\n")
    a, b = os.path.join(d, "a.sb"), os.path.join(d, "b.sb")
    _sb(src_a, a, "1000")
    _sb(src_b, b, "1000")

    added, removed, changed = kd.manifest_changes(kd.bundle_manifest(a),
                                                  kd.bundle_manifest(b))
    check("the added file is named", added, ["etc/new"])
    check("the removed file is named", removed, ["etc/gone"])
    # conf changed content at the same size, grew changed both, prog changed only its
    # mode -- and keep is absent, which is the mtime NOT registering as a change.
    check("what changed is named, and nothing else is",
          changed, [("etc/conf", "content"),
                    ("etc/grew", "size, content"),
                    ("etc/prog", "mode")])


def test_a_bundle_that_cannot_be_read_is_refused():
    """An unreadable bundle is not a bundle with no files.

    _bundle_paths answered an empty set when unsquashfs failed, and the caller then either
    skipped the bundle in silence or -- where only one side failed -- reported every file
    in it as added or removed. The same bug 69435b3 took out of _entries(), two functions
    away in the same file, and left behind here.
    """
    if not _have_tools("an unreadable bundle"):
        return
    src = tempfile.mkdtemp()
    os.makedirs(os.path.join(src, "etc"))
    with open(os.path.join(src, "etc", "conf"), "w") as f:
        f.write("one\n")
    d = tempfile.mkdtemp()
    sb = os.path.join(d, "a.sb")
    _sb(src, sb, "1000")
    try:
        kd.bundle_manifest(sb, 7)              # an offset that is not a squashfs
        check("an unreadable bundle raises", "no exception", "ListingError")
    except kd.ListingError as e:
        check("the refusal names the bundle", "a.sb" in str(e), True)


def test_an_unreadable_bundle_does_not_abandon_the_report():
    """One bundle nobody can read must not cost the verdict on everything else.

    bundle_manifest raises rather than answering an empty manifest, which is right -- but
    the exception went all the way out of diff(), so the file-level report printed and
    then stopped with no `DIFFERENT` or `identical` line at all. diff()'s own comment says
    an image that cannot be listed is "refused outright rather than halfway through a
    report about it", and this was halfway.

    Still exit 2, not 1: the images did differ, and the question was not fully answered.
    docs/90-reference/cli.md already promised that status for this case.
    """
    if not _have_tools("an unreadable bundle inside a report"):
        return
    import contextlib
    import io
    src = tempfile.mkdtemp()
    os.makedirs(os.path.join(src, "etc"))
    with open(os.path.join(src, "etc", "conf"), "w") as f:
        f.write("one\n")
    d = tempfile.mkdtemp()
    sb = os.path.join(d, "x.sb")
    _sb(src, sb, "1000")
    with open(sb, "rb") as fh:
        good = fh.read()
    # The same bundle with its squashfs magic zeroed: unsquashfs cannot read it, and the
    # extents still differ, which is what puts it in the bundle loop rather than skipping
    # it as unchanged.
    bad = b"\x00\x00\x00\x00" + good[4:]

    lba, rel = 24, "/slax/modules/08-ssh.sb"
    a, b = os.path.join(d, "a.iso"), os.path.join(d, "b.iso")
    _iso_carrying(a, good, lba)
    _iso_carrying(b, bad, lba)
    stub = _stub_xorriso(d, rel, len(good), lba)

    # In-process, so the stub goes on this process's PATH rather than a child's env --
    # test_listing.py drives `diff` as a subprocess and hands it one. Restored in finally:
    # a test that leaves PATH rewritten would break every file after it in the same run.
    old_path, old_dir = os.environ.get("PATH", ""), os.environ.get("STUB_DIR")
    os.environ["PATH"] = stub + os.pathsep + old_path
    os.environ["STUB_DIR"] = stub
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = kd.diff(a, b, show_bundles=True)
    finally:
        os.environ["PATH"] = old_path
        if old_dir is None:
            os.environ.pop("STUB_DIR", None)
        else:
            os.environ["STUB_DIR"] = old_dir

    text = out.getvalue()
    check("the run is refused", rc, 2)
    check("the bundle is named as unreadable", "could not be read" in text, True)
    check("...and the report still reaches its verdict", "DIFFERENT" in text, True)


def test_a_term_during_bundles_takes_the_unpacked_bundle_away():
    """`diff --bundles` unpacks each changed bundle into a tree under TMPDIR, and removes it
    in a `finally`. Python's default TERM runs no `finally`. A TERM while it unpacked a
    52,903-entry bundle left 344 MiB there, and HUP left 489 MiB (#98, measured). diff.py
    runs main through handback.run now, which makes both exits.

    lib/diff.py runs as kitchen runs it, on two images whose one bundle differs. It uses the
    stub xorriso, and a stand-in unsquashfs that sends its parent TERM while the tree is
    there. The real one cannot pick that moment."""
    if not _have_tools("a TERM during --bundles"):
        return
    import signal
    src = tempfile.mkdtemp()
    d = tempfile.mkdtemp()
    sbs = []
    for word in ("one", "two"):
        with open(os.path.join(src, "conf"), "w") as f:
            f.write(word + "\n")
        sbs.append(os.path.join(d, word + ".sb"))
        _sb(src, sbs[-1], "1000")
    lba, rel = 24, "/slax/modules/08-x.sb"
    a, b = os.path.join(d, "a.iso"), os.path.join(d, "b.iso")
    for iso, sb in ((a, sbs[0]), (b, sbs[1])):
        with open(sb, "rb") as fh:
            _iso_carrying(iso, fh.read(), lba)
    stub = _stub_xorriso(d, rel, os.path.getsize(sbs[0]), lba)
    with open(os.path.join(stub, "unsquashfs"), "w") as f:
        f.write("#!/bin/sh\nkill -TERM $PPID\nsleep 5\n")
    os.chmod(os.path.join(stub, "unsquashfs"), 0o755)
    tmp = os.path.join(d, "tmp")
    os.makedirs(tmp)
    env = dict(os.environ, PATH=stub + os.pathsep + os.environ.get("PATH", ""), STUB_DIR=stub,
               TMPDIR=tmp)
    r = subprocess.run([sys.executable, os.path.join(_HERE, "..", "..", "lib", "diff.py"),
                        a, b, "--bundles"], env=env, capture_output=True, text=True, timeout=60)
    check("a TERM while a bundle is unpacked ends diff as an exit, 143", r.returncode,
          128 + signal.SIGTERM)
    check("...and the unpacked bundle is gone from TMPDIR", os.listdir(tmp), [])


def test_two_bundles_compare_by_content():
    """`kitchen diff a.sb b.sb`: the container diff, on two bundles that are in no image --
    one bundle from two work trees, say.

    Nothing compared two bundles. kitchen diff listed a squashfs as an ISO and refused it,
    "xorriso listed no files in 07-extras.sb", exit 2, for the one bundle built in two
    copies of the tree (#99, measured 2026-10-23). Each input is told by its content now.
    """
    if not _have_tools("two bundles compared directly"):
        return
    d = tempfile.mkdtemp()
    src_a, src_b = os.path.join(d, "a"), os.path.join(d, "b")
    for s, word in ((src_a, "one"), (src_b, "two")):
        os.makedirs(os.path.join(s, "etc"))
        with open(os.path.join(s, "etc", "conf"), "w") as f:
            f.write(word + "\n")
    for n in ("new1", "new2"):
        with open(os.path.join(src_b, "etc", n), "w") as f:
            f.write(n + "\n")
    # The same files again, with conf setuid and every entry another user's.
    src_c = os.path.join(d, "c")
    shutil.copytree(src_a, src_c)
    os.chmod(os.path.join(src_c, "etc", "conf"), 0o4644)
    a, rebuilt, changed, owned = (os.path.join(d, n + ".sb")
                                  for n in ("a", "rebuilt", "changed", "owned"))
    _sb(src_a, a, "1000")
    _sb(src_a, rebuilt, "2000")
    _sb(src_b, changed, "1000")
    _sb(src_c, owned, "1000", uid=1000)

    rc, out, _ = _run([a, rebuilt])
    check("a rebuilt bundle: exit 0", rc, 0)
    check("...and its verdict is identical content", _verdict(out), "identical content")
    rc, out, _ = _run([a, changed])
    check("a bundle changed inside: exit 1", rc, 1)
    check("...naming the file whose content changed", "~  etc/conf " in out, True)
    # The lists inside a container stopped at --limit in silence: one with more changes of
    # one kind listed --limit of them and said nothing of the rest (#99, found by reading).
    rc, out, _ = _run([a, changed, "--limit", "1"])
    check("a list --limit cuts short says what it left out", "... 1 more added" in out, True)
    # Unprivileged, unsquashfs makes every entry the runner's and drops setuid bits, so
    # read from the extraction these two bundles agreed: `identical content`, exit 0, as
    # uid 1000 (#99, measured 2026-10-23). CI's gates job is not root, so it runs this
    # exactly where the extraction cannot see either.
    rc, out, _ = _run([a, owned])
    check("a bundle whose owner and setuid bit changed: exit 1", rc, 1)
    check("...naming both", [ln.split() for ln in out.splitlines() if "etc/conf" in ln],
          [["~", "etc/conf", "mode,", "owner"]])

    iso = os.path.join(d, "x.iso")
    with open(a, "rb") as fh:
        _iso_carrying(iso, fh.read(), 24)
    rc, out, err = _run([a, iso])
    check("a bundle against an ISO: refused, exit 2", rc, 2)
    check("...naming both kinds", "a squashfs bundle" in err and "an ISO image" in err, True)
    check("...before printing half a report", out, "")


def test_two_esps_compare_by_content():
    """`kitchen diff a.img b.img` on two FAT12 images, as the UEFI ESP is.

    mkfs.vfat picks a volume serial for every image it makes, and each directory entry
    records a time. Three shipped UEFI profiles, each built twice, made pairs of ESPs that
    differed in 20 bytes with the same label and the same EFI/BOOT/BOOTX64.EFI, and kitchen
    diff could compare neither them nor the images that carried them (#99, measured
    2026-10-23). An ESP is compared by its files and its label now, read in memory: no
    tool, so this runs in CI's gates job whatever it lacks.
    """
    loader = b"MZ" + bytes(range(256)) * 8
    imgs = {
        "a": _fat12("SLAXEFI", {"EFI/BOOT/BOOTX64.EFI": loader}, 0x1234ABCD, 0x5A5A0001),
        "rebuilt": _fat12("SLAXEFI", {"EFI/BOOT/BOOTX64.EFI": loader}, 0x0BADF00D, 0x5A5A0002),
        "changed": _fat12("SLAXEFI", {"EFI/BOOT/BOOTX64.EFI": loader[:-1] + b"\x00"},
                          0x1234ABCD, 0x5A5A0001),
        "relabelled": _fat12("OTHER", {"EFI/BOOT/BOOTX64.EFI": loader}, 0x1234ABCD, 0x5A5A0001),
    }
    d = tempfile.mkdtemp()
    path = {}
    for name, img in imgs.items():
        path[name] = os.path.join(d, name + ".img")
        with open(path[name], "wb") as f:
            f.write(img)
    check("the rebuild differs in bytes", imgs["a"] != imgs["rebuilt"], True)

    rc, out, _ = _run([path["a"], path["rebuilt"]])
    check("a rebuilt ESP: exit 0", rc, 0)
    check("...and its verdict is identical content", _verdict(out), "identical content")
    rc, out, _ = _run([path["a"], path["changed"]])
    check("an ESP whose loader changed: exit 1", rc, 1)
    check("...naming the loader", "~  EFI/BOOT/BOOTX64.EFI " in out, True)
    rc, out, _ = _run([path["a"], path["relabelled"]])
    check("an ESP whose label changed: exit 1", rc, 1)
    check("...naming the label", "label  'SLAXEFI' -> 'OTHER'" in out, True)


def test_an_image_diff_hands_its_changed_containers_over():
    """`kitchen diff A.iso B.iso --bundles` on two images whose one difference is a
    container: a bundle, or the ESP.

    A rebuilt bundle read "(identical content -- every entry matches ...)" and the images
    still ended DIFFERENT, exit 1, and nothing looked inside the ESP. Every shipped profile
    that builds a bundle is UEFI too, so no rebuild of one could end anything else. Built
    twice each, example, boot-matrix and tor ended DIFFERENT, and boot-matrix, which builds
    no bundle, for its ESP alone (#99, measured 2026-10-23).
    Under --bundles each changed container goes to the container diff now, and the images
    are judged by what is inside them. Without it, still by their bytes.
    """
    if not _have_tools("an image diff's containers"):
        return
    d = tempfile.mkdtemp()
    sbs = {}
    for name, word, when in (("a", "one", "1000"), ("rebuilt", "one", "2000"),
                             ("changed", "two", "1000")):
        src = os.path.join(d, "src-" + name)
        os.makedirs(src)
        with open(os.path.join(src, "conf"), "w") as f:
            f.write(word + "\n")
        _sb(src, os.path.join(d, name + ".sb"), when)
        with open(os.path.join(d, name + ".sb"), "rb") as fh:
            sbs[name] = fh.read()
    loader = b"MZ" + bytes(range(256)) * 8
    esps = {"a": _fat12("SLAXEFI", {"EFI/BOOT/BOOTX64.EFI": loader}, 1, 1),
            "rebuilt": _fat12("SLAXEFI", {"EFI/BOOT/BOOTX64.EFI": loader}, 2, 2),
            "changed": _fat12("SLAXEFI", {"EFI/BOOT/BOOTX64.EFI": loader[:-1] + b"\x00"}, 1, 1)}
    cases = (
        ("a rebuilt bundle", "/slax/modules/08-x.sb", sbs, "rebuilt", 0, "identical content"),
        ("a bundle changed inside", "/slax/modules/08-x.sb", sbs, "changed", 1, "DIFFERENT"),
        ("a rebuilt ESP", "/boot/efi.img", esps, "rebuilt", 0, "identical content"),
        ("an ESP changed inside", "/boot/efi.img", esps, "changed", 1, "DIFFERENT"),
    )
    lba = 24
    for i, (name, rel, payloads, other, want, verdict) in enumerate(cases):
        box = os.path.join(d, str(i))
        os.makedirs(box)
        isos = [os.path.join(box, "a.iso"), os.path.join(box, "b.iso")]
        _iso_carrying(isos[0], payloads["a"], lba)
        _iso_carrying(isos[1], payloads[other], lba)
        stub = _stub_xorriso(box, rel, len(payloads["a"]), lba)
        rc, out, _ = _run(isos + ["--bundles"], stub)
        check(f"{name}, --bundles: exit {want}", rc, want)
        check(f"{name}, --bundles: the verdict", _verdict(out), verdict)
        if want == 0:
            # The positive case beside the negative one: the same pair, by its bytes.
            rc, out, _ = _run(isos, stub)
            check(f"{name}, without --bundles: still DIFFERENT, exit 1",
                  (rc, _verdict(out)), (1, "DIFFERENT"))


def main():
    # EVERY FIXTURE THIS FILE MAKES GOES IN ONE BOX, AND THE BOX GOES AWAY. Both the
    # global and the variable: mksquashfs is a child process and reads TMPDIR, which
    # tempfile.tempdir does not steer.
    box = tempfile.mkdtemp(prefix="test_diff-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_a_rebuild_is_not_a_change,
                   test_a_change_is_named,
                   test_a_bundle_that_cannot_be_read_is_refused,
                   test_an_unreadable_bundle_does_not_abandon_the_report,
                   test_a_term_during_bundles_takes_the_unpacked_bundle_away,
                   test_two_bundles_compare_by_content,
                   test_two_esps_compare_by_content,
                   test_an_image_diff_hands_its_changed_containers_over]:
            # One test crashing must not stop the rest: the count of failures is only honest
            # if every test ran. The traceback still goes to stderr, because a crash's location
            # is the useful half and a one-line summary loses it.
            try:
                fn()
            except Exception as e:                 # noqa: BLE001
                traceback.print_exc()
                FAILURES.append(f"{fn.__name__} crashed: {type(e).__name__}: {e}")
        if FAILURES:
            for f in FAILURES:
                print(f"FAIL {f}", file=sys.stderr)
            return 1
        print("tests/unit/test_diff.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
