#!/usr/bin/env python3
"""Download and verify a base Slax ISO.

Verification is not optional: size AND sha256 are checked against compat/sources.yaml
before the file is accepted, so a compromised or merely stale mirror cannot poison a
build. A file that already exists and verifies is left alone.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import handback  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Progress bars are for humans. In CI stderr is a file, and rewriting the line with \r
# just appends a new line for every percent -- four ISOs produced hundreds of lines of
# "verifying 37%" noise before this was gated.
TTY = sys.stderr.isatty()


def load_sources() -> dict:
    import yaml
    p = os.path.join(ROOT, "compat", "sources.yaml")
    if not os.path.isfile(p):
        raise RuntimeError(f"missing {p}")
    return yaml.safe_load(open(p))


def sha256(path: str, progress: bool = False) -> str:
    h = hashlib.sha256()
    total = os.path.getsize(path)
    done = 0
    show = progress and TTY
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
            done += len(chunk)
            if show and total:
                print(f"\r  verifying {100 * done // total}%", end="", file=sys.stderr)
    if show:
        print("\r" + " " * 24 + "\r", end="", file=sys.stderr)
    return h.hexdigest()


def verify(path: str, want_sha: str, want_size: int) -> str | None:
    """Return None if good, else a human-readable reason."""
    if not os.path.isfile(path):
        return "missing"
    got_size = os.path.getsize(path)
    if got_size != want_size:
        return f"size {got_size} != expected {want_size}"
    got = sha256(path, progress=True)
    if got != want_sha:
        return f"sha256 {got[:16]}... != expected {want_sha[:16]}..."
    return None


def download(url: str, dest: str) -> None:
    tmp = dest + ".part"
    start = time.time()
    with urllib.request.urlopen(url, timeout=120) as r:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if TTY and total:
                    rate = done / max(time.time() - start, 0.01) / 1048576
                    print(f"\r  {100 * done // total}%  {done // 1048576}/{total // 1048576} MiB"
                          f"  {rate:.1f} MiB/s", end="", file=sys.stderr)
    if TTY:
        print("\r" + " " * 48 + "\r", end="", file=sys.stderr)
    else:
        elapsed = time.time() - start
        print(f"  downloaded {done // 1048576} MiB in {elapsed:.0f}s", file=sys.stderr)
    os.replace(tmp, dest)


def _first_missing(path: str) -> str | None:
    """The highest directory of `path` that does not exist yet, which makedirs will create:
    the one directory this run makes for the user, with everything it puts under it."""
    path, top = os.path.abspath(path), None
    while not os.path.lexists(path):
        top, parent = path, os.path.dirname(path)
        if parent == path:
            break
        path = parent
    return top


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="kitchen fetch", description="download + verify a base Slax ISO")
    ap.add_argument("target", nargs="?",
                    help="flavour-arch-version, e.g. debian-64bit-12.2.0; or --all")
    ap.add_argument("--all", action="store_true", help="fetch every known target")
    ap.add_argument("-o", "--output-dir", default=os.path.join(ROOT, "isos"))
    ap.add_argument("--verify-only", action="store_true",
                    help="check what is already on disk; download nothing")
    a = ap.parse_args(argv[1:])

    src = load_sources()
    targets = src["targets"]
    if a.all:
        want = sorted(targets)
    elif a.target:
        if a.target not in targets:
            print(f"unknown target: {a.target}", file=sys.stderr)
            print(f"known: {', '.join(sorted(targets))}", file=sys.stderr)
            return 2
        want = [a.target]
    else:
        print("known targets:")
        for t in sorted(targets):
            p = os.path.join(a.output_dir, targets[t]["file"])
            print(f"  {t:<26} {'present' if os.path.isfile(p) else 'not downloaded'}")
        return 0

    made = _first_missing(a.output_dir)
    if made:
        MADE.append(made)
    os.makedirs(a.output_dir, exist_ok=True)
    rc = 0
    for t in want:
        spec = targets[t]
        dest = os.path.join(a.output_dir, spec["file"])
        why = verify(dest, spec["sha256"], spec["size"])
        if why is None:
            print(f"  ok       {spec['file']} (already verified)")
            continue
        if a.verify_only:
            print(f"  FAIL     {spec['file']}: {why}", file=sys.stderr)
            rc = 1
            continue
        if why != "missing":
            print(f"  re-fetch {spec['file']}: {why}", file=sys.stderr)

        # What this target's download writes, kept or not: a mirror that fails part-way
        # leaves the .part file.
        WROTE.extend([dest, dest + ".part"])
        got = False
        for m in src["mirrors"]:
            url = m["base"] + "/" + m["layout"].format(**spec)
            print(f"  fetching {spec['file']}  from {m['name']}")
            try:
                download(url, dest)
            except Exception as e:                      # noqa: BLE001
                print(f"  {m['name']} failed: {e}", file=sys.stderr)
                continue
            why = verify(dest, spec["sha256"], spec["size"])
            if why is None:
                print(f"  ok       {spec['file']} verified")
                got = True
                break
            print(f"  {m['name']} served a bad file: {why}", file=sys.stderr)
            os.unlink(dest)
        if not got:
            print(f"  FAIL     could not obtain a verified {spec['file']}", file=sys.stderr)
            rc = 1
    return rc


# What this run wrote as root, and the directory it created, for handback to give to the
# user who ran sudo (#79). Lists the entry point below reads, as apply.py's GIVE_BACK is.
# Only what this run wrote, never the whole output directory: -o can name ~/Downloads, or a
# home directory, and walking one would hand over every root-owned file in it.
WROTE: list = []
MADE: list = []

if __name__ == "__main__":
    import signal
    # TERM becomes an exit, as the trap in `kitchen` makes it, so the hand-back still runs:
    # Python runs no `finally` on a default SIGTERM, and sudo passes one on. Measured: a
    # download ended by TERM left isos/ and its .part file root's.
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    try:
        rc = main(sys.argv)
    finally:
        handback.give_back(WROTE, created=MADE)
    raise SystemExit(rc)
