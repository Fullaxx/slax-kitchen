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


def main():
    for fn in [test_key_includes_architecture, test_blank_line_inside_description,
               test_delta_is_added_and_changed, test_delta_empty_when_nothing_changed,
               test_merge_replaces_in_place, test_merge_appends_new_and_keeps_base,
               test_merge_is_idempotent, test_merge_order_later_fragment_wins,
               test_sortmod_matches_livekit, test_render_round_trip]:
        fn()
    if FAILURES:
        for f in FAILURES:
            print(f"FAIL {f}", file=sys.stderr)
        return 1
    print("tests/unit/test_dpkgdb.py: all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
