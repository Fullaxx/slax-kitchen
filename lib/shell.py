#!/usr/bin/env python3
"""kitchen shell -- chroot into a bundle, or into a stack of them, to look around.

Answering "what is actually IN 03-desktop?" otherwise means unsquashfs to a temp
directory, remembering to make the device nodes, remembering to clean up, and doing it
again tomorrow. This is that, with the cleanup guaranteed.

IT IS A THROWAWAY, and that is the whole design. The bundles are unpacked into a
temporary directory that is deleted on exit; the .sb files are opened read-only and
never written. The obvious expectation of a command called `shell` is that edits stick,
so the banner says they do not, every time, and the exit message says it again. If you
want changes captured, `bundle.script` runs a script in exactly this environment and
packs the delta into a new bundle -- that is the supported path, and it is reproducible,
which a session of typing never is.

Needs CAP_SYS_CHROOT and CAP_MKNOD, the same as bundle.packages. proot is not an
alternative: it does not translate statx(), so stat reads the host filesystem from
inside the fake root.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _bundles_in(tree: str) -> list:
    """Every .sb in a work tree or a directory, in load order.

    Load order is the numeric prefix and higher wins, so the stack is built in
    ascending order -- later unsquashfs calls overwrite earlier ones, which reproduces
    what aufs does at boot without needing a union mount.
    """
    for cand in (os.path.join(tree, "iso", "slax", "modules"),
                 os.path.join(tree, "slax", "modules"),
                 tree):
        if os.path.isdir(cand):
            sb = [os.path.join(cand, n) for n in os.listdir(cand) if n.endswith(".sb")]
            if sb:
                return sorted(sb, key=lambda p: os.path.basename(p))
    return []


def _pick(all_sb: list, want: str | None) -> list:
    if not want:
        return all_sb
    picked = []
    for w in want.split(","):
        w = w.strip()
        hit = [p for p in all_sb if os.path.basename(p) == w
               or os.path.basename(p) == w + ".sb"
               or re.match(re.escape(w), os.path.basename(p))]
        if not hit:
            names = ", ".join(os.path.basename(p) for p in all_sb)
            raise SystemExit(f"no bundle matching {w!r}. Present: {names}")
        picked.append(hit[0])
    return picked


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        prog="kitchen shell",
        description="chroot into a bundle (or a stack of them) to explore it")
    ap.add_argument("target", help="a .sb bundle, a work tree, or a directory of bundles")
    ap.add_argument("--stack", help="comma-separated bundles to layer, low to high "
                                    "(default: all of them, in load order)")
    ap.add_argument("-c", "--command", help="run this instead of an interactive shell")
    a = ap.parse_args(argv[1:])

    for tool in ("unsquashfs", "chroot"):
        if not shutil.which(tool):
            print(f"kitchen shell: {tool} not installed", file=sys.stderr)
            return 2

    if not os.path.exists(a.target):
        print(f"kitchen shell: no such path: {a.target}", file=sys.stderr)
        return 2

    if os.path.isfile(a.target):
        if not a.target.endswith(".sb"):
            print(f"kitchen shell: not a bundle: {a.target}", file=sys.stderr)
            return 2
        picked = [a.target]
    else:
        found = _bundles_in(a.target)
        if not found:
            print(f"kitchen shell: no .sb bundles under {a.target}", file=sys.stderr)
            return 2
        picked = _pick(found, a.stack)

    import apply as ap_mod  # reuse the chroot preparation bundle.script already uses

    work = tempfile.mkdtemp(prefix="kitchen-shell-")
    root = os.path.join(work, "root")
    os.makedirs(root)
    rc = 0
    try:
        for p in picked:
            r = subprocess.run(["unsquashfs", "-f", "-n", "-q", "-d", root, p],
                               capture_output=True, text=True)
            if r.returncode != 0:
                print(f"unsquashfs {os.path.basename(p)}: {r.stderr.strip()[:300]}",
                      file=sys.stderr)
                return 2
        try:
            ap_mod._prepare_chroot(root)
        except RuntimeError as e:
            print(f"kitchen shell: {e}", file=sys.stderr)
            print("  this needs CAP_MKNOD; see docs/40-workflow/container-vs-host.md",
                  file=sys.stderr)
            return 2

        names = " + ".join(os.path.basename(p) for p in picked)
        nfiles = sum(len(f) for _d, _s, f in os.walk(root))

        # Only 01-core carries a userland. Every other bundle is a fragment meant to be
        # stacked on it, so chrooting into one alone gives "chroot: failed to run
        # command '/bin/sh'" -- true, and no help at all. Say what to stack instead.
        if not os.path.exists(os.path.join(root, "bin", "sh")):
            print(f"kitchen shell: {names} has no /bin/sh.", file=sys.stderr)
            print("  Only 01-core ships a userland; every other bundle is a fragment",
                  file=sys.stderr)
            print("  meant to be layered on it. Stack it on a base:", file=sys.stderr)
            base = None
            if not os.path.isfile(a.target):
                have = [os.path.basename(x) for x in _bundles_in(a.target)]
                base = next((h for h in have if h.startswith("01-")), None)
            if base:
                want = ",".join([base] + [os.path.basename(p) for p in picked])
                print(f"    kitchen shell {a.target} --stack {want}", file=sys.stderr)
            else:
                print(f"    kitchen shell <work-tree> --stack 01-core,"
                      f"{os.path.basename(picked[-1])}", file=sys.stderr)
                print("  (a lone .sb has no sibling 01-core to stack under it)",
                      file=sys.stderr)
            return 2
        if not a.command:
            print(f"kitchen shell: {names}")
            print(f"  {nfiles:,} files, stacked low to high (higher wins, as at boot)")
            print(f"  THROWAWAY: {root} is deleted on exit and the .sb files are never")
            print("  written. To capture changes, use the bundle.script verb instead.")
            print("  exit or Ctrl-D to leave.")

        argvv = ["/bin/sh", "-c", a.command] if a.command else ["/bin/sh", "-i"]
        env = dict(os.environ)
        env.update({"PS1": r"(kitchen:\W) # ", "LC_ALL": "C", "LANG": "C",
                    "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                    "KITCHEN_SHELL": names})
        # stdio is inherited on purpose: this is the one command that is interactive.
        rc = subprocess.run(["chroot", root] + argvv, env=env).returncode
        if not a.command:
            print(f"kitchen shell: left {names}; nothing was saved.")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
