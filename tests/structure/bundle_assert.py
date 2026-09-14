#!/usr/bin/env python3
"""Assert that a work tree's bundle stack composes. No booting, no mounting.

A union filesystem composes TREES, not FILES. Any single file holding whole-system
state can only ever be right in the topmost bundle that ships it, so a bundle that
carries such a file and sits above a more complete copy silently replaces it.

On Debian that file is var/lib/dpkg/status, and it is cumulative by construction:
upstream builds each bundle by chrooting into everything below it, so the counts climb
295 -> 307 -> 424 -> 535 -> 575 -> 600 and the top one is complete. Add a bundle built
from a shorter stack and dpkg loses the difference -- about three hundred packages, with
no error anywhere.

Slackware has no equivalent: var/lib/pkgtools/packages/ is one file per package, so
bundles merge instead of shadowing. This check reports n/a there rather than inventing
an invariant.

Exit 0 if every assertion holds.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

DPKG_STATUS = "var/lib/dpkg/status"


def sortmod(names: list[str]) -> list[str]:
    """Order bundles the way livekitlib's sortmod does: numeric prefix, then name.

    sortmod is `sed 's,(.*/(.*)),\\2:\\1,' | sort -n`, so the key is the basename's
    leading number and ties fall back to a full string compare. Two bundles sharing a
    prefix -- 01-core and 01-firmware, or 07-branding and 07-extras -- are therefore
    ordered alphabetically, which is rarely what the author expected.
    """
    def key(n: str) -> tuple[int, str]:
        m = re.match(r"^(\d+)", n)
        return (int(m.group(1)) if m else 0, n)
    return sorted(names, key=key)


def read_status(sb: str) -> str | None:
    """Extract one file from a squashfs. Needs no privilege -- it is a plain read."""
    tmp = tempfile.mkdtemp(prefix="kitchen-bassert.")
    try:
        d = os.path.join(tmp, "x")
        subprocess.run(["unsquashfs", "-n", "-d", d, sb, DPKG_STATUS],
                       capture_output=True, text=True)
        p = os.path.join(d, DPKG_STATUS)
        if not os.path.isfile(p):
            return None
        with open(p, encoding="utf-8", errors="replace") as f:
            return f.read()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def packages(text: str) -> set[str]:
    """The (name, architecture) keys a status file claims are known to dpkg.

    Architecture matters: multiarch means wine32:i386 and wine64:amd64 are different
    entries, and a merge keyed on the name alone would drop one of them.
    """
    out, name, arch = set(), None, ""
    for stanza in re.split(r"\n\s*\n", text):
        if not stanza.strip():
            continue
        name, arch = None, ""
        for line in stanza.splitlines():
            if line.startswith("Package:"):
                name = line.split(":", 1)[1].strip()
            elif line.startswith("Architecture:"):
                arch = line.split(":", 1)[1].strip()
        if name:
            out.add(f"{name}:{arch}" if arch else name)
    return out


class Asserter:
    def __init__(self) -> None:
        self.fail: list[str] = []
        self.ok = 0

    def check(self, cond: bool, msg: str, detail: str = "") -> None:
        if cond:
            self.ok += 1
            print(f"  ok   {msg}")
        else:
            self.fail.append(msg + (f"  ({detail})" if detail else ""))
            print(f"  FAIL {msg}" + (f"  ({detail})" if detail else ""))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="bundle_assert.py",
                                 description="assert a bundle stack composes")
    ap.add_argument("tree", help="work tree, or a slax/modules directory")
    args = ap.parse_args(argv[1:])

    mods = args.tree
    for cand in (os.path.join(args.tree, "iso", "slax", "modules"),
                 os.path.join(args.tree, "slax", "modules")):
        if os.path.isdir(cand):
            mods = cand
            break
    if not os.path.isdir(mods):
        print(f"  FAIL no slax/modules under {args.tree}")
        return 1

    names = sortmod([n for n in os.listdir(mods) if n.endswith(".sb")])
    if not names:
        print(f"  FAIL no bundles in {mods}")
        return 1

    t = Asserter()
    print(f"bundle stack: {len(names)} bundles in {mods}")

    # Load order is the numeric prefix and higher wins, so walk upward and require each
    # dpkg database to be at least as complete as the one it is about to shadow.
    seen: list[tuple[str, set[str]]] = []
    for n in names:
        text = read_status(os.path.join(mods, n))
        if text is None:
            print(f"  --   {n:<18} ships no {DPKG_STATUS}")
            continue
        pkgs = packages(text)
        if not seen:
            print(f"  ok   {n:<18} {len(pkgs)} packages (base)")
            t.ok += 1
        else:
            prev_name, prev = seen[-1]
            lost = prev - pkgs
            t.check(not lost,
                    f"{n:<18} {len(pkgs)} packages, shadows {prev_name} ({len(prev)})",
                    f"drops {len(lost)}: " + ", ".join(sorted(lost)[:6])
                    + ("..." if len(lost) > 6 else "") if lost else "")
        seen.append((n, pkgs))

    if not seen:
        print("  --   no dpkg database in any bundle (Slackware packs one file per "
              "package, which merges in the union)")

    print(f"\n{t.ok} passed, {len(t.fail)} failed")
    return 1 if t.fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
