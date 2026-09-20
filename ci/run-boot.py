#!/usr/bin/env python3
"""Run one boot, own it, and answer for anything it leaves running.

    python3 ci/run-boot.py ./kitchen test out/x.iso --kernel --out out/tier-c ...

WHY. ci/tier-c.sh started each boot as an ordinary foreground child, let the pid go, and
reconstructed it afterwards out of $OUT/pids/*.pid. That forced two questions it had
created for itself -- is this pid still alive, and is it still ours -- and both are hard:

  - a ZOMBIE answers `kill -0` and names nothing, so the sweep reported its own corpse as
    somebody else's guest, and the gates job went red on master -- twice, the second a
    deliberate re-run, deterministic on the runner and green on the machine it was written
    on (09801bc);
  - a pid is a number a busy machine RECYCLES, so "only pids this run wrote" was never
    true, and the guard bolted on afterwards was only ever as specific as $OUT (622891b).

Five defects came out of those two questions. Neither needed asking: the boot is this
script's own child, and a process you started is a process you know.

ONE PROCESS GROUP, WHOSE ID IS THE CHILD'S PID. start_new_session puts the boot and
everything it starts into a group of its own. Nothing in the local boot chain detaches --
tier-c.sh -> `sh kitchen` -> python3 qemu_boot.py -> qemu, and not one of them calls
setsid or start_new_session -- so a guest orphaned by its launcher being killed is
reparented to init and STAYS IN THE GROUP. Measured rather than assumed: the launcher
SIGKILLed, the guest reparented to pid 1, its process group unchanged, and killpg reaches
it.

WHAT THAT BUYS, EXACTLY -- and it is worth being precise, because one of the two
questions goes away entirely and the other only gets easier:

  is it ours?      GONE. Nothing is searched for and nothing is matched. Only this boot's
                   processes are in this boot's group, and the group id cannot be recycled
                   underneath us: the kernel keeps a pid reserved while a process group
                   still refers to it, so a non-empty group is always the one we made.
  is it alive?     STILL A QUESTION, because killpg(pgid, 0) succeeds while the group has
                   any member and an unreaped zombie is a member. _group_running() below
                   answers it -- by reading /proc for processes ALREADY KNOWN to be ours,
                   never to work out which process to signal.

SIGNALS ARE FORWARDED, because the group is the point. A new session means Ctrl-C at the
terminal no longer reaches the boot on its own -- that used to happen for free, everything
being in one group -- so it is passed on deliberately. Wrong here means a guest surviving
an interrupt, which is the failure this file exists to prevent.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

def _group_alive(pgid: int) -> bool:
    """Is anything still in the group? Signal 0 asks without sending anything."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Something in the group is no longer ours to signal. That should not happen for a
        # group we created, and answering "alive" is the safe direction: it reports a leak
        # rather than hiding one.
        return True


def _group_running(pgid: int) -> bool:
    """Is any member of this group actually RUNNING, rather than a corpse?

    killpg(pgid, 0) succeeds while the group has any member at all, and an unreaped zombie
    is a member. That is precisely how the old sweep reported a guest which had already
    stopped, and how the gates job went red on a runner twice (09801bc): a process orphaned
    by its launcher is reparented to init, so whether its corpse lingers depends on whether
    that init reaps -- microseconds under systemd, forever in a container whose pid 1 does
    not. Found again here, by probing this very code, before it could reach CI.

    THE GROUP ANSWERS *WHO*, THIS ANSWERS *WHETHER*, and the difference is the whole point
    of the redesign. Nothing is searched for and nothing is matched: every pid read here is
    already known to be ours, because it is in a process group this process created. The
    old code read /proc to work out whether a pid it had found was plausibly a guest of
    ours; this reads it only to tell a live process from a dead one.

    Unreadable /proc answers "running": over-reporting a leak costs a message, and
    under-reporting one is the bug this exists to prevent.

    Copy-of: lib/boot_host.py  _group_alive _group_running _end_group
    Its _group_alive says why the two cannot be shared, and how to check they have not
    drifted again. They did once, within an hour of being written.
    """
    try:
        entries = os.listdir("/proc")
    except OSError:
        return True
    for entry in entries:
        if not entry.isdigit():
            continue
        try:
            with open(f"/proc/{entry}/stat") as fh:
                # After the LAST ')' come state, ppid and pgrp: a comm may itself contain
                # spaces and parentheses, so anything counting from the left is wrong.
                fields = fh.read().rsplit(") ", 1)[1].split()
            if fields[0] != "Z" and int(fields[2]) == pgid:
                return True
        except (OSError, IndexError, ValueError):
            continue
    return False


def _end_group(pgid: int, grace_s: float = 10.0) -> None:
    """Ask the group to stop, then insist.

    grace_s is what the guest gets to answer SIGTERM. qemu answers promptly, so it only
    matters for one that has already stopped listening; SIGKILL then gets two seconds,
    which is the kernel's work rather than the guest's.
    """
    for sig, wait_s in ((signal.SIGTERM, grace_s), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pgid, sig)
        except OSError:
            return
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            # _group_running, NOT _group_alive: killpg counts an unreaped corpse as a
            # member, so waiting on it meant waiting the whole grace period every time the
            # guest died instantly and its parent had not reaped it yet. Measured at 12 s
            # per teardown before this, on a machine whose init reaps in microseconds --
            # and a container's pid 1 often does not reap at all, which is the case this
            # runs in. Third place the same zombie has been found (09801bc, then the leak
            # check, then here).
            if not _group_running(pgid):
                return
            time.sleep(0.1)


def main(argv: list[str]) -> int:
    args = argv[1:]
    # NAMED, NOT COUNTED -- which was the point of 622891b and is kept. The old sweep
    # named the pid FILE, which encoded the boot mode; the caller knows that name without
    # anything having to be read back, so it is simply passed in.
    label = ""
    if len(args) >= 2 and args[0] == "--label":
        label, args = args[1], args[2:]
    if args and args[0] == "--":
        args = args[1:]
    if not args:
        print("usage: run-boot.py [--label NAME] <command> [args...]", file=sys.stderr)
        return 2
    try:
        boot = subprocess.Popen(args, start_new_session=True)
    except OSError as e:
        print(f"run-boot: cannot run {args[0]}: {e}", file=sys.stderr)
        return 2
    pgid = boot.pid          # the session leader is the group, by construction

    stopped: list = []

    def forward(signum, _frame):
        stopped.append(signum)
        try:
            os.killpg(pgid, signum)
        except (ProcessLookupError, PermissionError):
            pass

    previous = {}
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        previous[signum] = signal.signal(signum, forward)
    try:
        rc = boot.wait()
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
    # A CHILD KILLED BY A SIGNAL COMES BACK NEGATIVE, and passing that on is how this
    # first reported an interrupted boot as "exit 254": Popen.wait() answers -N for signal
    # N, and SystemExit(-2) leaves the shell 254. ci/tier-c.sh reads anything >= 2 as "the
    # machinery could not run", so the number has to mean what a shell means by it.
    if rc < 0:
        rc = 128 - rc

    # THE BOOT HAS BEEN REAPED, so anything still in its group outlived it. That is the
    # whole leak test, and it is one syscall.
    if _group_alive(pgid) and _group_running(pgid):
        who = f"{label}: " if label else ""
        print(f"LEAK {who}a guest was left running in this boot's process group "
              f"({pgid}); it has been stopped", file=sys.stderr)
        _end_group(pgid)
        if _group_running(pgid):
            print(f"LEAK {who}process group {pgid} would not stop", file=sys.stderr)
        if rc < 2:
            # 0 and 1 are the boot's own verdict; 2 and above mean the machinery could not
            # run at all and must reach ci/tier-c.sh unchanged, so it can stop before
            # writing a ledger row about a boot that never happened.
            rc = 1
    if stopped and rc == 0:
        rc = 128 + stopped[0]
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
