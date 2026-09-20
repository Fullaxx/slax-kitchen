#!/usr/bin/env python3
"""Unit tests for merging Debian's package database across bundles.

The bug these guard against shipped: add-packages built a bundle from 01-core alone,
so its var/lib/dpkg/status listed 299 packages, and the bundle sorted above
05-chromium's 600. Higher wins, so a booted system lost 304 packages with no error
anywhere. Every case below is a way the replacement could reintroduce that.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "lib"))

import dpkgdb  # noqa: E402

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def stanza(pkg, version, arch="amd64", extra=""):
    return (f"Package: {pkg}\nStatus: install ok installed\n"
            f"Architecture: {arch}\nVersion: {version}" + extra)


def test_key_includes_architecture():
    """Multiarch makes the name alone ambiguous.

    wine32:i386 and wine64:amd64 are separate dpkg entries. Keying a merge on the name
    would silently drop one of them -- which is exactly the shape of the bug being
    fixed, one level down.
    """
    text = stanza("wine", "8.0", "amd64") + "\n\n" + stanza("wine", "8.0", "i386")
    check("two arches parse as two entries", len(dpkgdb.parse(text)), 2)
    check("keys are arch-qualified",
          sorted(k for k, _ in dpkgdb.parse(text)), ["wine:amd64", "wine:i386"])


def test_blank_line_inside_description():
    """dpkg writes an empty description line as " .", so a blank line always ends a stanza.

    Splitting naively on "\\n\\n" would be wrong if descriptions could contain one; they
    cannot, and this pins that assumption to a test rather than a comment.
    """
    text = (stanza("a", "1", extra="\nDescription: first\n line one\n .\n line two")
            + "\n\n" + stanza("b", "2"))
    check("description with ' .' stays one stanza", len(dpkgdb.parse(text)), 2)
    check("both packages found",
          sorted(k for k, _ in dpkgdb.parse(text)), ["a:amd64", "b:amd64"])


def test_delta_is_added_and_changed():
    before = stanza("keep", "1") + "\n\n" + stanza("bump", "1")
    after = stanza("keep", "1") + "\n\n" + stanza("bump", "2") + "\n\n" + stanza("new", "1")
    got = sorted(k for k, _ in dpkgdb.parse(dpkgdb.delta(before, after)))
    check("delta = changed + added, not unchanged", got, ["bump:amd64", "new:amd64"])


def test_delta_empty_when_nothing_changed():
    """A bundle.script that only runs useradd has no packages to declare."""
    s = stanza("a", "1")
    check("no change means no fragment", dpkgdb.delta(s, s).strip(), "")


def test_merge_replaces_in_place():
    """An upgrade must REPLACE the stanza, not append a second one.

    dpkg reads the first entry for a name. Appending would leave the old version
    winning, so a recipe that installs a current Chromium over the 2023 one would
    report the old version forever.
    """
    base = stanza("chromium", "117") + "\n\n" + stanza("other", "1")
    frag = stanza("chromium", "150")
    merged = dpkgdb.merge(base, [frag])
    keys = [k for k, _ in dpkgdb.parse(merged)]
    check("no duplicate entry", keys.count("chromium:amd64"), 1)
    check("order preserved", keys, ["chromium:amd64", "other:amd64"])
    check("new version wins", "Version: 150" in merged, True)
    check("old version gone", "Version: 117" in merged, False)


def test_merge_appends_new_and_keeps_base():
    """The whole point: nothing below may be lost."""
    base = "\n\n".join(stanza(f"p{i}", "1") for i in range(600))
    merged = dpkgdb.merge(base, [stanza("tmux", "3.3"), stanza("ncdu", "1.18")])
    check("600 base + 2 added", len(dpkgdb.parse(merged)), 602)
    check("base intact", all(f"Package: p{i}" in merged for i in (0, 299, 599)), True)


def test_merge_is_idempotent():
    """pack runs on every build; merging twice must not grow the file."""
    base, frag = stanza("a", "1"), stanza("b", "1")
    once = dpkgdb.merge(base, [frag])
    twice = dpkgdb.merge(once, [frag])
    check("merging again changes nothing", twice, once)


def test_merge_order_later_fragment_wins():
    """Fragments apply in load order, so the higher bundle's claim is the live one."""
    merged = dpkgdb.merge(stanza("x", "1"), [stanza("x", "2"), stanza("x", "3")])
    check("last fragment wins", "Version: 3" in merged, True)
    check("single entry", len(dpkgdb.parse(merged)), 1)


def test_sortmod_matches_livekit():
    """Load order is the numeric prefix; ties fall back to an alphabetical compare.

    The tie case is not academic -- stock Slax ships 01-core.sb and 01-firmware.sb, and
    five of our own recipes all chose 07.
    """
    check("numeric, not lexical",
          dpkgdb.sortmod(["10-x.sb", "09-y.sb", "02-z.sb"]),
          ["02-z.sb", "09-y.sb", "10-x.sb"])
    check("same prefix falls back to name",
          dpkgdb.sortmod(["01-firmware.sb", "01-core.sb"]),
          ["01-core.sb", "01-firmware.sb"])
    check("generated bundle outranks every add-on",
          dpkgdb.sortmod(["98-dpkg-db.sb", "07-extras.sb", "11-firefox.sb"])[-1],
          "98-dpkg-db.sb")


def test_render_round_trip():
    text = "\n\n".join(stanza(f"p{i}", "1") for i in range(5)) + "\n"
    check("parse/render is lossless", dpkgdb.render(dpkgdb.parse(text)), text)


def test_merge_fragments_into_chroot():
    """A build chroot must see the add-on bundles beneath it, not just the stock ones.

    Bundles ship a fragment instead of a cumulative var/lib/dpkg/status, and pack merged
    them at the end -- but nothing merged them on the way IN. So stacking a previously
    built 10-chromium.sb under a new bundle left apt reading 04-apps.sb's status and
    believing chromium had installed nothing: unsquashfs -f cannot overwrite a status
    file that 10-chromium.sb does not contain. Measured consequence in
    docs/50-cookbook/firefox-esr.md: 5 packages declared where 2 was right.

    The fragment files must survive untouched. They are deliberately kept in a built
    bundle, so rewriting or re-timing them would put them in the NEXT bundle's file
    delta -- the higher bundle would ship a copy of the lower one's fragment, which then
    outlives the bundle it describes.
    """
    import tempfile

    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, "var", "lib", "dpkg"))
    fd = os.path.join(root, dpkgdb.FRAGMENT_DIR)
    os.makedirs(fd)

    status = os.path.join(root, dpkgdb.STATUS)
    with open(status, "w") as f:
        f.write(dpkgdb.render([("base:amd64", stanza("base", "1.0"))]))
    # Two fragments, and the higher-numbered bundle must win on a shared package.
    with open(os.path.join(fd, "10-chromium"), "w") as f:
        f.write(dpkgdb.render([("libnss3:amd64", stanza("libnss3", "2:3.87")),
                               ("shared:amd64", stanza("shared", "1.0"))]))
    with open(os.path.join(fd, "11-firefox"), "w") as f:
        f.write(dpkgdb.render([("shared:amd64", stanza("shared", "2.0"))]))

    before = {n: os.stat(os.path.join(fd, n)).st_mtime_ns for n in os.listdir(fd)}
    n, names = dpkgdb.merge_fragments_into_chroot(root)
    check("fragments merged", n, 2)
    check("named after their bundles", names, ["10-chromium", "11-firefox"])

    got = dict(dpkgdb.parse(open(status).read()))
    check("base survives", "base:amd64" in got, True)
    check("add-on package is now visible to apt", "libnss3:amd64" in got, True)
    check("later fragment wins", "Version: 2.0" in got.get("shared:amd64", ""), True)

    after = {n: os.stat(os.path.join(fd, n)).st_mtime_ns for n in os.listdir(fd)}
    check("fragment files untouched", after, before)

    # Idempotent: a second pass must not duplicate or reorder anything.
    dpkgdb.merge_fragments_into_chroot(root)
    check("merging twice changes nothing", dict(dpkgdb.parse(open(status).read())), got)

    # A stock-only stack has no fragment directory at all, and that is not an error.
    bare = tempfile.mkdtemp()
    os.makedirs(os.path.join(bare, "var", "lib", "dpkg"))
    check("no fragment dir is a no-op", dpkgdb.merge_fragments_into_chroot(bare), (0, []))


def test_a_real_status_does_not_discard_the_fragments_below_it():
    """The base is the highest real status; every fragment still merges into it.

    This used to be the other way round -- any real var/lib/dpkg/status discarded the
    fragments beneath it. Measured, that is wrong for every carrier except a saved
    session:

      stock status   upstream builds it from stock bundles only, so it cannot know about
                     an add-on beneath it. Discarding lost those packages for good, even
                     though 98-dpkg-db.sb sorts above every stock bundle and is what dpkg
                     actually reads at boot.

    The loss was silent whenever at least one fragment survived, because the error that
    was supposed to catch it only fired when EVERY fragment had been discarded.

    Inverting the reset also removes a fragility the old rule had: because fragments are
    no longer dropped by position, nothing here compares bundle numbers. sortmod still
    decides which status becomes the BASE -- the highest one -- and that is what the two
    status carriers below pin, since 05-chromium's must win over 01-core's.
    """
    import shutil
    import subprocess
    import tempfile

    # A BARE `return` HERE PRINTED "all checks passed" HAVING ASSERTED NOTHING, and
    # unsquashfs was never checked at all though merge_tree calls it. Both are required
    # tools of this project (kitchen doctor asserts them), so their absence is a named
    # failure, not a silent pass. Found 2026-09-20.
    missing = [t for t in ("mksquashfs", "unsquashfs") if not shutil.which(t)]
    if missing:
        FAILURES.append(f"{', '.join(missing)} not installed, so merge_tree cannot be "
                        f"tested (required tools -- see kitchen doctor)")
        return

    def sb(path, files):
        src = tempfile.mkdtemp()
        for rel, body in files.items():
            full = os.path.join(src, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(body)
        subprocess.run(["mksquashfs", src, path, "-noappend", "-no-progress", "-all-root"],
                       capture_output=True, check=True)

    def packed(mods):
        out = tempfile.mkdtemp()
        subprocess.run(["unsquashfs", "-n", "-q", "-d", out,
                        os.path.join(mods, dpkgdb.GENERATED)], capture_output=True)
        with open(os.path.join(out, dpkgdb.STATUS)) as f:
            return sorted(k for k, _ in dpkgdb.parse(f.read()))

    iso = tempfile.mkdtemp()
    mods = os.path.join(iso, "slax", "modules")
    os.makedirs(mods)
    frag = dpkgdb.FRAGMENT_DIR
    # below the stock status, at a tie-broken 05, and above it
    sb(os.path.join(mods, "00-mytools.sb"),
       {os.path.join(frag, "00-mytools"): dpkgdb.render([("mytool:amd64", stanza("mytool", "1.0"))])})
    sb(os.path.join(mods, "05-audio.sb"),
       {os.path.join(frag, "05-audio"): dpkgdb.render([("alsa:amd64", stanza("alsa", "1.2"))])})
    # Two status carriers, so the base is order-dependent: the higher must win.
    sb(os.path.join(mods, "01-core.sb"),
       {dpkgdb.STATUS: dpkgdb.render([("base:amd64", stanza("base", "1.0"))])})
    sb(os.path.join(mods, "05-chromium.sb"),
       {dpkgdb.STATUS: dpkgdb.render([("base:amd64", stanza("base", "1.0")),
                                      ("chromium:amd64", stanza("chromium", "117"))])})
    sb(os.path.join(mods, "07-extras.sb"),
       {os.path.join(frag, "07-extras"): dpkgdb.render([("tmux:amd64", stanza("tmux", "3.3a"))])})

    check("sortmod puts 05-audio below 05-chromium",
          dpkgdb.sortmod(["05-chromium.sb", "05-audio.sb"]), ["05-audio.sb", "05-chromium.sb"])
    n = dpkgdb.merge_tree(iso, quiet=True)
    check("every fragment merged, not just the ones above", n, 3)
    got = packed(mods)
    check("a fragment below the status survives", "mytool:amd64" in got, True)
    check("a tie-broken fragment below it survives", "alsa:amd64" in got, True)
    check("the HIGHEST status is the base, not the first",
          "chromium:amd64" in got, True)


def test_a_saved_session_supersedes_the_fragments_below_it():
    """...and a session is the one carrier that legitimately does discard them.

    savechanges squashes the WRITABLE layer, so 99-changes-N carries a status only if
    packages changed -- and that copy is a copy-up of the complete merged database,
    newer than the fragments that fed it. Merging them back over it downgrades: measured,
    tmux 3.4 became 3.3a.

    This is also a regression test. A guard added earlier RAISED on this tree, so
    `kitchen pack` failed on any work tree unpacked from an ISO somebody had saved
    changes onto -- and nothing in the suite packed such a tree, which is why it got
    through.
    """
    import shutil
    import subprocess
    import tempfile

    # A BARE `return` HERE PRINTED "all checks passed" HAVING ASSERTED NOTHING, and
    # unsquashfs was never checked at all though merge_tree calls it. Both are required
    # tools of this project (kitchen doctor asserts them), so their absence is a named
    # failure, not a silent pass. Found 2026-09-20.
    missing = [t for t in ("mksquashfs", "unsquashfs") if not shutil.which(t)]
    if missing:
        FAILURES.append(f"{', '.join(missing)} not installed, so merge_tree cannot be "
                        f"tested (required tools -- see kitchen doctor)")
        return

    def sb(path, files):
        src = tempfile.mkdtemp()
        for rel, body in files.items():
            full = os.path.join(src, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w") as f:
                f.write(body)
        subprocess.run(["mksquashfs", src, path, "-noappend", "-no-progress", "-all-root"],
                       capture_output=True, check=True)

    iso = tempfile.mkdtemp()
    mods = os.path.join(iso, "slax", "modules")
    os.makedirs(mods)
    sb(os.path.join(mods, "01-core.sb"),
       {dpkgdb.STATUS: dpkgdb.render([("base:amd64", stanza("base", "1.0"))])})
    sb(os.path.join(mods, "07-extras.sb"),
       {os.path.join(dpkgdb.FRAGMENT_DIR, "07-extras"):
        dpkgdb.render([("tmux:amd64", stanza("tmux", "3.3a"))])})
    # the session upgraded tmux on the booted system
    sb(os.path.join(mods, "99-changes-1.sb"),
       {dpkgdb.STATUS: dpkgdb.render([("base:amd64", stanza("base", "1.0")),
                                      ("tmux:amd64", stanza("tmux", "3.4"))])})

    n = dpkgdb.merge_tree(iso, quiet=True)
    check("a session supersedes the fragments below it", n, 0)
    check("so no generated bundle is written",
          os.path.isfile(os.path.join(mods, dpkgdb.GENERATED)), False)

    # And the direction that matters: merging would have downgraded it.
    session = dpkgdb.render([("tmux:amd64", stanza("tmux", "3.4"))])
    frag = dpkgdb.render([("tmux:amd64", stanza("tmux", "3.3a"))])
    merged = dict(dpkgdb.parse(dpkgdb.merge(session, [frag])))
    check("merging a fragment over a session would downgrade it",
          "3.3a" in merged["tmux:amd64"], True)


def main():
    # EVERY FIXTURE THIS FILE MAKES GOES IN ONE BOX, AND THE BOX GOES AWAY.
    # all 7 of this file's mkdtemp() calls had no cleanup on 2026-09-18, so running it by hand left
    # their trees in /tmp and nothing took them away. ci/checks/80-unit.sh does this
    # for a GATE run; the box is the half that survives running the file directly.
    # Issue #25.
    #
    # Set before the first test, because tempfile.tempdir only steers calls made
    # after it -- and cleared afterwards so a caller that imports this file is not
    # left pointing at a directory that no longer exists.
    #
    # BOTH THE GLOBAL AND THE VARIABLE. tempfile.tempdir steers this process; TMPDIR steers
    # the children, and they are not the same thing. ci/release-assets.sh and three siblings
    # do `mktemp -d "${TMPDIR:-/tmp}/..."`, which reads the variable and never the global, so
    # the global on its own leaves a subprocess's fixture outside the box. Nothing here drives
    # one of those today -- but the box claims every fixture this file makes, and those are
    # fixtures this file made. Under the gate it changes nothing, since the ambient TMPDIR is
    # already the gate's own box; it is the by-hand run that this is for.
    import shutil
    import tempfile
    box = tempfile.mkdtemp(prefix="test_dpkgdb-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_key_includes_architecture, test_blank_line_inside_description,
                   test_delta_is_added_and_changed, test_delta_empty_when_nothing_changed,
                   test_merge_replaces_in_place, test_merge_appends_new_and_keeps_base,
                   test_merge_is_idempotent, test_merge_order_later_fragment_wins,
                   test_sortmod_matches_livekit, test_render_round_trip,
                   test_merge_fragments_into_chroot,
                   test_a_real_status_does_not_discard_the_fragments_below_it,
                   test_a_saved_session_supersedes_the_fragments_below_it]:
            fn()
        if FAILURES:
            for f in FAILURES:
                print(f"FAIL {f}", file=sys.stderr)
            return 1
        print("tests/unit/test_dpkgdb.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
