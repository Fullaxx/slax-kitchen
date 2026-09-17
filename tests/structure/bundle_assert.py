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
GENERATED = "98-dpkg-db.sb"


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



# Bundles built by a privilege: none verb, plus the generated database. Everything in
# these comes from the author's filesystem, a downloaded archive, or nothing at all, so
# there is no ownership worth preserving and they are built with -all-root. The two
# chroot verbs are deliberately NOT here: dpkg and a recipe's script set ownership on
# purpose, and flattening it is what made users-and-auth ship a home directory its own
# user could not enter.
def entries(sb: str) -> list[tuple[str, int, int, str]]:
    """(mode, uid, gid, path) for every entry in a bundle. Needs no privilege."""
    r = subprocess.run(["unsquashfs", "-lln", sb], capture_output=True, text=True)
    if r.returncode != 0:
        return []
    # -rwsr-xr-x 1000/1000    1 2026-09-15 22:32 squashfs-root/usr/bin/evil
    #     [0]       [1]       [2]     [3]    [4]           [5]
    # The uid/gid pair is field 1. An earlier version read field 2, so every line was
    # skipped, both assertions reported "0 entries" and passed on a bundle built to fail
    # them -- which is the exact shape of check this project treats as worse than none.
    out = []
    for ln in r.stdout.splitlines():
        f = ln.split(None, 5)
        if len(f) < 6 or "/" not in f[1]:
            continue
        uid, _, gid = f[1].partition("/")
        if not uid.isdigit() or not gid.isdigit():
            continue
        out.append((f[0], int(uid), int(gid), f[5]))
    return out


def setuid_or_setgid(mode: str) -> bool:
    """`unsquashfs -lln` renders the bits in the x columns, as ls does."""
    return len(mode) >= 10 and (mode[3] in "sS" or mode[6] in "sS")


def image_path(p: str) -> str:
    """`squashfs-root/usr/bin/su` as an absolute path in the image: `/usr/bin/su`."""
    rel = p[len("squashfs-root"):] if p.startswith("squashfs-root") else p
    return rel or "/"


USR_ALIASES = ("bin", "sbin", "lib", "lib32", "lib64", "libx32")


def usr_merged(p: str) -> str:
    """A path in the /usr form, so a package database and a filesystem can be compared.

    Debian 12 is usr-merged and dpkg still records what the .deb shipped: `/bin/su` for a
    file the image has at `/usr/bin/su`. Slackware is not merged and really does have
    `/bin/mount`, which its own database records as `bin/mount`. Normalising BOTH sides
    the same way makes either database answer for either layout.
    """
    head, _, rest = p.lstrip("/").partition("/")
    return f"/usr/{head}/{rest}" if head in USR_ALIASES and rest else p


def unowned_privileged(rows: list, owned: set) -> list:
    """setuid/setgid FILES in `rows` that no package owns.

    THE QUESTION IS PROVENANCE, NOT THE BITS. Bundles are mounted as root at every boot,
    and not everything in one comes from a package: `bundle.fromTarball` takes an archive
    whose `sha256:` is optional and refuses setuid members itself, and a bundle.script can
    chmod anything. What is worth a human look is a privileged file with no package behind
    it -- so the package database decides, and packaged content passes whoever owns it.

    Directories are not tested. setgid on a directory means files created inside inherit
    its group; it hands nobody any privilege. Debian sets it on /var/lib/tor (02700,
    debian-tor) and on /var/mail, and tor refuses to start if its data directory is not
    owned by the user it drops to -- the first version of this rule flagged every entry
    owned by a non-root uid and failed the tor recipe for obeying its own package.

    If this misfires on packaged content again, the agreed next step is to report rather
    than fail.
    """
    return [(m, u, g, p) for m, u, g, p in rows
            if m.startswith("-") and setuid_or_setgid(m)
            and usr_merged(image_path(p)) not in owned]


def packaged_paths(mods: str, names: list) -> set:
    """Every path a package database records across the stack, in the /usr form.

    Read from the bundles rather than from a merged database: a package's file list
    travels with the bundle that installed it, and each stock bundle carries its own.
    Both flavours are read, because both ship setuid binaries -- dpkg's
    `var/lib/dpkg/info/*.list` (absolute paths), and Slackware's
    `var/lib/pkgtools/packages/<pkg>` (relative paths, after a `FILE LIST:` header).
    """
    owned: set = set()
    for n in names:
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["unsquashfs", "-q", "-f", "-d", d, os.path.join(mods, n),
                            "var/lib/dpkg/info", "var/lib/pkgtools/packages"],
                           capture_output=True)
            info = os.path.join(d, "var", "lib", "dpkg", "info")
            if os.path.isdir(info):
                for f in sorted(os.listdir(info)):
                    if not f.endswith(".list"):
                        continue
                    with open(os.path.join(info, f), errors="replace") as fh:
                        owned.update(usr_merged(ln.strip()) for ln in fh if ln.strip())
            pkgs = os.path.join(d, "var", "lib", "pkgtools", "packages")
            if os.path.isdir(pkgs):
                for f in sorted(os.listdir(pkgs)):
                    listing = False
                    with open(os.path.join(pkgs, f), errors="replace") as fh:
                        for ln in fh:
                            if ln.startswith("FILE LIST:"):
                                listing = True
                            elif listing and ln.strip():
                                owned.add(usr_merged("/" + ln.strip().lstrip("./")))
    return owned


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

    # Ownership. Until this existed nothing anywhere -- no gate, no structure assert, no
    # unit test -- looked at uid, gid or mode inside a built bundle, which is why three
    # separate defects lived in that blind spot at once: a home directory shipped
    # root:root, a setuid helper shipped with its bit stripped, and a generated database
    # shipped owned by whichever uid ran `kitchen pack`.
    #
    # WHAT CAN HONESTLY BE ASSERTED FROM A .sb ALONE is narrower than it looks: nothing
    # in the artifact says which verb built it, so "this one should be all-root" is not
    # a question this file can answer. Two invariants that hold regardless:
    print()
    gen = os.path.join(mods, GENERATED)
    if os.path.isfile(gen):
        rows = entries(gen)
        owned = [(u, g, p) for _m, u, g, p in rows if u != 0 or g != 0]
        t.check(not owned,
                f"{GENERATED:<18} {len(rows)} entries, all uid 0 gid 0",
                "non-root: " + ", ".join(f"{p} ({u}:{g})" for u, g, p in owned[:4])
                if owned else "")

    # A setuid or setgid file that no package owns does not belong in a bundle: a bundle
    # is mounted as root at every boot, and this is what would catch -all-root being
    # disabled on a verb whose source can carry setuid, which is the trap that made #4 and
    # #8 inseparable. Packaged content passes -- dpkg's lists are the authority on what is
    # meant to be there. See unowned_privileged() for why directories are not tested.
    #
    # Candidates first: the dpkg lookup unpacks each bundle's info directory, so it is
    # only paid when a bundle actually holds a privileged file.
    cand = {n: [e for e in entries(os.path.join(mods, n))
                if e[0].startswith("-") and setuid_or_setgid(e[0])] for n in names}
    owned = packaged_paths(mods, names) if any(cand.values()) else set()
    for n in names:
        priv = unowned_privileged(cand[n], owned)
        t.check(not priv,
                f"{n:<18} no setuid/setgid file that no package owns",
                "found: " + ", ".join(f"{p} ({m} {u}:{g})" for m, u, g, p in priv[:4])
                if priv else "")

    print(f"\n{t.ok} passed, {len(t.fail)} failed")
    return 1 if t.fail else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
