#!/usr/bin/env python3
"""Give back to the user who ran `sudo` what a kitchen command wrote as root (#70).

Under sudo, unpack, apply, pack, build and test write as root into that user's work tree
and output directory, and nothing gave the files back. The next unprivileged step met a
journal, a provenance record and a bundle it could not write, and a directory it could not
remove. CI's boot job chowned out/ and work/ after its build, slax-wine's build.sh chowns
its tree after every apply, and publishing-images.md had a publisher `sudo chown -R` out/.
This does what those did, once, here, whether the command succeeded or not.

What it changes is every path under the given ones that root owns, to SUDO_UID:SUDO_GID:
- only when this process is root and SUDO_UID names someone else, so root in a container,
  or a plain user, changes nothing;
- only in a place that is that user's: a directory this run created, or one the user owns
  or that sits in one the user owns. `sudo kitchen apply -w /` or `-w /etc` gives nothing
  away, and neither does a work tree under /srv that root owns;
- only root's paths, so a file anyone else owns is left alone, and not a regular file with
  another name, which this run did not write -- it could be a hardlink to one of the host's;
- symlinks are changed themselves and never followed, and the walk stays on the
  filesystem it started on;
- setuid and setgid bits, which chown clears, are put back.

It cannot change an image: pack records every file as root's (-uid 0 -gid 0), as the
stock image does, so who owns the tree never reaches the ISO.

Usage: handback.py [--new PATH]... [PATH]...
  --new names a path this run created. The shell side decides that as it registers each
  path; apply.py and fetch.py call give_back themselves. fetch gave nothing back until #79,
  and gives back only what it wrote, since its -o can name a place like ~/Downloads.
"""
from __future__ import annotations

import os
import stat
import sys


def invoker(env=None) -> tuple[int, int] | None:
    """(uid, gid) of the user who ran sudo, or None when there is nobody to give back to."""
    env = os.environ if env is None else env
    uid, gid = env.get("SUDO_UID", ""), env.get("SUDO_GID", "")
    if os.geteuid() != 0 or not uid.isdigit() or int(uid) == 0 or not gid.isdigit():
        return None
    return int(uid), int(gid)


def _under(top: str):
    """`top` and every path under it, never through a symlink and never onto another
    filesystem: a mount inside a work tree is not the work tree."""
    first = os.lstat(top)
    yield top
    if not stat.S_ISDIR(first.st_mode):
        return
    for d, dirs, files in os.walk(top):
        keep = []
        for n in dirs + files:
            p = os.path.join(d, n)
            st = os.lstat(p)
            if st.st_dev != first.st_dev:
                continue
            yield p
            if n in dirs and stat.S_ISDIR(st.st_mode):
                keep.append(n)
        dirs[:] = keep


def _theirs(top: str, uid: int) -> bool:
    """Whether `top` is a place of the user's: theirs, or in a directory that is."""
    parent = os.path.dirname(os.path.abspath(top))
    return os.lstat(top).st_uid == uid or os.lstat(parent).st_uid == uid


def give_back(paths=(), env=None, created=()) -> int:
    """Give every root-owned path under `paths` and `created` to the user who ran sudo, and
    say how many. `created` are the ones this run made, which are the user's by that fact;
    any other is given back only where it is the user's place (_theirs). A path that cannot
    be changed is named and skipped: this runs as a command ends, and must not hide why it
    ended."""
    who = invoker(env)
    if who is None:
        return 0
    uid, gid = who
    n = 0
    tops = [(p, True) for p in created] + [(p, False) for p in paths]
    for top, made in tops:
        if not top or not os.path.lexists(top) or not (made or _theirs(top, uid)):
            continue
        for p in _under(top):
            try:
                st = os.lstat(p)
                if st.st_uid != 0 or (stat.S_ISREG(st.st_mode) and st.st_nlink > 1):
                    continue
                os.lchown(p, uid, gid)
                if stat.S_ISREG(st.st_mode) and st.st_mode & (stat.S_ISUID | stat.S_ISGID):
                    os.chmod(p, stat.S_IMODE(st.st_mode))
                n += 1
            except OSError as e:
                print(f"handback: {p}: {e}", file=sys.stderr)
    if n:
        print(f"  gave {n} path(s) back to uid {uid}, who ran sudo", file=sys.stderr)
    return n


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(prog="handback.py")
    ap.add_argument("--new", action="append", default=[], metavar="PATH")
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args()
    give_back(a.paths, created=a.new)
