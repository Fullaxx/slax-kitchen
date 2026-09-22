#!/usr/bin/env python3
"""lib/target.py refuses a name that does not exist, rather than splitting it.

WHY THIS EXISTS. Ten sites parsed or assembled `<flavour>-<arch>-<version>` and one asked
compat/sources.yaml whether the name was real. Two of the nine that did not were gates,
and both failed open (#36):

  - `ci/recipe-matrix.sh debain-64bit-12.2.0 <a real ISO>` declared all 35 recipes
    incompatible with a flavour that does not exist, skipped every one, printed
    "0 passed, 0 failed, 35 skipped in 2 s" and exited 0. The gate whose header says it
    exists to catch a recipe that silently works on only one target ran nothing and
    reported success.
  - `ci/tier-c.sh --target` was unchecked, and the ledger merge drops only rows whose
    target differs, so a misspelt one appended beside the real rows. The ledger gate
    typed `target` as str where `path`, `result` and `accel` have closed sets, so it
    passed and announced "5 target(s)"; ci/release-notes.sh then derived a published
    claim of Tier C on a target that does not exist, across 21 boots that were 20.

SHAPE IS NOT EXISTENCE is the whole point, so the refusal cases matter more than the
happy ones: `debain-64bit-12.2.0` splits into three perfectly plausible fields.

Every expectation is read from compat/sources.yaml rather than written here. A fifth
hardcoded copy of the four names is the defect, not the test.
"""
import os
import subprocess
import sys
import traceback

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "lib"))

import target  # noqa: E402

FAILURES = []


def check(name, got, want):
    if got != want:
        FAILURES.append(f"{name}: got {got!r}, want {want!r}")


def cli(*args):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "lib", "target.py"), *args],
                       capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def test_a_name_that_does_not_exist_is_refused_not_split():
    """The refusal names what does exist, because a typo is the case this is for."""
    try:
        spec = target.resolve("debain-64bit-12.2.0")
        check("a misspelt target is refused", f"returned {spec}", "SystemExit")
    except SystemExit as e:
        check("...and the message names it", "debain-64bit-12.2.0" in str(e), True)
        check("...and lists the ones that exist",
              all(n in str(e) for n in target.names()), True)

    # The same string through the CLI, which is what the shell callers reach.
    rc, out = cli("debain-64bit-12.2.0")
    check("the CLI refuses it too", rc != 0, True)
    check("...naming the known targets", "debian-64bit-12.2.0" in out, True)

    # `where` exists so the refusal says which input to fix.
    try:
        target.resolve("nope", "--target")
    except SystemExit as e:
        check("the caller's flag is named", str(e).startswith("--target: "), True)


def test_a_half_written_entry_is_refused_where_it_is_read():
    """resolve() checks the fields are there, so a caller gets a named refusal rather
    than a KeyError from wherever it happened to reach for `file` or `sha256`.

    sources.yaml is hand-curated -- it is the one file in compat/ that is not generated
    -- so a new target added with a field forgotten is the ordinary way this happens.
    """
    import shutil
    import tempfile
    d = tempfile.mkdtemp()
    real = target.SOURCES
    try:
        bad = os.path.join(d, "sources.yaml")
        with open(bad, "w") as fh:
            fh.write("targets:\n  debian-64bit-12.2.0:\n    file: x.iso\n    tree: T\n")
        target.SOURCES = bad
        try:
            target.resolve("debian-64bit-12.2.0")
            check("a target missing fields is refused", "returned", "SystemExit")
        except SystemExit as e:
            check("...and the refusal names which", "size" in str(e) and "sha256" in str(e), True)
    finally:
        target.SOURCES = real
        # ci/unit-run.py fails a test that leaves a fixture in its TMPDIR, and running
        # this file on its own does not check that -- only the gate does.
        shutil.rmtree(d, ignore_errors=True)

    # And the check is not fooled into firing on the real file.
    check("the committed sources.yaml is complete",
          [target.resolve(n)["target"] for n in target.names()], target.names())


def test_every_real_target_resolves_to_what_sources_yaml_says():
    """Read from the file, not copied here -- a copy is what #36 was about."""
    doc = target.load().get("targets") or {}
    check("sources.yaml still defines targets", bool(doc), True)
    for name, spec in doc.items():
        got = target.resolve(name)
        check(f"{name} carries its own name", got["target"], name)
        check(f"{name} fields come from sources.yaml",
              [got["file"], got["tree"], got["sha256"], got["size"]],
              [spec["file"], spec["tree"], spec["sha256"], spec["size"]])
        # The triple and the filename disagree about field order on purpose; the point is
        # that neither is rebuilt from the other any more.
        check(f"{name} splits into its three fields",
              f"{got['flavour']}-{got['arch']}-{got['version']}", name)
    check("names() is the same set, sorted", target.names(), sorted(doc))


def test_the_iso_filename_is_looked_up_never_reversed():
    """ci/gen-manifests.sh reversed the arch/flavour swap with a sed and accepted anything
    of that shape: slax-64bit-debian-999.9.9.iso became target debian-64bit-999.9.9 and
    four manifests were written under a name no release has."""
    doc = target.load().get("targets") or {}
    for name, spec in doc.items():
        check(f"{spec['file']} resolves back to {name}",
              target.from_iso_name(spec["file"])["target"], name)
        check("...from a full path too",
              target.from_iso_name(os.path.join("/srv/isos", spec["file"]))["target"], name)

    try:
        target.from_iso_name("slax-64bit-debian-999.9.9.iso")
        check("an invented release is refused", "returned", "SystemExit")
    except SystemExit as e:
        check("...and the message names the file", "999.9.9" in str(e), True)


def test_the_ledger_gate_holds_to_the_same_set():
    """The gate's closed set is handed in from here, so the two cannot drift.

    ci/tier-c-claim.py keeps its own literal copy -- deliberately, since it imports only
    json and sys -- and tests/unit/test_release.py exists to catch that one drifting.
    This checks the gate takes the list rather than growing a second literal beside it.
    """
    gate = open(os.path.join(ROOT, "ci", "checks", "97-tier-c-ledger.sh")).read()
    check("the gate asks lib/target.py for the set", "lib/target.py\" --list" in gate, True)
    check("...and holds target to it", 'r.get("target") not in TARGETS' in gate, True)
    for name in target.names():
        check(f"the gate does not hardcode {name}", name in gate, False)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        try:
            t()
        except Exception:                                   # noqa: BLE001
            FAILURES.append(f"{t.__name__} crashed:\n{traceback.format_exc()}")
    for f in FAILURES:
        print(f"FAIL {f}")
    name = os.path.basename(__file__)
    print(f"{name}: {'all checks passed' if not FAILURES else f'{len(FAILURES)} failed'}")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
