#!/usr/bin/env python3
"""Unit tests for `kitchen diff`'s bundle comparison.

A squashfs is a container with a creation time of its own, and it stores an mtime per
file, so two builds of the same tree differ in BYTES while every file in them is
identical. `diff --bundles` compared the file LIST only and said "(same file list;
contents differ)" either way -- the one answer that is true whether or not anything
changed, which made two builds a recipe apart exactly the case it could not report.
Issue #30.
"""
import importlib.util
import os
import shutil
import struct
import subprocess
import sys
import tempfile

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


def _sb(src: str, path: str, mkfs_time: str) -> None:
    """Pack `src`, stamping the superblock rather than waiting for the clock to move.

    -mkfs-time, not time.sleep(1.1): the point is two containers that differ while their
    contents do not, and sleeping for it costs the gate a second at every commit and every
    push AND makes the fixture depend on wall-clock resolution.
    """
    subprocess.run(["mksquashfs", src, path, "-noappend", "-no-progress", "-all-root",
                    "-mkfs-time", mkfs_time], capture_output=True, check=True)


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
    # can carry one -- bundle.fromTarball admits fifos and device nodes under
    # privilege: mknod.
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
                   test_an_unreadable_bundle_does_not_abandon_the_report]:
            fn()
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
