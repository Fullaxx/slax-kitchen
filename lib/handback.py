#!/usr/bin/env python3
"""Give back to the user who ran `sudo` what a kitchen command wrote as root (#70).

Under sudo, unpack, apply, pack, build and test write as root into that user's work tree
and output directory, and nothing gave the files back. The next unprivileged step met a
journal, a provenance record and a bundle it could not write, and a directory it could not
remove. CI's boot job chowned out/ and work/ after its build, slax-wine's build.sh chowns
its tree after every apply, and publishing-images.md had a publisher `sudo chown -R` out/.
This does what those did, once, here, as the command ends: on success, on failure, and on
INT, TERM and HUP. The trap in `kitchen` makes those exits for the shell commands, with PIPE
too (#98), and run() makes them exits for the Python ones. docs/90-reference/handback.md
says what it gives back, what it never touches, and the endings it cannot survive, as
measured.

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

Usage: handback.py [--new PATH | PATH]...
  --new names a path this run created. The shell side decides that as it registers each
  path, so the two come in any order (main, below). apply.py and fetch.py go through run(),
  and so do shell.py and diff.py, which give nothing back (#98).
  fetch gave nothing back until #79, and gives back only what it wrote, since its -o can
  name a place like ~/Downloads.
"""
from __future__ import annotations

import errno
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


def _drop_dead_streams() -> None:
    """Point stdout or stderr at /dev/null when the terminal behind it has hung up.

    A real hangup -- the terminal closed, or an ssh session dropped -- makes every later write
    to that terminal fail with EIO. The exit the hangup starts runs cleanup that prints, and a
    print raised in the middle of it: give_back's own messages are such prints. And CPython
    flushes stdout and stderr as it exits, so the failed flush there made the exit status
    120, not the 129 run() promises. Measured 2026-10-23, on a pty whose master was closed:
    a print to either stream raised EIO, and the exit was 120, three runs in three.

    A write of zero bytes tells a hung-up terminal (EIO) from a live one, a file or a pipe,
    and writes nothing. It works on the descriptor and never on Python's buffered stream,
    because CPython's buffered writers are not reentrant. Measured 2026-10-23: a flush from a
    signal handler that landed inside a write to the same stream raised "reentrant call
    inside <_io.BufferedWriter name='<stdout>'>", three runs in three. What is still
    buffered goes to /dev/null at the final flush, where a dead terminal would have refused
    it anyway. boot_host.py does the same for a pipe closed under `kitchen test ... | head`.
    """
    for fd in (1, 2):
        try:
            os.write(fd, b"")
        except OSError as e:
            if e.errno != errno.EIO:
                continue
            try:
                devnull = os.open(os.devnull, os.O_WRONLY)
                os.dup2(devnull, fd)
                os.close(devnull)
            except OSError:
                pass


def run(main, argv: list, paths: list, created: list = ()) -> int:
    """Run an entry point's main(argv), and give back `paths` and `created` as it ends,
    however it ends: a return, an exception, INT, TERM or HUP. The lists are read when
    main() is done, so it fills them as it goes.

    TERM and HUP become exits here, as the trap in `kitchen` makes them for the shell
    commands, with the shell's status for a process a signal ended: 128 + the signal.
    Python runs no `finally` on a default SIGTERM or SIGHUP, and sudo passes both on. An
    apply ended by TERM gave nothing back and left its 1.4 GiB build chroot behind (#81),
    because both are done in a `finally`, and HUP, which a dropped ssh session or a closed
    terminal sends, did the same (#83). fetch had its own copy of these lines (#79), and
    this is the one copy now. The previous handlers are put back, for a caller in the same
    process.

    A signal ignored on entry stays ignored, as it does for the shell's trap, which POSIX
    does not let a script set for one. Under nohup HUP is ignored, and that means carry on:
    the first version of #83's fix made it an exit, and ended the run nohup was keeping.

    After a real hangup the terminal is gone and every write to it fails, so the handler
    first points a stream whose terminal has hung up at /dev/null (_drop_dead_streams). The
    cleanup can then print, and the exit keeps its status. Until then a closed terminal
    gave everything back but exited 120, as #83's real-hangup measurement found on
    2026-10-21.
    """
    import signal

    def _exit(signum, _frame):
        _drop_dead_streams()
        sys.exit(128 + signum)

    previous = {}
    for s in (signal.SIGTERM, signal.SIGHUP):
        if signal.getsignal(s) is not signal.SIG_IGN:
            previous[s] = signal.signal(s, _exit)
    try:
        return main(argv)
    finally:
        try:
            give_back(paths, created=created)
        finally:
            for s, handler in previous.items():
                signal.signal(s, handler)


def remove_tree(path: str) -> None:
    """Remove a scratch tree, holding INT, TERM and HUP until it is gone.

    Each of the three is an exit: run() makes TERM and HUP one, and Python makes INT a
    KeyboardInterrupt. An exit raised in the middle of shutil.rmtree stops it there, and
    ignore_errors catches only OSError. So a chroot step's build chroot, removed in a
    `finally` once its bundle was built, kept whatever the removal had not reached. That
    was outside the work tree apply gives back, and nothing removed it later. Measured
    2026-10-23: a TERM 0.05 s into the removal left 1.8 GiB and 28,951 paths root's, and
    the same window did it five times in five (#98).

    Held, a signal waits for the removal, which took 0.85 s for that chroot. It is then
    delivered as the mask is put back, and ends the command as it would have.
    """
    import shutil
    import signal
    held = {signal.SIGINT, signal.SIGTERM, signal.SIGHUP}
    previous = signal.pthread_sigmask(signal.SIG_BLOCK, held)
    try:
        shutil.rmtree(path, ignore_errors=True)
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, previous)


def main(argv: list[str]) -> int:
    """The shell side's entry. `kitchen`'s EXIT trap passes every path its command
    registered, in the order it registered them, each one plain or after --new.

    That is any order, because which one a path is depends on whether it existed.
    parse_args() fills a positional once, at the first run of plain paths, so plain paths
    on both sides of a --new one were refused: `handback.py A --new B C` exited 2,
    "unrecognized arguments: C", and gave nothing. `kitchen build` registers work/ and
    out/, then the tree unpack makes, then what pack and test write in. So from 51c5699 on,
    every build that got as far as pack, where work/ or out/ already existed, ended that
    way (#97). Measured 2026-10-23: a new image, its provenance record and pack's
    98-dpkg-db.sb stayed root's. parse_intermixed_args() takes the two in any order.
    """
    import argparse
    ap = argparse.ArgumentParser(prog="handback.py")
    ap.add_argument("--new", action="append", default=[], metavar="PATH")
    ap.add_argument("paths", nargs="*")
    a = ap.parse_intermixed_args(argv[1:])
    give_back(a.paths, created=a.new)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
