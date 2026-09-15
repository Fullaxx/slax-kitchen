#!/usr/bin/env python3
"""Make Debian's package database survive being split across squashfs bundles.

THE PROBLEM. A union filesystem composes trees, not files. Slackware's package
database is a directory -- var/lib/pkgtools/packages/ holds one file per package -- so
bundles merge for free. Debian's is a single file, var/lib/dpkg/status, so the topmost
bundle that ships one wins outright and every copy below it is invisible.

Upstream Slax gets away with this by building a strict chain: each bundle is chrooted
into everything below it, so the counts climb 295 -> 307 -> 424 -> 535 -> 575 -> 600 and
the top copy is complete. That works, but it makes bundles serial and un-removable --
you cannot build two add-ons independently, because whichever lands higher erases the
other's packages.

THE FIX. Add-on bundles ship no status at all. Each carries a FRAGMENT instead --
var/lib/slax-kitchen/dpkg-status.d/<bundle> holding only the stanzas that bundle added
or changed. A directory of fragments composes the way Slackware's database already
does. `kitchen pack` then merges base + fragments into one generated bundle.

The degradation is deliberate: drop a .sb onto a stick by hand afterwards and dpkg
simply will not list its packages. That is a cosmetic loss, where the shadowing bug was
a silent loss of three hundred real ones.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

STATUS = "var/lib/dpkg/status"
FRAGMENT_DIR = "var/lib/slax-kitchen/dpkg-status.d"

# Sits above every add-on bundle but below 99-changes-N, so a saved session still wins.
# Every bundle on every shipped image has superblock flags 0x04e0, because upstream uses
# one mksquashfs line in four places: livekitlib's create_bundle, dir2sb, savechanges and
# both flavours' module builders. Matching it exactly is what keeps a built bundle
# indistinguishable from a shipped one, so `kitchen probe` can still reason about an ISO.

MKSQUASHFS_ARGS = ["-comp", "xz", "-b", "1024K", "-Xbcj", "x86",
                   "-always-use-fragments", "-noappend"]

GENERATED = "98-dpkg-db.sb"


def sortmod(names: list[str]) -> list[str]:
    """Order bundles as livekitlib's sortmod does: numeric prefix, then full name."""
    def key(n: str) -> tuple[int, str]:
        m = re.match(r"^(\d+)", n)
        return (int(m.group(1)) if m else 0, n)
    return sorted(names, key=key)


def parse(text: str) -> list[tuple[str, str]]:
    """Split a status file into (key, stanza) pairs, preserving order.

    The key is name:architecture, not the name alone. Multiarch means wine32:i386 and
    wine64:amd64 are separate entries, and merging on the name would drop one of them.

    Splitting on a blank line is safe: dpkg writes an empty line inside a Description as
    " ." precisely so that a truly blank line always ends a stanza.
    """
    out = []
    for stanza in re.split(r"\n[ \t]*\n", text.strip("\n")):
        if not stanza.strip():
            continue
        name = arch = ""
        for line in stanza.splitlines():
            if line.startswith("Package:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Architecture:"):
                arch = line.split(":", 1)[1].strip()
        if name:
            out.append((f"{name}:{arch}" if arch else name, stanza))
    return out


def render(stanzas: list[tuple[str, str]]) -> str:
    return "\n\n".join(s for _, s in stanzas) + "\n"


def delta(before: str, after: str) -> str:
    """Stanzas that appeared or changed. An upgrade counts as a change, not an add."""
    old = dict(parse(before))
    return render([(k, s) for k, s in parse(after) if old.get(k) != s])


def merge(base: str, fragments: list[str]) -> str:
    """base, then each fragment in order: replace an existing key in place, else append.

    Replacing IN PLACE rather than appending keeps a fragment that upgrades a package
    (a newer chromium, say) from leaving two stanzas for the same name -- dpkg reads the
    first and would silently use the old one.
    """
    out = parse(base)
    index = {k: i for i, (k, _) in enumerate(out)}
    for frag in fragments:
        for k, s in parse(frag):
            if k in index:
                out[index[k]] = (k, s)
            else:
                index[k] = len(out)
                out.append((k, s))
    return render(out)


def _extract(sb: str, member: str, dest: str) -> str | None:
    """Pull one path out of a squashfs. A plain read -- no privilege needed."""
    subprocess.run(["unsquashfs", "-n", "-d", dest, sb, member],
                   capture_output=True, text=True)
    p = os.path.join(dest, member)
    return p if os.path.exists(p) else None


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def merge_fragments_into_chroot(root: str) -> tuple[int, list[str]]:
    """Fold any status fragments in an unpacked stack into that chroot's dpkg database.

    Bundles ship a fragment instead of a cumulative var/lib/dpkg/status, because a union
    composes trees and not files -- one bundle's copy would shadow the larger one below
    it wholesale. pack merges the fragments into 98-dpkg-db.sb at the end. But nothing
    merged them on the way IN, so a build chroot saw the status of the highest STOCK
    bundle and believed every add-on beneath it had installed nothing.

    Measured on the firefox-esr case: with 10-chromium.sb directly beneath it, the
    chroot still read 04-apps.sb's 575 packages -- unsquashfs -f cannot overwrite a
    status that 10-chromium.sb does not contain -- so apt reinstalled the browser
    runtime that was already there and the fragment declared 5 packages instead of 2.
    Harmless while the versions coincide; an archive update between the two builds makes
    it version skew, and removing the lower bundle makes the fragment declare packages
    whose files have left.

    The fragment FILES are read and never touched. They are deliberately not in
    BUNDLE_EXCLUDE (tests/unit/test_apply.py asserts they are kept), so rewriting or
    even re-timing them would put them in the next bundle's delta -- the higher bundle
    would ship a copy of the lower one's fragment, which then survives the lower bundle
    being removed. Read-only is load-bearing.

    Returns (fragments merged, bundle names they came from). The rewritten status cannot
    leak into a built bundle: BUNDLE_EXCLUDE drops var/lib/dpkg/status and status-old.
    """
    fd = os.path.join(root, FRAGMENT_DIR)
    if not os.path.isdir(fd):
        return (0, [])
    # Sorted, because each fragment is named after the bundle that wrote it and that is
    # the order the union loads them in -- the same order merge_tree uses at pack time.
    names = sorted(os.listdir(fd))
    fragments = []
    for n in names:
        f = os.path.join(fd, n)
        if os.path.isfile(f):
            with open(f, encoding="utf-8", errors="replace") as fh:
                fragments.append(fh.read())
    if not fragments:
        return (0, [])
    status = os.path.join(root, STATUS)
    base = ""
    if os.path.isfile(status):
        with open(status, encoding="utf-8", errors="replace") as fh:
            base = fh.read()
    with open(status, "w", encoding="utf-8") as fh:
        fh.write(merge(base, fragments))
    return (len(fragments), names)


def merge_tree(iso: str, quiet: bool = False) -> int:
    """Rebuild <iso>/slax/modules/98-dpkg-db.sb from the base status plus all fragments.

    Returns the number of fragments merged; 0 means nothing was written.
    """
    mods = os.path.join(iso, "slax", "modules")
    if not os.path.isdir(mods):
        return 0

    # Remove any previous generated bundle FIRST. It ships a complete status, so leaving
    # it in place would make it the base on a second pack and freeze the database at
    # whatever the first pack produced.
    prev = os.path.join(mods, GENERATED)
    if os.path.exists(prev):
        os.unlink(prev)

    names = sortmod([n for n in os.listdir(mods) if n.endswith(".sb")])
    tmp = tempfile.mkdtemp(prefix="kitchen-dpkgdb.")
    try:
        base, base_from, fragments, frag_from = "", "", [], []
        carriers: list = []
        discarded: list = []
        for n in names:
            sb = os.path.join(mods, n)
            d = os.path.join(tmp, n)
            p = _extract(sb, STATUS, d)
            if p:
                base, base_from = _read(p), n
                # A real status outranks everything below it, fragments included.
                # Remember what that cost: `discarded` deliberately survives the reset,
                # because the reset is the only thing that can silently lose a bundle's
                # packages and there is otherwise no trace of it afterwards.
                discarded += frag_from
                fragments, frag_from, carriers = [], [], []
                continue
            fd = os.path.join(tmp, n + ".frag")
            if _extract(sb, FRAGMENT_DIR, fd):
                carriers.append(n)
                for f in sorted(os.listdir(os.path.join(fd, FRAGMENT_DIR))):
                    fragments.append(_read(os.path.join(fd, FRAGMENT_DIR, f)))
                    frag_from.append(f"{n}:{f}")

        if not fragments:
            # Nothing to merge is normal -- a stock tree, or one with no add-on bundle.
            # Fragments that EXISTED and were outranked is not: those packages are now
            # in no database anywhere, and returning 0 here made that silent. It is how
            # `bundle.renumber` moving 05-chromium to 95 loses the bundle beneath it --
            # the real 600-package status sorts above the fragment, the reset fires, and
            # pack writes no 98-dpkg-db.sb at all while exiting 0.
            if discarded:
                raise RuntimeError(
                    "dpkgdb: every status fragment was outranked by a real "
                    "var/lib/dpkg/status above it, so none could be merged and the "
                    "packages they declare would be in no database at all.\n"
                    f"  discarded: {', '.join(discarded)}\n"
                    f"  outranked by: {base_from}\n"
                    "  A bundle that ships a real status must sort BELOW every bundle "
                    "carrying a fragment. Renumbering one above them is what usually "
                    "causes this -- see docs/40-workflow/composing-bundles.md.")
            return 0
        if not base:
            raise RuntimeError(
                "dpkgdb: bundles carry status fragments but no bundle ships a "
                "var/lib/dpkg/status to merge them into")

        # Only the FRAGMENT-BEARING bundles have to sort below the generated one. A
        # saved session at 99-changes-N legitimately outranks it -- that is the whole
        # point of 99 -- so checking "the highest bundle of any kind" would reject a
        # work tree unpacked from an ISO somebody had saved changes onto.
        for n in carriers:
            if sortmod([n, GENERATED])[-1] != GENERATED:
                raise RuntimeError(
                    f"dpkgdb: {n} carries a status fragment but sorts at or above "
                    f"{GENERATED}, so the merged database would be shadowed by it; "
                    f"renumber it below 98")

        merged = merge(base, fragments)
        stage = os.path.join(tmp, "stage")
        os.makedirs(os.path.join(stage, os.path.dirname(STATUS)), exist_ok=True)
        with open(os.path.join(stage, STATUS), "w") as f:
            f.write(merged)

        # Explicit, not umask-dependent. os.makedirs and open() above take whatever
        # umask the caller happened to have, and -all-root then fixes the ids but not
        # the modes -- so without this the generated bundle's permissions varied by who
        # ran pack, which also quietly contradicted the reproducibility claim in
        # docs/40-workflow/reproducibility.md.
        os.chmod(os.path.join(stage, STATUS), 0o644)
        for d in ("var/lib/dpkg", "var/lib", "var"):
            os.chmod(os.path.join(stage, d), 0o755)

        # -all-root because this bundle is GENERATED: nothing in it came from a chroot
        # whose ownership meant anything, and it is created by whoever ran `kitchen
        # pack`. ci/recipe-matrix.sh runs apply under sudo and pack without it, so on a
        # runner this shipped /var/lib/dpkg owned by uid 1001 -- and 98- outranks
        # everything below it, so that was the ownership the booted union saw for dpkg's
        # own database directory.
        #
        # MKSQUASHFS_ARGS is imported rather than retyped. This call site was a hand
        # copy, which made it the one place a change to lib/apply.py's bundle building
        # silently missed.
        r = subprocess.run(["mksquashfs", stage, os.path.join(mods, GENERATED)]
                           + MKSQUASHFS_ARGS + ["-all-root"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"dpkgdb: mksquashfs failed: {r.stderr.strip()[:300]}")

        if not quiet:
            print(f"  ok   {GENERATED}: {len(parse(base))} from {base_from} + "
                  f"{len(fragments)} fragment(s) -> {len(parse(merged))} packages")
            for f in frag_from:
                print(f"       {f}")
        return len(fragments)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kitchen dpkgdb",
                                 description="merge Debian status fragments into one bundle")
    ap.add_argument("iso", help="the iso/ directory of a work tree")
    ap.add_argument("-q", "--quiet", action="store_true")
    args = ap.parse_args(argv[1:])
    try:
        merge_tree(args.iso, quiet=args.quiet)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
