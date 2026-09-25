#!/usr/bin/env python3
"""`kitchen unpack --force` replaced the tree and kept the record of the one before it.

<dest>/.kitchen says what was applied to <dest>/iso, what that fetched and built, and what
pack is to master it with. `unpack --force` removed only the tree and rewrote only
origin.yaml, so journal.yaml, provenance.json and pack.yaml went on describing a tree that
was gone. Measured 2026-09-25, from slax-wine's review of the layering model (4646f15):

  kitchen status   listed a recipe whose bundle the fresh tree did not have
  kitchen apply    refused to apply it again, advising what had just been done
  kitchen pack     recorded it in the sidecar and mastered the old tree's hints -- a stale
                   `uefi: true` demanded the recipe `apply` refused, and a stale volid named
                   the image after a recipe it never had

A plain `unpack` did the same when iso/ had been deleted by hand.

xorriso is a stub whose `-extract / DIR` writes a one-file tree: CI's gates job does not
install the real one (tests/unit/test_diff.py), and nothing here is about extraction.

Run directly: python3 tests/unit/test_unpack.py
"""
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))

FAILURES = []

# What kitchen_unpack runs: `xorriso -osirrox on -indev ISO -extract / DIR`.
STUB_XORRISO = r'''#!/bin/sh
while [ $# -gt 0 ]; do
    if [ "$1" = -extract ]; then
        mkdir -p "$3/slax/boot" && echo stub > "$3/slax/boot/vmlinuz"
        exit 0
    fi
    shift
done
exit 0
'''

# Everything .kitchen ever holds: origin.yaml from lib/unpack.sh, journal.yaml and pack.yaml
# from lib/apply.py, provenance.json from lib/provenance.py.
RECORD = ("origin.yaml", "journal.yaml", "provenance.json", "pack.yaml")


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def in_a_box(fn):
    def run():
        tmp = tempfile.mkdtemp(prefix="unpack-")
        try:
            fn(tmp)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    run.__name__ = fn.__name__
    return run


def read(path):
    with open(path) as f:
        return f.read()


def seed(dest, files):
    for rel in files:
        path = os.path.join(dest, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("from the tree before\n")


def unpack(tmp, *args):
    """`sh kitchen unpack <a small file> ARGS`, with the stub xorriso first on PATH."""
    stub = os.path.join(tmp, "stub")
    os.makedirs(stub, exist_ok=True)
    with open(os.path.join(stub, "xorriso"), "w") as f:
        f.write(STUB_XORRISO)
    os.chmod(os.path.join(stub, "xorriso"), 0o755)
    iso = os.path.join(tmp, "base.iso")
    with open(iso, "wb") as f:
        f.write(b"stands in for an image\n")
    env = dict(os.environ, PATH=stub + os.pathsep + os.environ.get("PATH", ""), NO_COLOR="1")
    return subprocess.run(["sh", os.path.join(ROOT, "kitchen"), "unpack", iso] + list(args),
                          env=env, capture_output=True, text=True, timeout=60)


@in_a_box
def test_force_replaces_the_record_with_the_tree(tmp):
    """--force over an applied tree keeps nothing of it: a fresh origin.yaml and nothing else."""
    dest = os.path.join(tmp, "work")
    seed(dest, ["iso/slax/modules/40-var-demo.sb"] + [f".kitchen/{f}" for f in RECORD])
    p = unpack(tmp, "-o", dest, "--force")
    check("--force over an applied tree: exit", p.returncode, 0)
    check("...its record holds only origin.yaml",
          sorted(os.listdir(os.path.join(dest, ".kitchen"))), ["origin.yaml"])
    check("...written by this unpack",
          "from the tree before" in read(os.path.join(dest, ".kitchen", "origin.yaml")), False)
    check("...and the old tree is gone",
          os.path.exists(os.path.join(dest, "iso", "slax", "modules", "40-var-demo.sb")), False)


@in_a_box
def test_a_record_without_its_tree_is_refused(tmp):
    """No --force, and a .kitchen whose iso/ was deleted by hand: refused, nothing touched."""
    dest = os.path.join(tmp, "work")
    seed(dest, [".kitchen/journal.yaml"])
    p = unpack(tmp, "-o", dest)
    check("a record with no tree: refused", p.returncode, 1)
    check("...saying how to start over", "use --force to start over" in p.stderr, True)
    check("...the record untouched", os.listdir(os.path.join(dest, ".kitchen")), ["journal.yaml"])
    check("...and no tree unpacked beside it", os.path.exists(os.path.join(dest, "iso")), False)


def main():
    # One box for every fixture, removed afterwards: the convention of #25.
    box = tempfile.mkdtemp(prefix="test_unpack-")
    tempfile.tempdir = box
    _tmpdir = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = box
    try:
        for fn in [test_force_replaces_the_record_with_the_tree,
                   test_a_record_without_its_tree_is_refused]:
            # One test crashing must not stop the rest: the count of failures is only honest
            # if every test ran.
            try:
                fn()
            except Exception as e:                 # noqa: BLE001
                traceback.print_exc()
                FAILURES.append(f"{fn.__name__} crashed: {type(e).__name__}: {e}")
        if FAILURES:
            for f in FAILURES:
                print(f"FAIL {f}", file=sys.stderr)
            return 1
        print("tests/unit/test_unpack.py: all checks passed")
        return 0
    finally:
        tempfile.tempdir = None
        os.environ.pop("TMPDIR", None)
        if _tmpdir is not None:
            os.environ["TMPDIR"] = _tmpdir
        shutil.rmtree(box, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
