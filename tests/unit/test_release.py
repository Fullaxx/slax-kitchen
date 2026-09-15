#!/usr/bin/env python3
"""Unit tests for the release guard and the release notes.

A release workflow only runs when someone pushes a tag, which in this repo has
happened zero times. That makes it the least-exercised code here and the most
expensive to get wrong -- a bad Release object is visible to everyone and has to be
deleted by hand. So the parts that can be tested without pushing a tag, are.

The guard runs against throwaway repos rather than this one, so the cases stay true
after KITCHEN_VERSION is bumped.
"""
import os
import subprocess
import sys
import tempfile

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
GUARD = os.path.join(ROOT, "ci", "release-guard.sh")
NOTES = os.path.join(ROOT, "ci", "release-notes.sh")

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def check_in(name, needle, haystack):
    if needle not in haystack:
        FAILURES.append(f"{name}: {needle!r} not found in output")


def fake_repo(tmp, version):
    """A repo just real enough for the guard: a kitchen file and one commit."""
    with open(os.path.join(tmp, "kitchen"), "w") as fh:
        fh.write(f'#!/bin/sh\nKITCHEN_VERSION="{version}"\n')
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@e",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@e")
    for cmd in (["git", "init", "-q", "-b", "master"], ["git", "add", "kitchen"],
                ["git", "commit", "-qm", "x"]):
        subprocess.run(cmd, cwd=tmp, env=env, check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return tmp


def guard(version, tag, tag_push=False):
    """Exit code and combined output of the guard against a fake repo."""
    with tempfile.TemporaryDirectory() as tmp:
        fake_repo(tmp, version)
        argv = [GUARD, tag] + (["--tag-push"] if tag_push else [])
        p = subprocess.run(argv, env=dict(os.environ, REPO_ROOT=tmp),
                           capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr


def test_tag_must_match_version():
    """The invariant. A Release called v0.2.0 whose `kitchen version` says 0.1.0
    sends every bug report it generates at the wrong tree."""
    rc, out = guard("0.4.0", "v0.4.0")
    check("matching tag passes", rc, 0)
    rc, out = guard("0.4.0", "v0.5.0")
    check("mismatched tag fails", rc, 1)
    check_in("mismatch names both", "expected tag v0.4.0", out)
    # The v prefix is part of the contract, not decoration.
    rc, _ = guard("0.4.0", "0.4.0")
    check("bare version is not the tag", rc, 1)


def test_dev_suffix_only_blocks_a_tag_push():
    """The whole point of the split: the release path has to be rehearsable while
    the tree is still -dev, or it is first exercised on the day it matters."""
    rc, out = guard("0.4.0-dev", "v0.4.0-dev")
    check("dry run tolerates -dev", rc, 0)
    check_in("and says why", "not a tag push", out)

    rc, out = guard("0.4.0-dev", "v0.4.0-dev", tag_push=True)
    check("a real tag push rejects -dev", rc, 1)
    check_in("and says what to do", "drop the -dev suffix", out)

    rc, _ = guard("0.4.0", "v0.4.0", tag_push=True)
    check("a released version tag-pushes fine", rc, 0)


def test_guard_rejects_bad_usage():
    p = subprocess.run([GUARD], capture_output=True, text=True)
    check("no argument is a usage error, not a pass", p.returncode, 2)


def notes(tag, env=None):
    p = subprocess.run([NOTES, tag], cwd=ROOT, capture_output=True, text=True,
                       env=dict(os.environ, **(env or {})))
    if p.returncode != 0:
        FAILURES.append(f"release-notes.sh exited {p.returncode}: {p.stderr}")
    return p.stdout


def test_notes_say_what_was_not_done():
    """The notes are a verification claim. The claims that matter most are the
    negative ones -- what did NOT run -- because those are the ones a reader would
    otherwise assume."""
    out = notes("v9.9.9")
    for section in ("## Changes", "## What was verified", "## Provenance",
                    "## Redistribution"):
        check_in("section present", section, out)
    check_in("names the rung not reached", "Tier C was not run", out)
    check_in("says why", "/dev/kvm", out)
    check_in("distinguishes the three unbooted targets",
             "matrix-verified, not boot-verified", out)
    check_in("no ISO, and it is on purpose", "deliberate rather than an oversight", out)
    check_in("source-offer obligation named", "source-offer obligation", out)
    check_in("credits upstream", "Tomáš Matějíček", out)


def test_notes_publish_only_verifiable_hashes():
    """Base ISO hashes are checkable by anyone; a built-ISO hash is not, because the
    ISO container is not byte-reproducible. Publishing the second would be exactly
    the false assurance the ladder exists to prevent."""
    out = notes("v9.9.9")
    with open(os.path.join(ROOT, "compat", "sources.yaml")) as fh:
        wanted = [ln.split(":", 1)[1].strip() for ln in fh if ln.strip().startswith("sha256:")]
    check("four base ISOs pinned in sources.yaml", len(wanted), 4)
    for h in wanted:
        check_in("base ISO hash carried into the notes", h, out)
    check_in("and says why there is no ISO checksum", "not byte-reproducible", out)


def test_notes_never_truncate_silently():
    """A changelog cut at the cap with no marker is quiet data loss."""
    capped = notes("v9.9.9", env={"LOG_CAP": "5"})
    check_in("truncation is announced", "more. Full list:", capped)
    # Count inside ## Changes only: the base-ISO list further down is also bullets,
    # which is how the first version of this assertion read 9 instead of 5.
    changes = capped.split("## Changes", 1)[1].split("## What was verified", 1)[0]
    check("exactly LOG_CAP entries kept", changes.count("\n- "), 5)

    full = notes("v9.9.9")
    if "more. Full list:" in full:
        FAILURES.append("uncapped run claimed truncation")


def test_notes_links_are_absolute():
    """A relative link in a Release body resolves against .../releases/, which
    happens to work. 'Happens to work' is not verifiable without publishing one."""
    out = notes("v9.9.9", env={"GITHUB_REPOSITORY": "someone/elsewhere"})
    check_in("honours GITHUB_REPOSITORY",
             "https://github.com/someone/elsewhere/blob/v9.9.9/NOTICE.md", out)
    if "](../blob/" in out:
        FAILURES.append("a relative blob link survived")


for fn in list(globals().values()):
    if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
        fn()

if FAILURES:
    print(f"{len(FAILURES)} failure(s):", file=sys.stderr)
    for f in FAILURES:
        print(f"  {f}", file=sys.stderr)
    sys.exit(1)
print("release: all checks passed")
